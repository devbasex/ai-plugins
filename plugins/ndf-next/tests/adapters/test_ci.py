"""CI の結果の読み取りと落ちたチェックの分類（`ndf.adapters.ci`）。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from ndf.adapters import ci
from ndf.contracts import causes, limits

from ._ci_helpers import LOG, run


# --- 1 件の読み方 ---


def test_check_of_reads_in_progress_with_completed_at_and_conclusion_as_done() -> None:
    # #2690: status だけが in_progress のまま、終わった時刻と結論のあるチェックを待ち続けない
    c = ci.check_of(run("skill-check", status="in_progress", conclusion="success", completed_at="2026-11-06T13:46:38Z"))
    assert (c.state, c.conclusion, c.failed) == ("done", "success", False)


def test_check_of_reads_completed_at_without_conclusion_as_stale() -> None:
    c = ci.check_of(run("skill-check", status="in_progress", completed_at="2026-11-06T13:46:38Z"))
    assert c.state == "stale"


@pytest.mark.parametrize(("status", "state"), [("queued", "queued"), ("waiting", "queued"), ("in_progress", "running")])
def test_check_of_unfinished_states(status: str, state: str) -> None:
    assert ci.check_of(run("pytest", status=status)).state == state


def test_latest_prefers_rerun_and_unstarted_rerun() -> None:
    old = run("pytest", conclusion="failure", rid=1)
    rerun = run("pytest", conclusion="success", rid=2, started_at="2026-11-12T14:00:00Z", completed_at="2026-11-12T14:05:00Z")
    assert ci.latest([old, rerun])[0].conclusion == "success"
    queued = run("pytest", status="queued", rid=3, started_at=None)
    assert ci.latest([old, rerun, queued])[0].state == "queued"


@pytest.mark.parametrize(
    ("name", "is_test"),
    [("pytest", True), ("pytest (0/2)", True), ("Python tests / pytest (1/2)", True), ("pytest-lint", False), ("lint", False)],
)
def test_is_test_check_counts_shards(name: str, is_test: bool) -> None:
    # #3153: 分割ジョブ `pytest (k/n)` を、その名前のテストのチェックとして数える
    assert ci.is_test_check(name, ["pytest"]) is is_test


# --- 必須のチェックの読み方と種類 ---


def test_read_failed_shard_is_test_failure_while_aggregate_pending() -> None:
    # #3153 の再現: シャードが落ち、まとめのチェックがまだ終わっていない
    runs = [
        run("pytest (0/2)", conclusion="failure", rid=11),
        run("pytest (1/2)", rid=12),
        run("pytest", status="in_progress", rid=13),
    ]
    reading = ci.read(runs, ["pytest"], ["pytest"])
    assert reading.verdict == "test_failed"
    assert [c.name for c in reading.failed_tests] == ["pytest (0/2)"]
    assert ci.cause_of(reading, ["pytest"]) == "test_failed"


def test_read_failed_non_test_check_is_check_failed() -> None:
    reading = ci.read([run("pytest", rid=1), run("doc-lint", conclusion="failure", rid=2)], ["pytest", "doc-lint"], ["pytest"])
    assert ci.cause_of(reading, ["pytest"]) == "check_failed"


def test_read_test_failure_wins_over_other_failures() -> None:
    runs = [run("pytest (0/2)", conclusion="failure", rid=1), run("doc-lint", conclusion="failure", rid=2)]
    assert ci.cause_of(ci.read(runs, ["pytest", "doc-lint"], ["pytest"]), ["pytest"]) == "test_failed"


def test_read_ignores_checks_that_are_not_required() -> None:
    runs = [run("pytest", rid=1), run("optional", conclusion="failure", rid=2)]
    assert ci.cause_of(ci.read(runs, ["pytest"], ["pytest"]), ["pytest"]) is None


def test_missing_check_waits_until_the_list_settles() -> None:
    # #2317: push の直後（ほかが終わっていない）は待ち、出そろった後も現れなければ名前を挙げて止まる
    early = ci.read([run("pytest", rid=1), run("other", status="in_progress", rid=2)], ["pytest", "runtime-smoke (kiro)"], ["pytest"])
    assert early.verdict == "pending"
    settled = ci.read([run("pytest", rid=1), run("other", rid=2)], ["pytest", "runtime-smoke (kiro)"], ["pytest"])
    assert (settled.verdict, settled.missing) == ("missing", ["runtime-smoke (kiro)"])
    assert ci.cause_of(settled, ["pytest"]) == "precondition"


def test_named_shards_are_required_one_by_one() -> None:
    # 名指しした分割ジョブは畳まない。(0/2) だけが通っても (1/2) を待ち、出そろった後も無ければ名前を挙げる
    required = ["pytest (0/2)", "pytest (1/2)"]
    early = ci.read([run("pytest (0/2)", rid=1), run("other", status="in_progress", rid=2)], required, ["pytest"])
    assert early.verdict == "pending"
    settled = ci.read([run("pytest (0/2)", rid=1), run("CI / other", rid=2)], required, ["pytest"])
    assert (settled.verdict, settled.missing) == ("missing", ["pytest (1/2)"])
    both = ci.read([run("CI / pytest (0/2)", rid=1), run("pytest (1/2)", rid=2)], required, ["pytest"])
    assert both.verdict == "passed"


def test_empty_list_is_pending() -> None:
    assert ci.read([], ["pytest"], ["pytest"]).verdict == "pending"


def test_stale_check_is_infra_wait() -> None:
    runs = [run("pytest", rid=1), run("skill-check", status="in_progress", completed_at="2026-11-06T13:46:38Z", rid=2)]
    assert ci.cause_of(ci.read(runs, ["pytest", "skill-check"], ["pytest"]), ["pytest"]) == "infra_wait"


def test_after_merge_inherits_merge_failures_while_reading_is_pending() -> None:
    # #3153: マージの段階が読んだ落ちたシャードを引き継ぎ、読み直しが pending でも CI 以外と読まない
    pending = ci.read([run("pytest", status="in_progress", rid=1)], ["pytest"], ["pytest"])
    assert ci.cause_of(pending, ["pytest"], merge_failed=["pytest (1/2)"]) == "test_failed"
    assert ci.cause_of(pending, ["pytest"], merge_failed=["doc-lint"]) == "check_failed"
    assert ci.cause_of(pending, ["pytest"], merge_failed=[]) == "ci_pending"


def test_after_merge_without_failed_checks_is_not_ci() -> None:
    passed = ci.read([run("pytest", rid=1)], ["pytest"], ["pytest"])
    assert ci.cause_of(passed, ["pytest"], merge_failed=[]) == "not_ci"
    assert ci.cause_of(passed, ["pytest"]) is None


def test_every_kind_is_in_the_causes_table() -> None:
    for kind in ("test_failed", "check_failed", "ci_pending", "precondition", "infra_wait", "not_ci"):
        assert causes.row(kind).kind == kind


def test_split_failed() -> None:
    assert ci.split_failed(["pytest (0/2)", "doc-lint"], ["pytest"]) == (["pytest (0/2)"], ["doc-lint"])


@pytest.mark.parametrize(
    ("names", "tests", "expected"),
    [
        ([], ["pytest"], ([], [])),
        (["pytest", "doc-lint"], [], ([], ["pytest", "doc-lint"])),
        (
            ["doc-lint", "pytest", "W / pytest (1/2)", "doc-lint", "pytest"],
            ["CI / pytest (0/3)"],
            (["pytest", "W / pytest (1/2)", "pytest"], ["doc-lint", "doc-lint"]),
        ),
    ],
)
def test_split_failed_keeps_order_and_duplicates(names: list[str], tests: list[str], expected: tuple) -> None:
    """現状固定: 空の入力・重なった名前・ワークフローの前置き・分割ジョブの分け方と並び。"""
    assert ci.split_failed(names, tests) == expected


def test_split_failed_and_read_accept_generators() -> None:
    """現状固定: 名前とテストのチェックをジェネレーターで渡しても、1 度だけ読んで同じに分ける。"""
    names = (n for n in ["pytest (0/2)", "lint", "pytest"])
    assert ci.split_failed(names, (t for t in ["pytest"])) == (["pytest (0/2)", "pytest"], ["lint"])
    runs = [
        run("pytest (0/2)", conclusion="failure", rid=1),
        run("lint", conclusion="failure", rid=2),
        run("pytest", conclusion="failure", rid=3),
    ]
    reading = ci.read(iter(runs), iter(["pytest", "lint"]), (t for t in ["W / pytest"]))
    assert [c.name for c in reading.failed_tests] == ["pytest", "pytest (0/2)"]
    assert [c.name for c in reading.failed_other] == ["lint"]


# --- 落ちたテストの拾い出し（#3188） ---


def test_failed_test_nodes_and_files_from_log() -> None:
    nodes = ci.failed_test_nodes(LOG)
    assert nodes == ["tests/test_other.py", "tests/test_stage_check.py::test_names", "tests/test_stage_check.py::test_order[standard]"]
    assert ci.files_of(nodes) == ["tests/test_other.py", "tests/test_stage_check.py"]


class _Logs:
    def __init__(self) -> None:
        self.jobs: list[int] = []

    def job_log(self, job: int) -> str:
        self.jobs.append(job)
        return f"FAILED tests/test_{job}.py::t - x\n"


def test_failed_test_nodes_of_reads_at_most_the_declared_number_of_jobs() -> None:
    cap = int(limits.value("github.failed-logs", "count"))
    checks = [ci.Check(f"pytest ({i}/9)", "done", "failure", i) for i in range(cap + 3)]
    logs = _Logs()
    nodes = ci.failed_test_nodes_of(logs, checks)  # type: ignore[arg-type]
    assert logs.jobs == list(range(cap))
    assert nodes == sorted(f"tests/test_{i}.py::t" for i in range(cap))


# --- 待ち ---


class Clock:
    """眠りを記録し、時刻を進めるだけの時計。"""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _reader(readings: list[ci.Reading], clock: Clock, reads: list[float]):
    it: Iterator[ci.Reading] = iter(readings)
    last = readings[-1]

    def read() -> ci.Reading:
        reads.append(clock.now)
        return next(it, last)

    return read


def _reading(**kw) -> ci.Reading:
    base = {"failed_tests": [], "failed_other": [], "running": [], "queued": [], "stale": [], "missing": [], "settled": False}
    return ci.Reading(**{**base, **kw})


RUNNING = _reading(running=["pytest"])
QUEUED = _reading(queued=["pytest"])
PASSED = _reading(settled=True)


def test_watch_reads_once_more_at_the_deadline() -> None:
    # #2224: 最後の眠りを上限までの残りに縮め、上限の時刻に最後の 1 回を読む
    clock, reads = Clock(), []
    first = float(limits.policy("test-triage.check-wait").interval)
    budget = first * 2
    done = ci.Watch(_reader([RUNNING], clock, reads), budget, clock=clock, sleep=clock.sleep).run()
    assert reads[-1] == budget
    assert sum(clock.sleeps) == budget
    assert done.cause(["pytest"]) == "ci_pending"


def test_watch_returns_as_soon_as_checks_conclude() -> None:
    clock, reads, seen = Clock(), [], []
    w = ci.Watch(
        _reader([RUNNING, PASSED], clock, reads), 3600, on_round=lambda r, t: seen.append((r.verdict, t)), clock=clock, sleep=clock.sleep
    )
    done = w.run()
    assert done.rounds == 2 and done.cause(["pytest"]) is None
    # #2317: 読むたびに、待っているものと経過を渡す
    assert [v for v, _ in seen] == ["pending", "passed"]


def test_watch_does_not_count_runner_wait_against_budget() -> None:
    # #2719: ランナーの割り当てを待つ間は予算に数えない
    clock, reads = Clock(), []
    readings = [QUEUED] * 6 + [RUNNING, PASSED]
    done = ci.Watch(_reader(readings, clock, reads), 5, clock=clock, sleep=clock.sleep).run()
    assert done.cause(["pytest"]) is None
    assert done.queued_for > 5


def test_watch_remaining_does_not_shrink_while_waiting_for_a_runner() -> None:
    # API の上限の待ちの締め切りは Watch の予算と同じ数え方: ランナー待ちが予算の秒を越えても残りは減らない
    clock, seen = Clock(), []
    w = ci.Watch(_reader([QUEUED] * 6 + [RUNNING, PASSED], clock, []), 5, clock=clock, sleep=clock.sleep)
    w.on_round = lambda r, t: seen.append((clock.now, w.remaining()))
    w.run()
    queued_rounds = seen[1:6]
    assert queued_rounds[-1][0] > 5
    assert all(left == pytest.approx(5) for _, left in queued_rounds)


def test_watch_stops_with_infra_wait_when_runner_wait_exceeds_its_limit() -> None:
    clock, reads = Clock(), []
    done = ci.Watch(_reader([QUEUED], clock, reads), 5, clock=clock, sleep=clock.sleep).run()
    assert done.runner_exhausted and done.cause(["pytest"]) == "infra_wait"
    assert done.queued_for == pytest.approx(limits.seconds("ci.runner-wait"))
