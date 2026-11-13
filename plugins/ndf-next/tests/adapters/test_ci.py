"""CI の結果の読み取りと落ちたチェックの分類（`ndf.adapters.ci`）。"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from githubkit import GitHub as Client

from ndf.adapters import ci, github
from ndf.contracts import causes, limits

HEAD = "2bb66a6c14a618cbac108b61737ad49cfb1ac48b"
REPO = "devbasex/ai-plugins"


def run(name: str, status: str = "completed", conclusion: str | None = None, rid: int = 1, **extra) -> dict:
    """REST の check run 1 件。終わったものは既定で success と completed_at を持つ。"""
    done = status == "completed"
    row = {
        "id": rid, "name": name, "status": status, "conclusion": "success" if done and conclusion is None else conclusion,
        "started_at": "2026-11-12T13:30:00Z", "completed_at": "2026-11-12T13:33:00Z" if done else None,
    }  # fmt: skip
    return {**row, **extra}


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


# --- 落ちたテストの拾い出し（#3188） ---

LOG = """2026-11-12T13:33:01.1Z ============ short test summary info ============
2026-11-12T13:33:01.2Z FAILED tests/test_stage_check.py::test_order[standard] - AssertionError: x
2026-11-12T13:33:01.3Z FAILED tests/test_stage_check.py::test_names - AssertionError
2026-11-12T13:33:01.4Z ERROR tests/test_other.py - ImportError
2026-11-12T13:33:01.5Z FAILED tests/test_stage_check.py::test_names - AssertionError
"""


def test_failed_tests_and_files_from_log() -> None:
    nodes = ci.failed_tests(LOG)
    assert nodes == ["tests/test_other.py", "tests/test_stage_check.py::test_names", "tests/test_stage_check.py::test_order[standard]"]
    assert ci.files_of(nodes) == ["tests/test_other.py", "tests/test_stage_check.py"]


class _Logs:
    def __init__(self) -> None:
        self.jobs: list[int] = []

    def job_log(self, job: int) -> str:
        self.jobs.append(job)
        return f"FAILED tests/test_{job}.py::t - x\n"


def test_failed_tests_of_reads_at_most_the_declared_number_of_jobs() -> None:
    cap = int(limits.value("github.failed-logs", "count"))
    checks = [ci.Check(f"pytest ({i}/9)", "done", "failure", i) for i in range(cap + 3)]
    logs = _Logs()
    nodes = ci.failed_tests_of(logs, checks)  # type: ignore[arg-type]
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
    w = ci.Watch(_reader([RUNNING, PASSED], clock, reads), 3600, on_round=lambda r, t: seen.append((r.verdict, t)), clock=clock, sleep=clock.sleep)
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


def test_watch_stops_with_infra_wait_when_runner_wait_exceeds_its_limit() -> None:
    clock, reads = Clock(), []
    done = ci.Watch(_reader([QUEUED], clock, reads), 5, clock=clock, sleep=clock.sleep).run()
    assert done.runner_exhausted and done.cause(["pytest"]) == "infra_wait"
    assert done.queued_for == pytest.approx(limits.seconds("ci.runner-wait"))


# --- 副命令 `ndf ci watch` ---


def _serve(monkeypatch: pytest.MonkeyPatch, runs: list[dict], log: str = "") -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/pulls/7"):
            pull = {"number": 7, "state": "open", "merged": False, "body": "", "mergeable_state": "clean", "head": {"sha": HEAD, "ref": "x"}, "base": {"ref": "develop"}}
            return httpx.Response(200, json=pull)
        if path.endswith("/check-runs"):
            return httpx.Response(200, json={"total_count": len(runs), "check_runs": runs})
        if path.endswith("/logs"):
            return httpx.Response(200, text=log, headers={"content-type": "text/plain"})
        return httpx.Response(404, json={"message": "not found"})

    api = Client("t", transport=httpx.MockTransport(handler), http_cache=False, auto_retry=False)
    monkeypatch.setattr(github, "client", lambda: api)


ARGS = ["watch", "--pr", "7", "--repo", REPO, "--required", "pytest", "--required", "doc-lint", "--test", "pytest", "--budget", "60"]


def _out(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_cli_watch_test_failure_lists_test_files(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    runs = [run("pytest (0/2)", conclusion="failure", rid=41), run("pytest", status="in_progress", rid=42), run("doc-lint", rid=43)]
    _serve(monkeypatch, runs, log=LOG)
    code = ci.main(ARGS)
    out = _out(capsys)
    assert code == causes.exit_code("test_failed")
    assert (out["status"], out["cause"]["kind"], out["cause"]["instance"]) == ("stopped", "test_failed", HEAD)
    assert out["metrics"]["test_files"] == ["tests/test_other.py", "tests/test_stage_check.py"]
    assert out["items"][0] == {"kind": "check", "name": "pytest (0/2)", "result": "failure", "job": 41, "test": True}


def test_cli_watch_passes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _serve(monkeypatch, [run("pytest", rid=1), run("doc-lint", rid=2)])
    assert ci.main(ARGS) == 0
    assert _out(capsys)["status"] == "ok"


def test_cli_watch_after_merge_with_failed_shard(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _serve(monkeypatch, [run("pytest", rid=1), run("doc-lint", rid=2)])
    assert ci.main([*ARGS, "--after-merge", "--merge-failed", "pytest (1/2)"]) == causes.exit_code("test_failed")
    assert _out(capsys)["cause"]["kind"] == "test_failed"


def test_cli_watch_after_merge_without_failures_is_not_ci(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _serve(monkeypatch, [run("pytest", rid=1), run("doc-lint", rid=2)])
    assert ci.main([*ARGS, "--after-merge"]) == causes.exit_code("not_ci")
    assert _out(capsys)["cause"]["kind"] == "not_ci"


def test_cli_watch_rate_limit_is_infra_wait(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def limited() -> Client:
        headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"}
        handler = lambda request: httpx.Response(403, json={"message": "rate"}, headers=headers)  # noqa: E731
        return Client("t", transport=httpx.MockTransport(handler), http_cache=False, auto_retry=False)

    monkeypatch.setattr(github, "client", limited)
    assert ci.main(ARGS) == causes.exit_code("infra_wait")
    out = _out(capsys)
    assert out["cause"]["kind"] == "infra_wait" and out["cause"]["retry_after"] > 0
