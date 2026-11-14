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
from enum import StrEnum
from typing import NamedTuple, Optional, TypeVar

from ndf.adapters.github import GitHub, text_of
from ndf.contracts import facts, limits

T = TypeVar("T")


class State(StrEnum):
    """チェックの状態（`Check.state`）。"""

    DONE = "done"
    QUEUED = "queued"
    RUNNING = "running"
    STALE = "stale"  # 終わった時刻があるのに結論が無い


class Verdict(StrEnum):
    """読んだ結果（`Reading.verdict`）。先の方を優先する。"""

    TEST_FAILED = "test_failed"
    CHECK_FAILED = "check_failed"
    PENDING = "pending"  # まだ結論が出ていない
    MISSING = "missing"
    STALE = "stale"
    PASSED = "passed"  # 必須のチェックがすべて通った


# 通過として読む結論（SKIPPED・NEUTRAL を通す）。ほかの結論で終わったチェックは落ちたと数える。表は contracts/domains/ci-outcomes.json
PASSING = frozenset(facts.ids("ci-outcomes", outcome="passed"))
# ランナーの割り当てを待つ状態（ランナーの待ちは呼ぶ側の待ちの予算に数えない。#2719）。待つ状態のうち実行中と結果の届いていない状態を除く
QUEUED_STATES = frozenset(facts.ids("ci-outcomes", outcome="pending")) - {"in_progress", "expected"}
# 分割ジョブの名前の末尾（`pytest (0/2)`）
_SHARD = re.compile(r"\s*\(\d+/\d+\)$")
# pytest の落ちた行（ログの各行の先頭の時刻を除いた後）。`FAILED <パス>::<名前> - <理由>`・`ERROR <パス>`
_PYTEST_FAILED = re.compile(r"(?:^|\s)(?:FAILED|ERROR) (?P<node>[^\s:]+\.py(?:::[^\s]+)?)")
# 止まった理由の種類（値は contracts/causes.json の kind）
TEST_FAILED_KIND = "test_failed"
CHECK_FAILED_KIND = "check_failed"
CI_PENDING_KIND = "ci_pending"
PRECONDITION_KIND = "precondition"
INFRA_WAIT_KIND = "infra_wait"
NOT_CI_KIND = "not_ci"
# 読んだ結果（`Reading.verdict`）→ 止まった理由の種類。`passed` は通常 None、マージの後なら `not_ci`
_VERDICT_KINDS = {
    Verdict.TEST_FAILED: TEST_FAILED_KIND,
    Verdict.CHECK_FAILED: CHECK_FAILED_KIND,
    Verdict.PENDING: CI_PENDING_KIND,
    Verdict.MISSING: PRECONDITION_KIND,
    Verdict.STALE: INFRA_WAIT_KIND,
}


class Check(NamedTuple):
    """1 つのチェックの最新の実行。`state` は `queued`・`running`・`done`・`stale`（終わった時刻があるのに結論が無い）。"""

    name: str
    state: str
    conclusion: str
    job: int

    @property
    def failed(self) -> bool:
        return self.state == State.DONE and self.conclusion not in PASSING


def _state_of(status: str, conclusion: str, completed: str) -> str:
    """check run の `status`・`conclusion`・`completed_at`（小文字・空文字に揃えた値）から `Check.state` を決める。"""
    if status == "completed" or (completed and conclusion):
        return State.DONE
    if completed:
        return State.STALE
    if status in QUEUED_STATES:
        return State.QUEUED
    return State.RUNNING


def check_of(run: dict) -> Check:
    """REST の check run 1 件を読む。

    `status` が `completed` でなくても、終わった時刻と結論があれば終わったとして読む（GitHub の状態の不整合。#2690）。
    終わった時刻があり結論の無いものは `stale` にし、待ち続けない。
    """
    status, conclusion, completed = text_of(run["status"]).lower(), text_of(run["conclusion"]).lower(), text_of(run["completed_at"])
    state = _state_of(status, conclusion, completed)
    return Check(text_of(run["name"]), state, conclusion if state == State.DONE else "", int(run["id"]))


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


def _unprefixed(name: str) -> str:
    """ワークフローの名前の前置き `<ワークフロー> / ` を最後の ` / ` で外した名前（空白と分割ジョブの末尾は残す）。"""
    return name.rpartition(" / ")[2]


def bare_name(name: str) -> str:
    """照合に使う名前。分割ジョブの末尾 `(k/n)` と、ワークフローの名前の前置き `<ワークフロー> / ` を外す。"""
    return _SHARD.sub("", _unprefixed(name)).strip()


def bare_names(names: Iterable[str]) -> set[str]:
    """照合に使う名前（`bare_name`）の集合。テストのチェックの名前を 1 度だけ揃えて、分類の間で使い回す。"""
    return {bare_name(n) for n in names}


def _short_name(name: str) -> str:
    """ワークフローの名前の前置き `<ワークフロー> / ` を外し、前後の空白を除いた名前（分割ジョブの末尾は保つ）。"""
    return _unprefixed(name).strip()


def required_key(name: str) -> str:
    """必須のチェックの照合の鍵。分割ジョブを名指しした（`pytest (1/2)`）ものはその分割を保ち、ほかは `bare_name`。

    名指しした分割を畳むと、`pytest (0/2)` だけが通った時点で `pytest (1/2)` も揃ったと読んでしまう。
    """
    short = _short_name(name)
    return short if _SHARD.search(short) else bare_name(name)


def _fulfils(check_name: str, key: str) -> bool:
    """チェックが必須の鍵（`required_key`）に当たるか。分割を名指しした鍵はその分割だけに当たる。"""
    return _short_name(check_name) == key if _SHARD.search(key) else bare_name(check_name) == key


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
            (Verdict.TEST_FAILED, self.failed_tests),
            (Verdict.CHECK_FAILED, self.failed_other),
            (Verdict.PENDING, self.running or self.queued or (self.missing and not self.settled)),
            (Verdict.MISSING, self.missing),
            (Verdict.STALE, self.stale),
        ]
        return next((name for name, hit in order if hit), Verdict.PASSED)

    @property
    def only_queued(self) -> bool:
        """終わっていないチェックが、すべてランナーの割り当てを待っている。"""
        return bool(self.queued) and not self.running


def _match_required(checks: list[Check], wanted: set[str]) -> tuple[list[Check], set[str]]:
    """(必須の鍵のどれかに当たるチェック, 当たるチェックのあった必須の鍵)。照合は `_fulfils`。"""
    mine = [c for c in checks if any(_fulfils(c.name, w) for w in wanted)]
    present = {w for w in wanted if any(_fulfils(c.name, w) for c in mine)}
    return mine, present


def read(runs: Iterable[dict], required: Iterable[str], tests: Iterable[str]) -> Reading:
    """必須のチェック（分割ジョブを含む）の今の状態を読む。

    一覧にまだ現れない必須のチェックは、ほかのチェックが終わるまでは push の直後として待ち（`pending`）、
    一覧のチェックがすべて終わった後も現れなければ `missing` にする（#2317）。
    """
    checks, test_names = latest(runs), bare_names(tests)
    wanted = {required_key(r) for r in required}
    mine, present = _match_required(checks, wanted)
    failed_tests, failed_other = _partition((c for c in mine if c.failed), lambda c: c.name, test_names)
    return Reading(
        failed_tests=failed_tests,
        failed_other=failed_other,
        running=[c.name for c in mine if c.state == State.RUNNING],
        queued=[c.name for c in mine if c.state == State.QUEUED],
        stale=[c.name for c in mine if c.state == State.STALE],
        missing=sorted(wanted - present),
        settled=bool(checks) and all(c.state in (State.DONE, State.STALE) for c in checks),
    )


def split_failed(names: Iterable[str], tests: Iterable[str]) -> tuple[list[str], list[str]]:
    """落ちたチェックの名前を (テストのチェック, テスト以外) に分ける（SF4）。"""
    return _partition(names, lambda n: n, bare_names(tests))


class Classifier(NamedTuple):
    """読んだ結果を止まった理由の種類へ分ける前提。`tests` はテストのチェックの名前、`merge_failed` はマージの段階が
    落ちたと読んだチェックの名前（マージの後の判定だけが渡す）。"""

    tests: Iterable[str]
    merge_failed: Optional[Iterable[str]] = None

    def cause(self, reading: Reading) -> Optional[str]:
        """読んだ結果の止まった理由の種類。通っていれば None。

        マージの段階がテストのチェックの落ちを読んでいれば、読み直しでまとめのチェックが終わっていなくても
        `test_failed` にする（#3153）。マージの後で落ちたチェックがどこにも無ければ `not_ci`（CI 以外の理由で断られた）。
        """
        after_merge = self.merge_failed is not None
        merged_tests, merged_other = split_failed(self.merge_failed if self.merge_failed is not None else [], self.tests)
        if merged_tests:
            return TEST_FAILED_KIND
        verdict = reading.verdict
        if merged_other and verdict in (Verdict.PENDING, Verdict.PASSED):
            return CHECK_FAILED_KIND
        if verdict == Verdict.PASSED:
            return NOT_CI_KIND if after_merge else None
        return _VERDICT_KINDS[verdict]


def cause_of(reading: Reading, tests: Iterable[str], merge_failed: Optional[Iterable[str]] = None) -> Optional[str]:
    """読んだ結果の止まった理由の種類（`Classifier.cause`）。通っていれば None。"""
    return Classifier(tests, merge_failed).cause(reading)


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
        return self.cause_by(Classifier(tests, merge_failed))

    def cause_by(self, classifier: Classifier) -> Optional[str]:
        """止まった理由の種類。ランナーの割り当ての待ちだけで上限を超えた `ci_pending` は `infra_wait`。"""
        kind = classifier.cause(self.reading)
        return INFRA_WAIT_KIND if kind == CI_PENDING_KIND and self.runner_exhausted else kind


@dataclass
class Watch:
    """必須のチェックの結論が出るまで読み直す。

    - 間隔は方針 `test-triage.check-wait`（最初の間隔・倍率・頭打ち）。読んだ結果が変わらない間は伸ばし、変われば
      最初の間隔へ戻す（不足 g）。待ちの合計は呼ぶ側が `budget` で渡す
    - 最後の眠りは上限までの残りに縮め、上限の時刻に最後の 1 回を読む（上限より前に打ち切らない。#2224）
    - 終わっていないチェックがすべてランナーの割り当てを待つ間は `budget` に数えず、`ci.runner-wait` で別に数える（#2719）
    - 読むたびに `on_round` へ読んだ結果と経過の秒を渡す（待つ間に何を待っているかを出す。#2317）
    - `decided` が真を返したら、結論の出ていないチェックが残っていても待たずに返す（マージの段階から引き継いだ
      失敗で止まった理由が決まっているとき、読み直しに API と待ちを使わない）
    - 予算は `started`（`clock` の値）から数える。省けば作った時点。前の段階で待ち始めた呼び手は、その時刻を渡して
      同じ予算を引き継ぐ。始まりの時刻は `started` で読める
    """

    reader: Callable[[], Reading]
    budget: float
    on_round: Optional[Callable[[Reading, float], None]] = None
    decided: Optional[Callable[[Reading], bool]] = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    started: Optional[float] = None
    _queued: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        # 省いたら作った時点から数える（`run` の前の API の呼び出しも `remaining` で同じ予算に入れる）
        if self.started is None:
            self.started = self.clock()

    def remaining(self) -> float:
        """待ちに使える残りの秒。ランナーの割り当ての待ちは予算に数えない（API の上限の待ちの締め切りに使う）。"""
        return self._remaining_at(self.clock())

    def _remaining_at(self, now: float) -> float:
        """時刻 `now` での待ちに使える残りの秒（ランナーの割り当ての待ちを予算に数えない）。"""
        return self.budget - (now - self.started - self._queued)

    def run(self) -> WatchResult:
        policy = limits.policy("test-triage.check-wait")
        runner_cap = limits.seconds("ci.runner-wait")
        interval, rounds = float(policy.interval), 0
        previous: Optional[Reading] = None
        while True:
            rounds += 1
            reading, now, done = self._round(rounds)
            if done is not None:
                return done
            if previous is not None and reading != previous:
                # 読んだ結果が変わった（チェックが動いた）ら、間隔を最初の値へ戻す。変化が無い間だけ伸ばす（不足 g）
                interval = float(policy.interval)
            previous = reading
            wait = self._next_wait(reading, interval, now, runner_cap)
            if wait <= 0:
                return self._exhausted_result(reading, rounds, now, runner_cap)
            self.sleep(wait)
            if reading.only_queued:
                # 眠りは頼んだ秒より短くならない。時計の差が丸めで縮んでも、頼んだ秒は数える
                self._queued += max(self.clock() - now, wait)
            interval = min(interval * float(policy.factor), float(policy.max_interval))

    def _round(self, rounds: int) -> tuple[Reading, float, Optional[WatchResult]]:
        """1 周（`rounds` 周目）: 読み、経過の秒を `on_round` へ渡し、結論が出た（か `decided` が真）なら結果を返す。

        返すのは読んだ結果・読んだ時刻・待ちを終える結果（読み直すなら None）。
        """
        reading = self.reader()
        now = self.clock()
        elapsed = now - self.started
        if self.on_round is not None:
            self.on_round(reading, elapsed)
        if reading.verdict != Verdict.PENDING or (self.decided is not None and self.decided(reading)):
            return reading, now, WatchResult(reading, rounds, elapsed, self._queued)
        return reading, now, None

    def _exhausted_result(self, reading: Reading, rounds: int, now: float, runner_cap: float) -> WatchResult:
        """待ちが上限に達したときの結果。ランナーの割り当ての待ちだけで上限を超えたら `runner_exhausted`。"""
        exhausted = reading.only_queued and self._queued >= runner_cap
        return WatchResult(reading, rounds, now - self.started, self._queued, exhausted)

    def _next_wait(self, reading: Reading, interval: float, now: float, runner_cap: float) -> float:
        """時刻 `now` から次に眠る秒。上限までの残りに縮める。0 以下なら上限に達した。"""
        if reading.only_queued:
            return min(interval, runner_cap - self._queued)
        return min(interval, self._remaining_at(now))


def watch_head(github: GitHub, sha: str, required: list[str], tests: list[str]) -> Callable[[], Reading]:
    """コミット `sha` の必須のチェックを読む関数（`Watch.reader` に渡す）。読めなければ `GitHubError`。"""
    return lambda: read(github.check_runs(sha), required, tests)
