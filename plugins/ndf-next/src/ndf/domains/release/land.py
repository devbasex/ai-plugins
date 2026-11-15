"""承認の証跡の後に、本番の PR（main 宛てのスプリントの PR）をマージする（本番のプランと workflow が打つ）。

1. 本番ブランチ宛ての PR は、証跡を読み直して今の先端が承認されていることを確かめる（commit status も付け直す）。
   書く token が記録の読み手に信用されなければ、本番ブランチへ入れる前に止まる（マージの後のリリース記録が必ず落ちる。#3487）
2. 宛先のブランチの規則（ruleset）と従来の保護の必須のチェック（check run と commit status）が PR の先端で通るまで待つ。落ちたら打ち直さずに止まった理由で
   返す（同じ CI の失敗を読み直し続けない。#1983）
3. 本番ブランチ宛ての PR は、待ちの後にもう一度証跡を読み直し、待つ前と同じ先端への最新の答えが承認のときだけ進む
   （待つ間に足された差し戻しや消された承認を見落とさない）
4. 先端の SHA を添えて merge commit でマージする（承認の後に先端が動いていれば GitHub が断る）。マージキューへ入れたら、
   予算の範囲で PR がマージされるまで待つ（待たずに返すと、リリース記録が走らない）。待つ間も
   キューに残っているかと承認を読み直し、キューが外したらすぐに止まり、承認が失効したらキューから外して止まる

本番ブランチへ入れた後に、次の版のブランチも、ベースブランチへ戻す同期の PR も作らない（#3572 の I10）。版数とタグは CD が
マージの後に版上げの PR で作る（決定 7）。版上げの PR は `land_bump` が、承認の証跡を読まずに、差分の判定と必須のチェックの
後に照合付きで迂回のマージを打つ。

承認の後はコードを直さない（#3572 の I4）。必須のチェックが落ちたら、落ちたチェック（`Landed.failed`）を結果に載せて
止まり、push もマージもしない。本番ブランチへ入れたら（マージ済みからの再開を含む）、承認（`merge_decide.Approval`。
承認ゲートの承認か、人の GitHub でのマージか）を結果に載せ、人のマージなら人のマージの記録を 1 回だけ書く（決定 8）。

迂回の宣言（`admin`。`.ndf/project.json` の `production_merge.admin`）があり、本番ブランチの規則の承認数が 1 以上と読めるときだけ、
本番ブランチ宛ての PR を管理者の迂回でマージし（`GitHub.merge_as_admin`）、迂回の記録を置く（#3506 の I2b・I7）。そのときの
証跡の照合は、担い手が MVV 判定の証跡を数えない（I9）。承認数が 0 か読めなければ迂回しない。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

from ndf.adapters import ci
from ndf.adapters.github import MERGE_COMMIT, MERGE_QUEUE_PENDING, GitHub, GitHubError, MergeOutcome, Pull
from ndf.domains.release import merge_decide, release_plan, trail

# 必須のチェックの解決は `required_checks` が持つ（`land.Required` は従来の import 先として残す）
from ndf.domains.release.required_checks import NOT_FOUND, Required, RequiredChecks, from_sources

# 本番の PR と版上げの PR のマージの方式。版上げのコミットの起点と範囲（版上げの PR の第 2 親）を保つため merge commit に限る
# （squash・rebase では範囲の PR が読めなくなる）。許されていなければ GitHub へ送らずに止まる
MERGE_METHOD = MERGE_COMMIT
# 承認レビューの規則の種類（ruleset）
REVIEW_RULE = "pull_request"
REVIEW_COUNT = "required_approving_review_count"
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


def _review_count(raw: dict) -> int:
    """規則か従来の保護の承認数（`required_approving_review_count`）。数でなければ 0。"""
    value = raw.get(REVIEW_COUNT)
    return value if isinstance(value, int) else 0


def _judged_refusal(pull: Pull, got: trail.Read, moved: str, unapproved: str) -> Landed | None:
    """読み直した証跡 `got` の先端が `pull` から動いていれば `moved`、承認されていなければ `unapproved` で止まった結果
    （どちらでもなければ None）。"""
    if got.pull.head_sha != pull.head_sha:
        return Landed(False, "refused", moved)
    if not got.judgment.approved:
        return Landed(False, "refused", unapproved)
    return None


def _dequeued(number: int) -> Landed:
    """マージされずにマージキューから外された PR `number` の止まった結果。"""
    return Landed(
        False,
        "not_ci",
        f"#{number} はマージされずにマージキューから外された。merge_group の必須のチェック（または衝突）を"
        "確かめて人へ戻す（打ち直してもキューへ入れ直すだけになる）",
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
            _review_count(rule["parameters"])
            for rule in rules
            if rule.get("type") == REVIEW_RULE and isinstance(rule.get("parameters"), dict)
        ]
        if found:
            return max(found)
        try:
            got = self.hub.rest("GET", f"/repos/{self.hub.repo}/branches/{branch}/protection/required_pull_request_reviews")
        except GitHubError as exc:
            return 0 if exc.status == NOT_FOUND else None
        return _review_count(got) if isinstance(got, dict) else None

    def _bypass(self, pull: Pull) -> bool:
        """この PR を管理者の迂回でマージするか（宣言があり、本番ブランチ宛てで、承認数が 1 以上と読める。#3506 の I2b）。"""
        if not self.admin or not self._is_production(pull):
            return False
        n = self.approvals(pull.base_ref)
        return n is not None and n >= 1

    @property
    def _checks(self) -> RequiredChecks:
        """必須のチェックの解決とチェックの実行の読み（`required_checks.RequiredChecks`。状態を持たない）。"""
        return RequiredChecks(self.hub)

    def required(self, branch: str) -> Required:
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
            names = "、".join(c.name for c in failed)
            listed = f"。落ちたチェック: {names}" if names else ""
            detail = f"#{pull.number} の必須のチェックが通らない（{kind}。コードを直さずに止まる{listed}）"
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

    def _record_admin(self, pull: Pull, done: Landed) -> Landed:
        """迂回でマージした結果に、迂回の記録の URL か、書けなかった理由を足す（#3506 の I7・決定 7）。"""
        try:
            _wrote, url = trail.write_admin_record(self.hub, pull.number, pull.head_sha)
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

    def _await_queue(self, pull: Pull, deadline: float) -> Landed:
        """マージキューへ入れた PR `pull` がマージされるまで、時刻 `deadline`（`clock` の値）まで読み直す。

        読み直すたびに、キューに残っているかと（本番ブランチ宛てなら）承認を確かめる。キューが外したら `not_ci` で
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
            refused = self._queued_refusal(pull, now)
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
            _wrote, url = trail.write_merge_record(self.hub, number, approval)
        except trail.TrailError as exc:
            return done._replace(record_error=str(exc))
        return done._replace(merge_record=url)

    def _released(self, pull: Pull, done: Landed) -> Landed:
        """本番ブランチへ入れた結果に、マージコミットと承認（人のマージなら記録も）を足す。版は CD が決める（決定 7）。"""
        merged_sha = self.hub.pull(pull.number).merge_sha
        done = done._replace(production_sha=merged_sha if merged_sha else None)
        approval = self._approval(pull.number)
        return self._record_merge(pull.number, done, approval) if approval is not None else done

    def _resume_merged(self, number: int, pull: Pull, deadline: float) -> Landed:
        """マージ済みの PR からの再開。本番ブランチ宛てなら承認を決めて（人のマージなら記録を書き）、
        迂回の記録が無ければ補う。同期の PR を作らない（I10）。"""
        if not self._is_production(pull):
            return Landed(True, None, f"#{number} は既にマージされている")
        synced = self._released(pull, Landed(True, None, f"#{number} は既に {self.production} へマージされている"))
        # 再開: 利用者の証跡がマージした先端への承認なら、迂回でマージしたとして記録を補う（I7）
        if self._bypass(pull) and (judged := trail.user_judgment_at(self.hub, number, pull.head_sha)) is not None and judged.approved:
            return self._record_admin(pull, synced)
        return synced

    def land(self, number: int) -> Landed:
        """本番の PR を照合付きでマージし、承認を結果に載せる。head の名前を問わない。同期の PR を作らない（I10）。

        待ちの予算（`budget`）はエントリポイントで 1 つの締め切りにし、チェックの待ちとマージキューの待ちで分け合う。
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
        bypass = self._bypass(pull)
        done = self._merge(pull, deadline, bypass)
        if not done.merged or not self._is_production(pull):
            return done
        released = self._released(pull, done)
        return self._record_admin(pull, released) if bypass else released

    def land_bump(self, number: int, allowed: Callable[[Pull], list[str]]) -> Landed:
        """版上げの PR を、承認の証跡を読まずに、差分の判定と必須のチェックの後に照合付きの迂回でマージする（#3572 の決定 7）。

        `allowed` は版のファイルと CHANGELOG の外に及ぶ差分のパスを返す（空なら版上げだけ）。迂回の宣言が無い・承認数を
        読めない・迂回が断られたときは、ruleset を変えずに済む手段が無いとして止まる（要求の未決 3）。
        """
        deadline = self.clock() + self.budget
        pull = self.hub.pull(number)
        if pull.merged:
            return Landed(True, None, f"#{number} は既にマージされている", production_sha=pull.merge_sha or None)
        outside = allowed(pull)
        if outside:
            return Landed(False, "refused", f"#{number} の差分が版のファイルと CHANGELOG の外に及ぶ: {', '.join(outside)}")
        if not self._bypass(pull):
            return Landed(
                False,
                "precondition",
                f"#{number} を迂回でマージできない（宣言 production_merge.admin が無いか、{pull.base_ref} の承認数を読めない）。"
                "ruleset を変えずに済む手段が無い（要求の未決 3）ため人へ戻す",
            )
        kind, failed = self._watch(pull, deadline)
        if kind is not None:
            names = "、".join(c.name for c in failed)
            listed = f"。落ちたチェック: {names}" if names else ""
            return Landed(False, kind, f"#{number} の必須のチェックが通らない（{kind}{listed}）", failed=failed)
        outcome = self.hub.merge_as_admin(number, expect_head=pull.head_sha, method=MERGE_METHOD)
        if not outcome.merged:
            return Landed(False, outcome.kind, outcome.detail)
        merged_sha = self.hub.pull(number).merge_sha
        return Landed(True, None, f"#{number} を迂回でマージした", production_sha=merged_sha or None, admin=True)
