"""承認の証跡の後に、リリースの PR をマージする（GitHub Actions の workflow が打つ）。

1. 本番ブランチ宛ての PR は、証跡を読み直して今の先端が承認されていることを確かめる（commit status も付け直す）。
   書く token が記録の読み手に信用されなければ、本番ブランチへ入れる前に止まる（マージの後のリリース記録が必ず落ちる。#3487）
2. 宛先のブランチの規則（ruleset）と従来の保護の必須のチェック（check run と commit status）が PR の先端で通るまで待つ。落ちたら打ち直さずに止まった理由で
   返す（同じ CI の失敗を読み直し続けない。#1983）
3. 本番ブランチ宛ての PR は、待ちの後にもう一度証跡を読み直し、待つ前と同じ先端への最新の答えが承認のときだけ進む
   （待つ間に足された差し戻しや消された承認を見落とさない）
4. 先端の SHA を添えて merge commit でマージする（承認の後に先端が動いていれば GitHub が断る）。マージキューへ入れたら、
   予算の範囲で PR がマージされるまで待つ（待たずに返すと、戻す PR・リリース記録・タグが走らない）。待つ間も
   キューに残っているかと承認を読み直し、キューが外したらすぐに止まり、承認が失効したらキューから外して止まる
5. 本番ブランチへ入れたら、本番ブランチからベースブランチ宛ての PR を作り（開いたものがあれば使い直す）、必須のチェックの
   後にマージする（版上げのコミットをベースブランチへ戻す）。マージ済みの PR で打ち直しても、ここから再開する

承認の後はコードを直さない（#3572 の I4）。必須のチェックが落ちたら、落ちたチェック（`Landed.failed`）を結果に載せて
止まり、push もマージもしない。本番ブランチへ入れたら（マージ済みからの再開を含む）、版と承認（`merge_decide.Approval`。
承認ゲートの承認か、人の GitHub でのマージか）を結果に載せ、人のマージなら人のマージの記録を 1 回だけ書く（決定 8）。

迂回の宣言（`admin`。`.ndf/project.json` の `production_merge.admin`）があり、本番ブランチの規則の承認数が 1 以上と読めるときだけ、
本番ブランチ宛ての PR を管理者の迂回でマージし（`GitHub.merge_as_admin`）、迂回の記録を置く（#3506 の I2b・I7）。そのときの
証跡の照合は、担い手が MVV 判定の証跡を数えない（I9）。承認数が 0 か読めなければ迂回しない。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import NamedTuple

from ndf.adapters import ci
from ndf.adapters.github import MERGE_COMMIT, MERGE_QUEUE_PENDING, GitHub, GitHubError, MergeOutcome, Pull, text_of
from ndf.contracts import facts
from ndf.domains.release import approval_decide, merge_decide, trail, version_decide

# 規則の種類（必須のチェック）
REQUIRED_RULE = "required_status_checks"
# リリースの PR と戻す PR のマージの方式。本番ブランチとベースブランチの祖先の関係を保つため merge commit に限る
# （squash・rebase では次のリリースの範囲に出した変更が再び入る）。許されていなければ GitHub へ送らずに止まる
MERGE_METHOD = MERGE_COMMIT
# GitHub が PR の作成を断る状態と、そのうち差分が無いことを表す文言（ベースブランチへ戻す差分が無い）
UNPROCESSABLE = HTTPStatus.UNPROCESSABLE_ENTITY
NOTHING_TO_SYNC_TEXT = "No commits between"
# 従来の保護（branch protection）が無いときの状態
NOT_FOUND = HTTPStatus.NOT_FOUND
# 承認レビューの規則の種類（ruleset）
REVIEW_RULE = "pull_request"
REVIEW_COUNT = "required_approving_review_count"
# 結論の出ていない状態（事実の表 ci-outcomes の pending。commit status の `pending` を含む）
PENDING_STATES = frozenset(facts.ids("ci-outcomes", outcome="pending"))
# マージキューへ入れた PR がマージされたかを読み直す間隔（秒）
QUEUE_POLL_SECONDS = 30.0
# 従来の保護の `app_id` で「どの発行元でもよい」を表す値
ANY_APP = -1


class Required(NamedTuple):
    """宛先の必須のチェック。`names` は名前（重複なく整列）、`apps` は発行元のアプリ（ruleset の `integration_id`・
    従来の保護の `app_id`）を指定された名前ごとのアプリの ID（#3506）。"""

    names: list[str]
    apps: dict[str, frozenset[int]]


class Landed(NamedTuple):
    """マージの結果。止まったら `kind` に止まった理由の種類。"""

    merged: bool
    kind: str | None
    detail: str
    sync_pr: int | None = None
    # 本番ブランチへ入ったコミット（タグと GitHub Release を作る版。本番ブランチへ入れていなければ None）
    production_sha: str | None = None
    # 本番の PR を管理者の迂回でマージしたか・迂回の記録の URL・記録を書けなかった理由（#3506）
    admin: bool = False
    admin_record: str | None = None
    record_error: str | None = None
    # 出した版・承認（経路・承認した人・先端）・止まったときの落ちたチェック・人のマージの記録の URL（#3572）
    version: str | None = None
    approval: merge_decide.Approval | None = None
    failed: tuple[ci.Check, ...] = ()
    merge_record: str | None = None


def release_version(ref: str) -> str | None:
    """リリースブランチ `ref` の名前の版（リリースブランチでなければ None）。本番の PR の head から出した版を読む。"""
    return version_decide.version_of_branch(ref) if ref.startswith(version_decide.BRANCH_PREFIX) else None


def _status_run(status: dict, app: int | None = None) -> dict:
    """commit status 1 件を check run の形にする（`ci.read` に渡す）。`app` は書き手の GitHub App（分かれば `app.id` に入れる）。"""
    state = text_of(status["state"]).lower()
    conclusion = "" if state in PENDING_STATES else state
    run = {
        "name": text_of(status["context"]),
        "status": "completed" if conclusion else "in_progress",
        "conclusion": conclusion,
        "completed_at": text_of(status["updated_at"]) if conclusion else "",
        "started_at": text_of(status["created_at"]),
        "id": int(status["id"]),
    }
    if app is not None:
        run["app"] = {"id": app}
    return run


def _review_count(raw: dict) -> int:
    """規則か従来の保護の承認数（`required_approving_review_count`）。数でなければ 0。"""
    value = raw.get(REVIEW_COUNT)
    return value if isinstance(value, int) else 0


def _list(raw: dict, key: str) -> list:
    """`raw[key]` が配列ならそれ、ほかは空の配列。"""
    got = raw.get(key)
    return got if isinstance(got, list) else []


def _app(value: object) -> int | None:
    """発行元のアプリの ID（指定が無い・どれでもよい（-1）なら None）。"""
    return value if isinstance(value, int) and not isinstance(value, bool) and value != ANY_APP and value > 0 else None


def _protection_checks(branch: dict) -> list[tuple[str, int | None]]:
    """`GET /branches/<名前>` の従来の保護の必須のチェック（名前, 発行元）。`contexts` は発行元を持たない。"""
    protection = branch.get("protection")
    checks = protection.get("required_status_checks") if isinstance(protection, dict) else None
    if not isinstance(checks, dict):
        return []
    contexts: list[tuple[str, int | None]] = [(text_of(c), None) for c in _list(checks, "contexts")]
    return contexts + [(text_of(c["context"]), _app(c.get("app_id"))) for c in _list(checks, "checks") if isinstance(c, dict)]


def _ruleset_checks(rules: list[dict]) -> list[tuple[str, int | None]]:
    """宛先のブランチの規則（ruleset）の必須のチェック（名前, 発行元）。"""
    return [
        (text_of(check["context"]), _app(check.get("integration_id")))
        for rule in rules
        if rule.get("type") == REQUIRED_RULE
        for check in rule["parameters"]["required_status_checks"]
    ]


def _required_of(pairs: list[tuple[str, int | None]]) -> Required:
    """必須のチェック（名前, 発行元）の並びを、承認の commit status を除いて名前と発行元のアプリへ集める。"""
    pairs = [(n, app) for n, app in pairs if n != approval_decide.STATUS_CONTEXT]
    apps: dict[str, set[int]] = {}
    for name, app in pairs:
        if app is not None:
            apps.setdefault(name, set()).add(app)
    return Required(sorted({n for n, _ in pairs}), {k: frozenset(v) for k, v in apps.items()})


def _restricted(name: str, apps: dict[str, frozenset[int]]) -> list[frozenset[int]]:
    """チェックの名前 `name` が `ci.read` の照合で当たる、発行元を指定された必須のチェックのアプリの集合の並び。"""
    return [want for required, want in apps.items() if ci.fulfils(name, required)]  # noqa: NDF201 上限: 発行元を指定した必須のチェックの数（ci.fulfils は名前の照合だけで外へ出ない）


def _from_sources(runs: list[dict], apps: dict[str, frozenset[int]]) -> list[dict]:
    """発行元を指定された必須のチェックに当たる実行は、指定のアプリのもの（check run と、そのアプリの bot が書いた commit status）
    だけを残す（同じ名前の別の発行元を数えない。#3506）。

    当たるかは `ci.read` と同じ照合（`ci.fulfils`）で決める。生の名前で絞ると、前置きや分割ジョブの末尾の付いた別の発行元の
    実行（`other / pytest`）が絞り込みを抜け、`ci.read` が必須のチェックとして数える。同じ名前に別々のアプリが指定されていれば、
    1 つの実行では満たせないため残さない（待ちが止める）。"""
    return [r for r in runs if all(want == {_app_of(r)} for want in _restricted(text_of(r.get("name")), apps))]


def _app_of(run: dict) -> object:
    """実行の発行元のアプリの ID（`app` が無い commit status は None）。"""
    app = run.get("app")
    return app.get("id") if isinstance(app, dict) else None


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
        "キューがマージした後に `ndf release-steps merge` を打ち直す（戻す PR・リリース記録・タグへ進む）",
    )


@dataclass(frozen=True)
class Lander:
    """1 つのリポジトリのリリースの PR をマージする。`wait` は必須のチェックの待ちを組む関数（テストが差し替える）。"""

    hub: GitHub
    base: str
    production: str
    budget: float
    wait: Callable[[Callable[[], ci.Reading], float], ci.WatchResult] | None = None
    # 迂回の宣言（`declaration.production_admin`）。偽なら今のマージのまま
    admin: bool = False
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep

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
        if not self.admin or pull.base_ref != self.production:
            return False
        n = self.approvals(pull.base_ref)
        return n is not None and n >= 1

    def required(self, branch: str) -> Required:
        """宛先のブランチの規則（ruleset。全ページ）と従来の保護の必須のチェック（承認の commit status を除く）。"""
        pairs = _ruleset_checks(self.hub.branch_rules(branch))
        try:
            got = self.hub.rest("GET", f"/repos/{self.hub.repo}/branches/{branch}")
        except GitHubError as exc:
            if exc.status != NOT_FOUND:
                raise
            got = {}
        return _required_of(pairs + _protection_checks(got if isinstance(got, dict) else {}))

    def _runs(self, sha: str, apps: dict[str, frozenset[int]], bots: dict[str, int | None]) -> list[dict]:
        """コミットの check run と commit status（check run の形にしたもの）の一覧。

        発行元を指定された必須のチェックがあれば、commit status を全件（書き手つき）で読み、それに当たる status には
        書き手の bot のアプリを付ける（指定のアプリが付けた status を満たしたと数える。#3506）。`bots` は bot ごとのアプリの控え。"""
        if not apps:
            combined = self.hub.rest("GET", f"/repos/{self.hub.repo}/commits/{sha}/status", params={"per_page": 100})
            statuses = _list(combined, "statuses") if isinstance(combined, dict) else []
            return self.hub.check_runs(sha) + [_status_run(s) for s in statuses if isinstance(s, dict)]
        return self.hub.check_runs(sha) + [_status_run(s, self._status_app(s, apps, bots)) for s in self.hub.statuses(sha)]

    def _status_app(self, status: dict, apps: dict[str, frozenset[int]], bots: dict[str, int | None]) -> int | None:
        """発行元を指定された必須のチェックに当たる commit status の書き手のアプリ（当たらない・bot でなければ None）。"""
        if not _restricted(text_of(status.get("context")), apps):
            return None
        creator = status.get("creator")
        login = text_of(creator.get("login")) if isinstance(creator, dict) else ""
        if login not in bots:
            bots[login] = self.hub.bot_app(login)
        return bots[login]

    def _watch(self, pull: Pull, deadline: float) -> tuple[str | None, tuple[ci.Check, ...]]:
        """必須のチェック（check run と commit status）を時刻 `deadline` まで待ち、止まった理由の種類（通れば None）と
        最後の読みの落ちたチェックを返す。"""
        required = self.required(pull.base_ref)
        bots: dict[str, int | None] = {}

        def reader() -> ci.Reading:
            return ci.read(_from_sources(self._runs(pull.head_sha, required.apps, bots), required.apps), required.names, [])

        left = max(0.0, deadline - self.clock())
        watched = self.wait(reader, left) if self.wait is not None else ci.Watch(reader, left).run()
        kind = watched.cause([])
        if kind is None:
            return None, ()
        return kind, (*watched.reading.failed_tests, *watched.reading.failed_other)

    def _refusal(self, pull: Pull, human_only: bool = False) -> Landed | None:
        """本番ブランチ宛ての PR の証跡を読み直し、`pull` の先端が承認されていなければ止まった結果（承認されていれば None）。
        `human_only` なら担い手が MVV 判定の証跡を数えない（迂回の前提。#3506 の I9）。"""
        if pull.base_ref != self.production:
            return None
        got = trail.check(self.hub, pull.number, human_only)
        if got.pull.head_sha != pull.head_sha:
            return Landed(False, "refused", f"#{pull.number} の先端が {pull.head_sha[:12]} から {got.pull.head_sha[:12]} へ動いた")
        if not got.judgment.approved:
            return Landed(False, "refused", f"#{pull.number} は承認されていない: {got.judgment.detail}")
        return None

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
        `bypass` なら管理者の迂回で打つ（戻す PR は常に偽）。迂回でなくマージキューへ入れたら、マージされるまで待つ。"""
        blocked = self._blocker(pull, deadline, bypass)
        if blocked is not None:
            return blocked
        if bypass:
            outcome: MergeOutcome = self.hub.merge_as_admin(pull.number, expect_head=pull.head_sha, method=MERGE_METHOD)
            return Landed(outcome.merged, outcome.kind, outcome.detail)  # 迂回の印は _record が立てる
        outcome = self.hub.merge(pull.number, expect_head=pull.head_sha, method=MERGE_METHOD, require_method=True)
        if outcome.kind == MERGE_QUEUE_PENDING:
            return self._await_queue(pull, deadline)
        return Landed(outcome.merged, outcome.kind, outcome.detail)

    def _record(self, pull: Pull, done: Landed) -> Landed:
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
        if pull.base_ref != self.production:
            return None
        got = trail.judged(self.hub, now)
        if got.pull.head_sha != pull.head_sha:
            return Landed(False, "refused", f"#{pull.number} の先端がキューの待ちの間に {got.pull.head_sha[:12]} へ動いた")
        if not got.judgment.approved:
            return Landed(False, "refused", f"#{pull.number} の承認がキューの待ちの間に失効した: {got.judgment.detail}")
        return None

    def _await_queue(self, pull: Pull, deadline: float) -> Landed:
        """マージキューへ入れた PR `pull` がマージされるまで、時刻 `deadline`（`clock` の値）まで読み直す。

        読み直すたびに、キューに残っているかと（本番ブランチ宛てなら）承認を確かめる。キューが外したら `not_ci` で
        すぐに止まり、承認が失効したらキューから外して `refused` で止まる（前の承認のままキューがマージしない）。
        予算の内にマージされなければ `ci_pending` で止まり、キューがマージした後の打ち直しを促す（`land` はマージ済みの
        本番ブランチ宛ての PR から、戻す PR へ再開する）。
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

    def _open_sync(self) -> int | None:
        """本番ブランチからベースブランチへ戻す PR の番号（開いたものがあれば使い直し、無ければ作る。差分が無ければ None）。

        head は本番ブランチにする（リリースのブランチはマージの後に自動で消えることがある）。
        """
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

    def _sync(self, pull: Pull, deadline: float) -> Landed:
        """本番ブランチへ入れた版上げを、ベースブランチへ戻す PR でマージする（マージ済みからの再開でも打つ）。"""
        merged_sha = self.hub.pull(pull.number).merge_sha
        landed = merged_sha if merged_sha else None
        number = self._open_sync()
        if number is None:
            return Landed(True, None, f"#{pull.number} をマージした（{self.base} へ戻す差分は無い）", production_sha=landed)
        synced = self._merge(self.hub.pull(number), deadline)
        detail = f"#{pull.number} をマージした。戻す PR: {synced.detail}"
        return synced._replace(sync_pr=number, detail=detail, production_sha=landed)

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
        """本番ブランチへ入れた結果に、版と承認（人のマージなら記録も）を足す。"""
        done = done._replace(version=release_version(pull.head_ref))
        approval = self._approval(pull.number)
        return self._record_merge(pull.number, done, approval) if approval is not None else done

    def _resume_merged(self, number: int, pull: Pull, deadline: float) -> Landed:
        """マージ済みの PR からの再開。本番ブランチ宛てならベースブランチへ戻し、承認を決めて（人のマージなら記録を書き）、
        迂回の記録が無ければ補う。"""
        if pull.base_ref != self.production:
            return Landed(True, None, f"#{number} は既にマージされている")
        synced = self._released(pull, self._sync(pull, deadline))
        # 再開: 利用者の証跡がマージした先端への承認なら、迂回でマージしたとして記録を補う（I7）
        if self._bypass(pull) and (judged := trail.user_judgment_at(self.hub, number, pull.head_sha)) is not None and judged.approved:
            return self._record(pull, synced)
        return synced

    def land(self, number: int) -> Landed:
        """リリースの PR をマージする。本番ブランチへ入れた後（マージ済みからの再開を含む）は、ベースブランチへ戻す。

        待ちの予算（`budget`）はエントリポイントで 1 つの締め切りにし、本番の PR と戻す PR のチェックの待ち・マージキューの待ちで
        分け合う（PR ごとに取り直すと合計が予算の倍になり、workflow の時間の上限を超えて取り消される）。
        """
        deadline = self.clock() + self.budget
        pull = self.hub.pull(number)
        if pull.merged:
            return self._resume_merged(number, pull, deadline)
        version_decide.version_of_branch(pull.head_ref)
        if pull.base_ref == self.production and not self.hub.token_trusted():
            return Landed(
                False,
                "refused",
                f"#{number} を {self.production} へ入れる token の書き手が write 以上の権限を持つ利用者でなく、マージの後の"
                "リリース記録が読まれない。利用者の token（workflow では secrets.NDF_RELEASE_TOKEN）でマージする",
            )
        bypass = self._bypass(pull)
        done = self._merge(pull, deadline, bypass)
        if not done.merged or pull.base_ref != self.production:
            return done
        synced = self._released(pull, self._sync(pull, deadline))
        return self._record(pull, synced) if bypass else synced
