"""CI の結果の読み取りと、落ちたチェックの分類（外部との接続の層。SF3・SF4）。

読み手は CI の状態を `gh pr checks` の文や Markdown から読まず、この関数が返す止まった理由の種類だけで次の手を
決める。種類と行き先は止まった理由の表（`contracts/causes.json`）が持つ。

| 読んだ結果 | 種類 |
| --- | --- |
| 落ちたテストのチェック（分割ジョブ `<名前> (k/n)` を含む） | `test_failed` |
| 落ちたテスト以外のチェック | `check_failed` |
| 終わっていないチェック | `ci_pending` |
| ランナーの割り当ての待ちだけが `ci.runner-wait` を超えた・終わったのに結論の無いチェック | `infra_wait` |
| 一覧が出そろった後も現れない必須のチェック | `precondition` |
| マージが落ちた後で、落ちたチェックが無い | `not_ci` |
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import NamedTuple, Optional, TypeVar

from ndf.adapters.github import GitHub, text_of
from ndf.contracts import limits

T = TypeVar("T")

# チェックの状態（`Check.state`）
DONE = "done"
QUEUED = "queued"
RUNNING = "running"
STALE = "stale"  # 終わった時刻があるのに結論が無い
# 読んだ結果（`Reading.verdict`）のうち、まだ結論が出ていない・必須のチェックがすべて通った
PENDING = "pending"
PASSED = "passed"
# 通過として読む結論（SKIPPED・NEUTRAL を通す）。ほかの結論で終わったチェックは落ちたと数える
PASSING = ("success", "neutral", "skipped")
# ランナーの割り当てを待つ状態（ランナーの待ちは呼ぶ側の待ちの予算に数えない。#2719）
QUEUED_STATES = ("queued", "waiting", "requested", "pending")
# 分割ジョブの名前の末尾（`pytest (0/2)`）
_SHARD = re.compile(r"\s*\(\d+/\d+\)$")
# pytest の落ちた行（ログの各行の先頭の時刻を除いた後）。`FAILED <パス>::<名前> - <理由>`・`ERROR <パス>`
_PYTEST_FAILED = re.compile(r"(?:^|\s)(?:FAILED|ERROR) (?P<node>[^\s:]+\.py(?:::[^\s]+)?)")
# 読んだ結果（`Reading.verdict`）→ 止まった理由の種類。`passed` は通常 None、マージの後なら `not_ci`
_VERDICT_KINDS = {
    "test_failed": "test_failed",
    "check_failed": "check_failed",
    "pending": "ci_pending",
    "missing": "precondition",
    "stale": "infra_wait",
}


class Check(NamedTuple):
    """1 つのチェックの最新の実行。`state` は `queued`・`running`・`done`・`stale`（終わった時刻があるのに結論が無い）。"""

    name: str
    state: str
    conclusion: str
    job: int

    @property
    def failed(self) -> bool:
        return self.state == DONE and self.conclusion not in PASSING


def _state_of(status: str, conclusion: str, completed: str) -> str:
    """check run の `status`・`conclusion`・`completed_at`（小文字・空文字に揃えた値）から `Check.state` を決める。"""
    if status == "completed" or (completed and conclusion):
        return DONE
    if completed:
        return STALE
    if status in QUEUED_STATES:
        return QUEUED
    return RUNNING


def check_of(run: dict) -> Check:
    """REST の check run 1 件を読む。

    `status` が `completed` でなくても、終わった時刻と結論があれば終わったとして読む（GitHub の状態の不整合。#2690）。
    終わった時刻があり結論の無いものは `stale` にし、待ち続けない。
    """
    status, conclusion, completed = text_of(run["status"]).lower(), text_of(run["conclusion"]).lower(), text_of(run["completed_at"])
    state = _state_of(status, conclusion, completed)
    return Check(text_of(run["name"]), state, conclusion if state == DONE else "", int(run["id"]))


def _newness(run: dict, index: int) -> tuple:
    """新しさ（大きいほど新しい）。時刻の無い実行（まだ始まっていない再実行）は最も新しい。"""
    stamp = max(text_of(run["completed_at"]), text_of(run["started_at"]))
    return (0 if stamp else 1, stamp, int(run["id"]), index)


def latest(runs: Iterable[dict]) -> list[Check]:
    """同じ名前のチェックを最新の実行 1 件へ畳む（再実行で通ったチェックは通ったとして読む）。並びは名前の順。"""
    best: dict[str, tuple[tuple, dict]] = {}
    for i, run in enumerate(runs):
        name, rank = text_of(run["name"]), _newness(run, i)
        if name not in best or rank >= best[name][0]:
            best[name] = (rank, run)
    return [check_of(best[name][1]) for name in sorted(best)]


def bare_name(name: str) -> str:
    """照合に使う名前。分割ジョブの末尾 `(k/n)` と、ワークフローの名前の前置き `<ワークフロー> / ` を外す。"""
    return _SHARD.sub("", name.rpartition(" / ")[2]).strip()


def bare_names(names: Iterable[str]) -> set[str]:
    """照合に使う名前（`bare_name`）の集合。テストのチェックの名前を 1 度だけ揃えて、分類の間で使い回す。"""
    return {bare_name(n) for n in names}


def is_test_check(name: str, tests: Iterable[str]) -> bool:
    """チェックがテストのチェックか。名前の一致か、分割ジョブ（`pytest (1/2)`）の一致で数える（#3153）。"""
    return bare_name(name) in bare_names(tests)


def _partition(items: Iterable[T], name_of: Callable[[T], str], test_names: set[str]) -> tuple[list[T], list[T]]:
    """(テストのチェック, テスト以外) に 1 回の走査で分ける。並びと重なりは保つ。"""
    hits: list[T] = []
    others: list[T] = []
    for item in items:
        (hits if bare_name(name_of(item)) in test_names else others).append(item)
    return hits, others


class Reading(NamedTuple):
    """必須のチェックを 1 回読んだ結果。"""

    failed_tests: list[Check]
    failed_other: list[Check]
    running: list[str]
    queued: list[str]
    stale: list[str]
    missing: list[str]
    settled: bool

    @property
    def verdict(self) -> str:
        """`test_failed`・`check_failed`・`pending`・`missing`・`stale`・`passed` のどれか（先の方を優先する）。"""
        order = [
            ("test_failed", self.failed_tests),
            ("check_failed", self.failed_other),
            (PENDING, self.running or self.queued or (self.missing and not self.settled)),
            ("missing", self.missing),
            ("stale", self.stale),
        ]
        return next((name for name, hit in order if hit), PASSED)

    @property
    def only_queued(self) -> bool:
        """終わっていないチェックが、すべてランナーの割り当てを待っている。"""
        return bool(self.queued) and not self.running


def read(runs: Iterable[dict], required: Iterable[str], tests: Iterable[str]) -> Reading:
    """必須のチェック（分割ジョブを含む）の今の状態を読む。

    一覧にまだ現れない必須のチェックは、ほかのチェックが終わるまでは push の直後として待ち（`pending`）、
    一覧のチェックがすべて終わった後も現れなければ `missing` にする（#2317）。
    """
    checks, test_names = latest(runs), bare_names(tests)
    wanted = {bare_name(r) for r in required}
    mine = [c for c in checks if bare_name(c.name) in wanted]
    present = {bare_name(c.name) for c in mine}
    failed_tests, failed_other = _partition((c for c in mine if c.failed), lambda c: c.name, test_names)
    return Reading(
        failed_tests=failed_tests,
        failed_other=failed_other,
        running=[c.name for c in mine if c.state == RUNNING],
        queued=[c.name for c in mine if c.state == QUEUED],
        stale=[c.name for c in mine if c.state == STALE],
        missing=sorted(wanted - present),
        settled=bool(checks) and all(c.state in (DONE, STALE) for c in checks),
    )


def split_failed(names: Iterable[str], tests: Iterable[str]) -> tuple[list[str], list[str]]:
    """落ちたチェックの名前を (テストのチェック, テスト以外) に分ける（SF4）。"""
    return _partition(names, lambda n: n, bare_names(tests))


def cause_of(reading: Reading, tests: Iterable[str], merge_failed: Optional[Iterable[str]] = None) -> Optional[str]:
    """読んだ結果の止まった理由の種類。通っていれば None。

    `merge_failed` はマージの段階が落ちたと読んだチェックの名前（マージの後の判定だけが渡す）。マージの段階が
    テストのチェックの落ちを読んでいれば、読み直しでまとめのチェックが終わっていなくても `test_failed` にする（#3153）。
    マージの後で落ちたチェックがどこにも無ければ `not_ci`（CI 以外の理由で断られた）。
    """
    after_merge = merge_failed is not None
    merged_tests, merged_other = split_failed(merge_failed or (), tests)
    if merged_tests:
        return "test_failed"
    verdict = reading.verdict
    if merged_other and verdict in (PENDING, PASSED):
        return "check_failed"
    if verdict == PASSED:
        return "not_ci" if after_merge else None
    return _VERDICT_KINDS[verdict]


def failed_test_nodes(log_text: str) -> list[str]:
    """CI のジョブのログから、pytest が落とした（`FAILED`・`ERROR`）テストの node id を並べる（重ねない・昇順）。"""
    return sorted({m.group("node") for m in _PYTEST_FAILED.finditer(log_text)})


def files_of(nodes: Iterable[str]) -> list[str]:
    """node id のテストのファイル（重ねない・昇順）。CI の修正が push の前に手元で打つ範囲（#3188）。"""
    return sorted({n.partition("::")[0] for n in nodes})


def failed_test_nodes_of(github: GitHub, checks: Iterable[Check]) -> list[str]:
    """落ちたテストのチェックのジョブのログから、落ちたテストの node id を集める（`github.failed-logs` ジョブまで）。"""
    cap = int(limits.value("github.failed-logs", "count"))
    found: set[str] = set()
    for check in list(checks)[:cap]:  # noqa: NDF201 上限: ジョブ github.failed-logs 件まで（limits.json）
        found.update(failed_test_nodes(github.job_log(check.job)))
    return sorted(found)


@dataclass
class WatchResult:
    """待ちの結果。`runner_exhausted` はランナーの割り当ての待ちが `ci.runner-wait` を超えたこと。"""

    reading: Reading
    rounds: int
    elapsed: float
    queued_for: float
    runner_exhausted: bool = False

    def cause(self, tests: Iterable[str], merge_failed: Optional[Iterable[str]] = None) -> Optional[str]:
        kind = cause_of(self.reading, tests, merge_failed)
        return "infra_wait" if kind == "ci_pending" and self.runner_exhausted else kind


@dataclass
class Watch:
    """必須のチェックの結論が出るまで読み直す。

    - 間隔は方針 `test-triage.check-wait`（最初の間隔・倍率・頭打ち）。待ちの合計は呼ぶ側が `budget` で渡す
    - 最後の眠りは上限までの残りに縮め、上限の時刻に最後の 1 回を読む（上限より前に打ち切らない。#2224）
    - 終わっていないチェックがすべてランナーの割り当てを待つ間は `budget` に数えず、`ci.runner-wait` で別に数える（#2719）
    - 読むたびに `on_round` へ読んだ結果と経過の秒を渡す（待つ間に何を待っているかを出す。#2317）
    """

    reader: Callable[[], Reading]
    budget: float
    on_round: Optional[Callable[[Reading, float], None]] = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _queued: float = field(default=0.0, init=False)

    def run(self) -> WatchResult:
        policy = limits.policy("test-triage.check-wait")
        runner_cap = limits.seconds("ci.runner-wait")
        interval, start, rounds = float(policy.interval), self.clock(), 0
        while True:
            reading = self.reader()
            rounds += 1
            now = self.clock()
            if self.on_round is not None:
                self.on_round(reading, now - start)
            if reading.verdict != PENDING:
                return WatchResult(reading, rounds, now - start, self._queued)
            wait = self._next_wait(reading, interval, now - start, runner_cap)
            if wait <= 0:
                exhausted = reading.only_queued and self._queued >= runner_cap
                return WatchResult(reading, rounds, now - start, self._queued, exhausted)
            self.sleep(wait)
            if reading.only_queued:
                # 眠りは頼んだ秒より短くならない。時計の差が丸めで縮んでも、頼んだ秒は数える
                self._queued += max(self.clock() - now, wait)
            interval = min(interval * float(policy.factor), float(policy.max_interval))

    def _next_wait(self, reading: Reading, interval: float, elapsed: float, runner_cap: float) -> float:
        """次に眠る秒。上限までの残りに縮める。0 以下なら上限に達した。"""
        if reading.only_queued:
            return min(interval, runner_cap - self._queued)
        return min(interval, self.budget - (elapsed - self._queued))


def watch_head(github: GitHub, sha: str, required: list[str], tests: list[str]) -> Callable[[], Reading]:
    """コミット `sha` の必須のチェックを読む関数（`Watch.reader` に渡す）。読めなければ `GitHubError`。"""
    return lambda: read(github.check_runs(sha), required, tests)
