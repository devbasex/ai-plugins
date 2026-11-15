"""承認の証跡の後に、本番の PR（本番ブランチ宛ての PR）を、取り込み方の行とマージの方式に合った経路で入れる（本番のプランと workflow が打つ）。

1. 本番ブランチ宛ての PR は、証跡を読み直して今の head が承認されていることを確かめる（commit status も付け直す）。
   書く token が記録の読み手に信用されなければ、本番ブランチへ入れる前に止まる（マージの後のリリース記録が必ず落ちる。#3487）
2. マージの方式（`.ndf/project.json` の `production_merge.method`）を本番ブランチの ruleset と照らす（`merge_method.decide`。
   #3572 の決定 17）。食い違えば投入もマージもせずに `precondition` で止まる（I15）
3. 宛先のブランチの規則と従来の保護の必須のチェックが PR の head で通るまで待つ。落ちたら打ち直さずに止まった理由で返す（#1983）
4. 待ちの後にもう一度証跡を読み直し、待つ前と同じ head への最新の答えが承認のときだけ進む
5. 方式ごとに入れる。どれも承認した head を添えて打ち、承認の後に head が動いていれば GitHub が断る

| 方式 | 入れ方 |
| --- | --- |
| `merge-queue` | 承認した head を `expectedHeadOid` に渡してキューへ入れ、投入の記録を残し、マージかキューから外れるまで待つ。外れたら `queue_removed`（同じ head で入れ直さない。決定 18・I16） |
| `strict` | head が本番ブランチの先端を含むときだけマージする。含まなければ `behind`（決定 19） |
| 無し（`github-flow` でない行だけ） | merge commit でマージする（基底がキューを持てばキューへ入れて待つ）。迂回の宣言があれば迂回する（#3506） |

取り込み方の行が `github-flow` でなく、ベースブランチが本番ブランチと違えば、本番ブランチへ入れた後に同期の PR（本番ブランチから
ベースブランチ宛て）でベースブランチへ戻す。`github-flow` の行では同期の PR を作らない（決定 10・I10）。

承認の後はコードを直さない（#3572 の I4）。必須のチェックが落ちたら、落ちたチェック（`Landed.failed`）を結果に載せて
止まり、push もマージもしない。本番ブランチへ入れたら（マージ済みからの再開を含む）、承認（`merge_decide.Approval`。
承認ゲートの承認か、人の GitHub でのマージか）を結果に載せ、人のマージなら人のマージの記録を 1 回だけ書く（決定 8）。

迂回の宣言（`admin`。`.ndf/project.json` の `production_merge.admin`）があり、マージの方式が無く、本番ブランチの規則の承認数が 1 以上と
読めるときだけ、本番ブランチ宛ての PR を管理者の迂回でマージし（`GitHub.merge_as_admin`）、迂回の記録を置く（#3506 の I2b・I7）。
マージの方式があるときは、本番の PR もバージョン上げの PR も迂回しない（I17。迂回は組み合わせの確かめも越える）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import NamedTuple

from ndf.adapters import ci
from ndf.adapters.github import MERGE_COMMIT, MERGE_QUEUE_PENDING, GitHub, GitHubError, MergeOutcome, Pull
from ndf.domains.release import merge_decide, merge_method, records_trail, release_plan, required_checks, trail
from ndf.domains.release.declaration import GITHUB_FLOW
from ndf.domains.release.required_checks import NOT_FOUND, RequiredChecks, from_sources

# 本番の PR と版上げの PR のマージの方式。版上げのコミットの起点と範囲（版上げの PR の第 2 親）を保つため merge commit に限る
# （squash・rebase では範囲の PR が読めなくなる）。許されていなければ GitHub へ送らずに止まる
MERGE_METHOD = MERGE_COMMIT
# GitHub が PR の作成を断る状態と、そのうち差分が無いことを表す文言（ベースブランチへ戻す差分が無い）
UNPROCESSABLE = HTTPStatus.UNPROCESSABLE_ENTITY
NOTHING_TO_SYNC_TEXT = "No commits between"
# マージキューへ入れた PR がマージされたかを読み直す間隔（秒）
QUEUE_POLL_SECONDS = 30.0


class Landed(NamedTuple):
    """マージの結果。止まったら `kind` に止まった理由の種類。"""

    merged: bool
    kind: str | None
    detail: str
    # 本番ブランチへ入ったコミット（タグと GitHub Release を作る版。本番ブランチへ入れていなければ None）
    production_sha: str | None = None
    # 本番の PR を管理者の迂回でマージしたか・迂回の記録の URL・記録を書けなかった理由（#3506）
    admin: bool = False
    admin_record: str | None = None
    record_error: str | None = None
    # 承認（経路・承認した人・先端）・止まったときの落ちたチェック・人のマージの記録の URL（#3572）
    approval: merge_decide.Approval | None = None
    failed: tuple[ci.Check, ...] = ()
    merge_record: str | None = None
    # 同期の PR（`github-flow` でない行）・使ったマージの方式・方式の食い違い（設定の値, ruleset の値）
    sync_pr: int | None = None
    method: str | None = None
    mismatch: tuple[str, str] | None = None


def _failed_listing(failed: tuple[ci.Check, ...]) -> str:
    """止まった理由の文に添える落ちたチェックの並び（無ければ空）。"""
    names = "、".join(c.name for c in failed)
    return f"。落ちたチェック: {names}" if names else ""


def _sha_or_none(sha: str | None) -> str | None:
    """マージコミットの SHA（空なら None）。"""
    return sha if sha else None


def _judged_refusal(pull: Pull, got: trail.Read, moved: str, unapproved: str) -> Landed | None:
    """読み直した証跡 `got` の先端が `pull` から動いていれば `moved`、承認されていなければ `unapproved` で止まった結果
    （どちらでもなければ None）。"""
    if got.pull.head_sha != pull.head_sha:
        return Landed(False, "refused", moved)
    if not got.judgment.approved:
        return Landed(False, "refused", unapproved)
    return None


def _dequeued(number: int) -> Landed:
    """マージされずにマージキューから外された PR `number` の止まった結果（決定 18・I16）。"""
    return Landed(
        False,
        "queue_removed",
        f"#{number} はマージされずにマージキューから外れた（キューの一時のコミットの必須のチェックか衝突）。"
        "同じ head では入れ直さない。直しを入れた head で承認を取り直す",
    )


def _queue_timeout(number: int) -> Landed:
    """マージキューへ入れた PR `number` が待ちの内にマージされなかった止まった結果。"""
    return Landed(
        False,
        MERGE_QUEUE_PENDING,
        f"#{number} はマージキューに入れたが、待ちの内にマージされない。"
        "キューがマージした後に `ndf release-steps merge` を打ち直す（承認の記録へ進む）",
    )


@dataclass(frozen=True)
class Lander:
    """1 つのリポジトリのリリースの PR をマージする。`wait` は必須のチェックの待ちを組む関数（テストが差し替える）。"""

    hub: GitHub
    production: str
    budget: float
    wait: Callable[[Callable[[], ci.Reading], float], ci.WatchResult] | None = None
    # 迂回の宣言（`declaration.production_admin`）。偽なら今のマージのまま
    admin: bool = False
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    # 取り込み方の行（`declaration.flow_of`。None は既定の行）・マージの方式（`declaration.merge_method_of`）・
    # ベースブランチ（`declaration.base_branch`。None なら同期の PR を作らない）
    flow: str | None = None
    method: str | None = None
    base: str | None = None

    @property
    def _syncs(self) -> bool:
        """本番ブランチへ入れた後に同期の PR でベースブランチへ戻すか（`github-flow` でなく、ベースブランチが本番ブランチと違う）。"""
        return self.flow != GITHUB_FLOW and self.base is not None and self.base != self.production

    def _is_production(self, pull: Pull) -> bool:
        """`pull` が本番ブランチ宛ての本番の PR か（判定の正本は `release_plan.is_production_pr`）。"""
        return release_plan.is_production_pr(pull.base_ref, self.production)

    def approvals(self, branch: str) -> int | None:
        """宛先のブランチの承認数。ruleset の承認の規則があればその最大、無ければ従来の保護、保護が無ければ 0、読めなければ None。"""
        try:
            rules = self.hub.branch_rules(branch)
        except GitHubError:  # noqa: NDF401 ネットワーク: 承認数を読めなければ迂回しない（#3506 の I2b）
            return None
        found = [
            merge_method.review_count(rule["parameters"])
            for rule in rules
            if rule.get("type") == merge_method.REVIEW_RULE and isinstance(rule.get("parameters"), dict)
        ]
        if found:
            return max(found)
        try:
            got = self.hub.rest("GET", f"/repos/{self.hub.repo}/branches/{branch}/protection/required_pull_request_reviews")
        except GitHubError as exc:
            return 0 if exc.status == NOT_FOUND else None
        return merge_method.review_count(got) if isinstance(got, dict) else None

    def _bypass(self, pull: Pull) -> bool:
        """この PR を管理者の迂回でマージするか（宣言があり、マージの方式が無く、本番ブランチ宛てで、承認数が 1 以上と読める。
        #3506 の I2b。方式があれば迂回しない。#3572 の I17）。"""
        if not self.admin or self.method is not None or not self._is_production(pull):
            return False
        n = self.approvals(pull.base_ref)
        return n is not None and n >= 1

    @property
    def _checks(self) -> RequiredChecks:
        """必須のチェックの解決とチェックの実行の読み（`required_checks.RequiredChecks`。状態を持たない）。"""
        return RequiredChecks(self.hub)

    def required(self, branch: str) -> required_checks.Required:
        """宛先のブランチの規則（ruleset。全ページ）と従来の保護の必須のチェック（承認の commit status を除く）。"""
        return self._checks.required(branch)

    def _watch(self, pull: Pull, deadline: float) -> tuple[str | None, tuple[ci.Check, ...]]:
        """必須のチェック（check run と commit status）を時刻 `deadline` まで待ち、止まった理由の種類（通れば None）と
        最後の読みの落ちたチェックを返す。"""
        checks = self._checks
        required = checks.required(pull.base_ref)
        bots: dict[str, int | None] = {}

        def reader() -> ci.Reading:
            return ci.read(from_sources(checks.runs(pull.head_sha, required.apps, bots), required.apps), required.names, [])

        left = max(0.0, deadline - self.clock())
        watched = self.wait(reader, left) if self.wait is not None else ci.Watch(reader, left).run()
        kind = watched.cause([])
        if kind is None:
            return None, ()
        return kind, (*watched.reading.failed_tests, *watched.reading.failed_other)

    def _refusal(self, pull: Pull, human_only: bool = False) -> Landed | None:
        """本番ブランチ宛ての PR の証跡を読み直し、`pull` の先端が承認されていなければ止まった結果（承認されていれば None）。
        `human_only` なら担い手が MVV 判定の証跡を数えない（迂回の前提。#3506 の I9）。"""
        if not self._is_production(pull):
            return None
        got = trail.check(self.hub, pull.number, human_only)
        moved = f"#{pull.number} の先端が {pull.head_sha[:12]} から {got.pull.head_sha[:12]} へ動いた"
        return _judged_refusal(pull, got, moved, f"#{pull.number} は承認されていない: {got.judgment.detail}")

    def _blocker(self, pull: Pull, deadline: float, bypass: bool = False) -> Landed | None:
        """マージの前の停止条件を順に確かめ、止まった結果（進めてよければ None）。

        証跡の読み直し → 必須のチェックの待ち（時刻 `deadline` まで）→ 待ちの後の証跡の読み直し（本番ブランチ宛てでなければ
        読み直しは素通り）。
        """
        refused = self._refusal(pull, bypass)
        if refused is not None:
            return refused
        kind, failed = self._watch(pull, deadline)
        if kind is not None:
            detail = f"#{pull.number} の必須のチェックが通らない（{kind}。コードを直さずに止まる{_failed_listing(failed)}）"
            return Landed(False, kind, detail, failed=failed)
        return self._refusal(pull, bypass)

    def _merge(self, pull: Pull, deadline: float, bypass: bool = False) -> Landed:
        """停止条件（`_blocker`）を確かめた後にマージする。待ちは時刻 `deadline`（`land` が 1 度だけ決める）まで。
        `bypass` なら管理者の迂回で打つ。迂回でなくマージキューへ入れたら、マージされるまで待つ。"""
        blocked = self._blocker(pull, deadline, bypass)
        if blocked is not None:
            return blocked
        if bypass:
            outcome: MergeOutcome = self.hub.merge_as_admin(pull.number, expect_head=pull.head_sha, method=MERGE_METHOD)
            return Landed(outcome.merged, outcome.kind, outcome.detail)  # 迂回の印は _record_admin が立てる
        outcome = self.hub.merge(pull.number, expect_head=pull.head_sha, method=MERGE_METHOD, require_method=True)
        if outcome.kind == MERGE_QUEUE_PENDING:
            return self._await_queue(pull, deadline)
        return Landed(outcome.merged, outcome.kind, outcome.detail)

    def _method_of(self, pull: Pull) -> tuple[str | None, Landed | None]:
        """マージの方式を本番ブランチの ruleset と照らし、(方式, None) か (None, 止まった結果) を返す（I15）。

        `github-flow` でない行で方式の宣言が無ければ、ruleset を読まずに方式なし（今のマージ）。"""
        if self.method is None and self.flow != GITHUB_FLOW:
            return None, None
        try:
            facts = merge_method.rules_of(self.hub.branch_rules(pull.base_ref), self.hub.merge_queue(pull.base_ref))
            review = self.hub.review_decision(pull.number) if self.method is not None and facts.review_count >= 1 else None
        except GitHubError as exc:
            return None, Landed(False, "precondition", f"#{pull.number} の宛先 {pull.base_ref} の ruleset を読めない: {exc}")
        chosen = merge_method.decide(self.flow, self.method, facts, review, GITHUB_FLOW)
        if chosen.problem is None:
            return chosen.value, None
        declared = chosen.declared if chosen.declared is not None else "（無し）"
        detail = (
            f"#{pull.number} を入れない: {chosen.problem}（設定 production_merge.method={declared}・ruleset: {chosen.actual}）。"
            "設定か ruleset を直して打ち直す"
        )
        return None, Landed(False, "precondition", detail, mismatch=(declared, chosen.actual))

    def _behind(self, pull: Pull) -> Landed | None:
        """head が本番ブランチの先端を祖先に持たなければ止まった結果（`behind`）。比べを読めなければ `precondition`。"""
        try:
            n = self.hub.behind_by(self.production, pull.head_sha)
        except GitHubError as exc:
            return Landed(False, "precondition", f"#{pull.number} の head と {self.production} を比べられない: {exc}")
        if n == 0:
            return None
        return Landed(
            False,
            "behind",
            f"#{pull.number} の head が {self.production} の先端より {n} コミット遅れている。{self.production} を取り込んで承認を取り直す",
        )

    def _land_queue(self, pull: Pull, deadline: float) -> Landed:
        """承認した head でキューへ入れ、投入の記録を残し、マージかキューから外れるのを待つ（決定 18・I16。迂回しない）。"""
        number = pull.number
        try:
            queued = records_trail.queued_heads(self.hub, number)
        except trail.TrailError as exc:
            return Landed(False, "unknown", f"#{number} のキューへの投入の記録を読めない: {exc}")
        if pull.head_sha in queued:
            # 打ち直し: 入れた head の記録がある。キューの中なら待ちを続け、無ければ外れたとして入れ直さない
            return self._await_queue(pull, deadline) if self.hub.queue_entry(number).in_queue else _dequeued(number)
        blocked = self._blocker(pull, deadline)
        if blocked is not None:
            return blocked
        outcome = self.hub.enqueue(number, pull.head_sha)
        if outcome.merged:
            return Landed(True, None, outcome.detail)
        if outcome.kind != MERGE_QUEUE_PENDING:
            return Landed(False, outcome.kind, outcome.detail)
        try:
            records_trail.write_queue_record(self.hub, number, pull.head_sha)
        except trail.TrailError as exc:
            self.hub.dequeue(number)
            return Landed(False, "unknown", f"#{number} のキューへの投入の記録を書けない: {exc}。キューから外した")
        return self._await_queue(pull, deadline)

    def _land_strict(self, pull: Pull, deadline: float) -> Landed:
        """head が本番ブランチの先端を含むかを確かめ、停止条件の後に承認した head でマージする（決定 19。迂回しない）。"""
        behind = self._behind(pull)
        if behind is not None:
            return behind
        blocked = self._blocker(pull, deadline)
        if blocked is not None:
            return blocked
        outcome = self.hub.merge(pull.number, expect_head=pull.head_sha, method=MERGE_METHOD, require_method=True)
        if outcome.merged:
            return Landed(True, None, outcome.detail)
        again = self._behind(pull)
        return again if again is not None else Landed(False, outcome.kind, outcome.detail)

    def _open_sync(self) -> int | None:
        """本番ブランチからベースブランチへ戻す PR の番号（開いたものがあれば使い直し、無ければ作る。差分が無ければ None）。"""
        owner = self.hub.repo.partition("/")[0]
        found = self.hub.rest(
            "GET",
            f"/repos/{self.hub.repo}/pulls",
            params={"state": "open", "head": f"{owner}:{self.production}", "base": self.base},
        )
        if isinstance(found, list) and found:
            return int(found[0]["number"])
        try:
            made = self.hub.rest(
                "POST",
                f"/repos/{self.hub.repo}/pulls",
                {"title": f"Sync: {self.production} を {self.base} へ戻す", "head": self.production, "base": self.base, "body": ""},
            )
        except GitHubError as exc:
            if exc.status != UNPROCESSABLE or NOTHING_TO_SYNC_TEXT not in str(exc):
                raise
            return None
        return int(made["number"])

    def _sync(self, done: Landed, deadline: float) -> Landed:
        """本番ブランチへ入れた結果 `done` に、ベースブランチへ戻す同期の PR のマージを足す（`github-flow` の行では呼ばない。I10）。"""
        number = self._open_sync()
        if number is None:
            return done._replace(detail=f"{done.detail}（{self.base} へ戻す差分は無い）")
        synced = self._merge(self.hub.pull(number), deadline)
        detail = f"{done.detail}。同期の PR #{number}: {synced.detail}"
        return done._replace(merged=synced.merged, kind=synced.kind, failed=synced.failed, sync_pr=number, detail=detail)

    def _record_admin(self, pull: Pull, done: Landed) -> Landed:
        """迂回でマージした結果に、迂回の記録の URL か、書けなかった理由を足す（#3506 の I7・決定 7）。"""
        try:
            _wrote, url = records_trail.write_admin_record(self.hub, pull.number, pull.head_sha)
        except trail.TrailError as exc:
            return done._replace(admin=True, record_error=str(exc))
        return done._replace(admin=True, admin_record=url)

    def _queued_refusal(self, pull: Pull, now: Pull) -> Landed | None:
        """キューで待つ本番ブランチ宛ての PR の証跡を読み直し、承認が失効していれば止まった結果（承認のままなら None）。

        `now` はこのループの 1 回で読んだ PR で、読み直さずに判定へ渡す（ループの 1 回ごとの照会を 1 回に保つ）。
        待ちの間は commit status を付け直さない（読み直しのたびに書かない）。
        """
        if not self._is_production(pull):
            return None
        got = trail.judged(self.hub, now)
        moved = f"#{pull.number} の先端がキューの待ちの間に {got.pull.head_sha[:12]} へ動いた"
        return _judged_refusal(pull, got, moved, f"#{pull.number} の承認がキューの待ちの間に失効した: {got.judgment.detail}")

    def _await_queue(self, pull: Pull, deadline: float, judge: bool = True) -> Landed:
        """マージキューへ入れた PR `pull` がマージされるまで、時刻 `deadline`（`clock` の値）まで読み直す。

        読み直すたびに、キューに残っているかと（本番ブランチ宛てで `judge` なら）承認を確かめる。版上げの PR は承認の
        証跡を持たないため、`land_bump` は `judge` を偽にして呼ぶ。キューが外したら `queue_removed` で
        すぐに止まり、承認が失効したらキューから外して `refused` で止まる（前の承認のままキューがマージしない）。
        予算の内にマージされなければ `ci_pending` で止まり、キューがマージした後の打ち直しを促す（`land` はマージ済みの
        本番ブランチ宛ての PR から、承認の記録へ再開する）。
        """
        number = pull.number
        queued_merged = Landed(True, None, f"#{number} をマージキューがマージした")
        while True:
            now = self.hub.pull(number)
            if now.merged:
                return queued_merged
            refused = self._queued_refusal(pull, now) if judge else None
            if refused is not None:
                self.hub.dequeue(number)
                return refused._replace(detail=f"{refused.detail}。マージキューから外した")
            if not self.hub.in_merge_queue(number):
                # 読み直しの間にキューがマージしていれば、外されたのではない
                return queued_merged if self.hub.pull(number).merged else _dequeued(number)
            left = deadline - self.clock()
            if left <= 0:
                return _queue_timeout(number)
            self.sleep(min(QUEUE_POLL_SECONDS, left))

    def _approval(self, number: int) -> merge_decide.Approval | None:
        """マージ済みの PR を読み直し、承認を決める（マージされていなければ None）。"""
        return merge_decide.decide(trail.merge_facts(self.hub, number), trail.approvals(self.hub, number))

    def _record_merge(self, number: int, done: Landed, approval: merge_decide.Approval) -> Landed:
        """承認を結果に足し、経路が人のマージなら人のマージの記録を書く（書けなければ理由を `record_error` へ）。"""
        done = done._replace(approval=approval)
        if approval.route != merge_decide.MERGE_ROUTE:
            return done
        try:
            _wrote, url = records_trail.write_merge_record(self.hub, number, approval)
        except trail.TrailError as exc:
            return done._replace(record_error=str(exc))
        return done._replace(merge_record=url)

    def _released(self, pull: Pull, done: Landed) -> Landed:
        """本番ブランチへ入れた結果に、マージコミットと承認（人のマージなら記録も）を足す。版は CD が決める（決定 7）。"""
        merged_sha = self.hub.pull(pull.number).merge_sha
        done = done._replace(production_sha=_sha_or_none(merged_sha))
        approval = self._approval(pull.number)
        return self._record_merge(pull.number, done, approval) if approval is not None else done

    def _resume_merged(self, number: int, pull: Pull, deadline: float) -> Landed:
        """マージ済みの PR からの再開。本番ブランチ宛てなら承認を決めて（人のマージなら記録を書き）、
        迂回の記録が無ければ補い、`github-flow` でない行では同期の PR へ進む。"""
        if not self._is_production(pull):
            return Landed(True, None, f"#{number} は既にマージされている")
        released = self._released(pull, Landed(True, None, f"#{number} は既に {self.production} へマージされている"))
        # 再開: 利用者の証跡がマージした head への承認なら、迂回でマージしたとして記録を補う（I7）
        if self._bypass(pull) and (judged := trail.user_judgment_at(self.hub, number, pull.head_sha)) is not None and judged.approved:
            released = self._record_admin(pull, released)
        return self._sync(released, deadline) if self._syncs else released

    def land(self, number: int) -> Landed:
        """本番の PR を行とマージの方式に合った経路で入れ、承認を結果に載せる。head の名前を問わない。

        待ちの予算（`budget`）はエントリポイントで 1 つの締め切りにし、チェックの待ち・マージキューの待ち・同期の PR で分け合う。
        """
        deadline = self.clock() + self.budget
        pull = self.hub.pull(number)
        if pull.merged:
            return self._resume_merged(number, pull, deadline)
        if self._is_production(pull) and not self.hub.token_trusted():
            return Landed(
                False,
                "refused",
                f"#{number} を {self.production} へ入れる token の書き手が write 以上の権限を持つ利用者でなく、マージの後の"
                "リリース記録が読まれない。利用者の token（workflow では secrets.NDF_RELEASE_TOKEN）でマージする",
            )
        if not self._is_production(pull):
            return self._merge(pull, deadline)
        method, stopped = self._method_of(pull)
        if stopped is not None:
            return stopped
        bypass = False
        if method == merge_method.QUEUE:
            done = self._land_queue(pull, deadline)
        elif method == merge_method.STRICT:
            done = self._land_strict(pull, deadline)
        else:
            bypass = self._bypass(pull)
            done = self._merge(pull, deadline, bypass)
        done = done._replace(method=method)
        if not done.merged:
            return done
        released = self._released(pull, done)
        released = self._record_admin(pull, released) if bypass else released
        return self._sync(released, deadline) if self._syncs else released

    def land_bump(self, number: int, allowed: Callable[[Pull], list[str]]) -> Landed:
        """バージョン上げの PR を、承認の証跡を読まずに、差分の判定と必須のチェックの後に head との照合付きで入れる（決定 7・20）。

        `allowed` はバージョンの置き場と CHANGELOG の外に及ぶ差分のパスを返す（空ならバージョン上げだけ）。マージの方式があれば
        本番の PR と同じく ruleset と照らし、迂回せずに方式で入れる（`merge-queue` はキューの先頭へ、`strict` は先端を含む head を
        マージ）。方式が無ければ、宛先の承認数が 0 なら普通のマージ、1 以上なら迂回でマージする。
        """
        deadline = self.clock() + self.budget
        pull = self.hub.pull(number)
        if pull.merged:
            return Landed(True, None, f"#{number} は既にマージされている", production_sha=_sha_or_none(pull.merge_sha))
        outside = allowed(pull)
        if outside:
            return Landed(False, "refused", f"#{number} の差分がバージョンの置き場と CHANGELOG の外に及ぶ: {', '.join(outside)}")
        method, stopped = self._method_of(pull) if self._is_production(pull) else (None, None)
        if stopped is not None:
            return stopped
        if method is not None:
            return self._bump_by_method(pull, method, deadline)
        return self._bump_plain(pull, deadline)

    def _bump_plain(self, pull: Pull, deadline: float) -> Landed:
        """マージの方式が無いとき、承認数 0 なら普通のマージ、1 以上なら迂回でバージョン上げの PR をマージする。"""
        number = pull.number
        plain = self._is_production(pull) and self.approvals(pull.base_ref) == 0
        if not plain and not self._bypass(pull):
            return Landed(
                False,
                "precondition",
                f"#{number} を迂回でマージできない（宣言 production_merge.admin が無いか、{pull.base_ref} の承認数を読めない）。"
                "ruleset を変えずに済む手段が無い（要求の未決 3）ため人へ戻す",
            )
        kind, failed = self._watch(pull, deadline)
        if kind is not None:
            return Landed(False, kind, f"#{number} の必須のチェックが通らない（{kind}{_failed_listing(failed)}）", failed=failed)
        if plain:
            outcome = self.hub.merge(number, expect_head=pull.head_sha, method=MERGE_METHOD, require_method=True)
            if outcome.kind == MERGE_QUEUE_PENDING:
                # main がマージキューを使うとき、普通のマージはキューへ入れる。マージされるまで残りの締め切りまで待つ
                queued = self._await_queue(pull, deadline, judge=False)
                if not queued.merged:
                    return queued
                outcome = outcome._replace(merged=True, kind=None, detail=queued.detail)
        else:
            outcome = self.hub.merge_as_admin(number, expect_head=pull.head_sha, method=MERGE_METHOD)
        if not outcome.merged:
            return Landed(False, outcome.kind, outcome.detail)
        merged_sha = self.hub.pull(number).merge_sha
        how = "マージした" if plain else "迂回でマージした"
        return Landed(True, None, f"#{number} を{how}", production_sha=_sha_or_none(merged_sha), admin=not plain)

    def _bump_by_method(self, pull: Pull, method: str, deadline: float) -> Landed:
        """バージョン上げの PR を、head が本番ブランチの先端を含むときだけ方式で入れる（迂回しない。I17）。"""
        number = pull.number
        behind = self._behind(pull)
        if behind is not None:
            return behind
        kind, failed = self._watch(pull, deadline)
        if kind is not None:
            return Landed(False, kind, f"#{number} の必須のチェックが通らない（{kind}{_failed_listing(failed)}）", failed=failed)
        if method == merge_method.QUEUE:
            # キューの先頭へ入れ、キューで前に並んだ PR がバージョン上げのマージコミットの祖先に入らないようにする
            outcome = self.hub.enqueue(number, pull.head_sha, jump=True)
            if not outcome.merged and outcome.kind != MERGE_QUEUE_PENDING:
                return Landed(False, outcome.kind, outcome.detail, method=method)
            if not outcome.merged:
                queued = self._await_queue(pull, deadline, judge=False)
                if not queued.merged:
                    return queued._replace(method=method)
        else:
            outcome = self.hub.merge(number, expect_head=pull.head_sha, method=MERGE_METHOD, require_method=True)
            if not outcome.merged:
                again = self._behind(pull)
                return (again if again is not None else Landed(False, outcome.kind, outcome.detail))._replace(method=method)
        merged_sha = self.hub.pull(number).merge_sha
        return Landed(True, None, f"#{number} を {method} でマージした", production_sha=_sha_or_none(merged_sha), method=method)
