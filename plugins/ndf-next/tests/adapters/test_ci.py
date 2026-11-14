"""CI の結果の読み取りと落ちたチェックの分類（`ndf.adapters.ci`）。"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from githubkit import GitHub as Client

from ndf import ci_cli
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

LOG = """2026-11-12T13:33:01.1Z ============ short test summary info ============
2026-11-12T13:33:01.2Z FAILED tests/test_stage_check.py::test_order[standard] - AssertionError: x
2026-11-12T13:33:01.3Z FAILED tests/test_stage_check.py::test_names - AssertionError
2026-11-12T13:33:01.4Z ERROR tests/test_other.py - ImportError
2026-11-12T13:33:01.5Z FAILED tests/test_stage_check.py::test_names - AssertionError
"""


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


def test_watch_counts_budget_from_the_given_start() -> None:
    # 3394-11: 前の段階で待ち始めた呼び手は始まりの時刻を渡し、同じ予算を引き継ぐ
    clock, reads = Clock(), []
    clock.now = 100.0
    w = ci.Watch(_reader([RUNNING], clock, reads), 30, clock=clock, sleep=clock.sleep, started=80.0)
    assert w.started == 80.0 and w.remaining() == pytest.approx(10)
    done = w.run()
    assert reads[-1] == pytest.approx(110) and done.elapsed == pytest.approx(30)


def test_watch_start_defaults_to_construction_time() -> None:
    clock = Clock()
    clock.now = 42.0
    assert ci.Watch(_reader([RUNNING], clock, []), 5, clock=clock, sleep=clock.sleep).started == 42.0


def test_watch_stops_with_infra_wait_when_runner_wait_exceeds_its_limit() -> None:
    clock, reads = Clock(), []
    done = ci.Watch(_reader([QUEUED], clock, reads), 5, clock=clock, sleep=clock.sleep).run()
    assert done.runner_exhausted and done.cause(["pytest"]) == "infra_wait"
    assert done.queued_for == pytest.approx(limits.seconds("ci.runner-wait"))


# --- 副命令 `ndf ci watch` ---


def _serve(monkeypatch: pytest.MonkeyPatch, runs: list[dict], log: str = "", log_status: int = 200, calls: list[str] | None = None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if calls is not None:
            calls.append(path)
        if path.endswith("/pulls/7"):
            pull = {
                "number": 7,
                "state": "open",
                "merged": False,
                "body": "",
                "mergeable_state": "clean",
                "head": {"sha": HEAD, "ref": "x"},
                "base": {"ref": "develop"},
            }
            return httpx.Response(200, json=pull)
        if path.endswith("/check-runs"):
            return httpx.Response(200, json={"total_count": len(runs), "check_runs": runs})
        if path.endswith("/logs"):
            return httpx.Response(log_status, text=log, headers={"content-type": "text/plain"})
        return httpx.Response(404, json={"message": "not found"})

    api = Client("t", transport=httpx.MockTransport(handler), http_cache=False, auto_retry=False)
    monkeypatch.setattr(github, "client", lambda **_: api)


ARGS = ["watch", "--pr", "7", "--repo", REPO, "--required", "pytest", "--required", "doc-lint", "--test", "pytest", "--budget", "60"]


def _out(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_cli_watch_test_failure_lists_test_files(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    runs = [run("pytest (0/2)", conclusion="failure", rid=41), run("pytest", status="in_progress", rid=42), run("doc-lint", rid=43)]
    _serve(monkeypatch, runs, log=LOG)
    code = ci_cli.main(ARGS)
    out = _out(capsys)
    assert code == causes.exit_code("test_failed")
    assert (out["status"], out["cause"]["kind"], out["cause"]["instance"]) == ("stopped", "test_failed", HEAD)
    assert out["metrics"]["test_files"] == ["tests/test_other.py", "tests/test_stage_check.py"]
    assert out["items"][0] == {"kind": "check", "name": "pytest (0/2)", "result": "failure", "job": 41, "test": True}


def test_cli_watch_keeps_test_failure_when_logs_cannot_be_read(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    # ログを読めない（権限・Actions 以外のチェック）ときも、test_failed の分類と落ちたチェックの一覧は残す
    runs = [run("pytest (0/2)", conclusion="failure", rid=41), run("pytest", rid=42), run("doc-lint", rid=43)]
    _serve(monkeypatch, runs, log="forbidden", log_status=403)
    assert ci_cli.main(ARGS) == causes.exit_code("test_failed")
    out = _out(capsys)
    assert out["cause"]["kind"] == "test_failed"
    assert (out["metrics"]["failed_tests"], out["metrics"]["test_files"]) == ([], [])
    assert "403" in out["metrics"]["log_error"]
    assert out["items"][0]["name"] == "pytest (0/2)"


def test_cli_watch_passes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _serve(monkeypatch, [run("pytest", rid=1), run("doc-lint", rid=2)])
    assert ci_cli.main(ARGS) == 0
    assert _out(capsys)["status"] == "ok"


def test_cli_watch_after_merge_with_failed_shard(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _serve(monkeypatch, [run("pytest", rid=1), run("doc-lint", rid=2)])
    assert ci_cli.main([*ARGS, "--after-merge", "--merge-failed", "pytest (1/2)"]) == causes.exit_code("test_failed")
    assert _out(capsys)["cause"]["kind"] == "test_failed"


def test_cli_watch_after_merge_with_failed_test_does_not_wait_for_pending(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # マージの段階がテストの落ちを読んでいれば、まとめのチェックが終わっていなくても読み直さずに返す
    calls: list[str] = []
    _serve(monkeypatch, [run("pytest", status="in_progress", rid=1), run("doc-lint", rid=2)], calls=calls)
    assert ci_cli.main([*ARGS, "--after-merge", "--merge-failed", "pytest (1/2)"]) == causes.exit_code("test_failed")
    out = _out(capsys)
    assert (out["cause"]["kind"], out["metrics"]["rounds"]) == ("test_failed", 1)
    assert sum(p.endswith("/check-runs") for p in calls) == 1


def test_cli_watch_after_merge_without_failures_is_not_ci(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _serve(monkeypatch, [run("pytest", rid=1), run("doc-lint", rid=2)])
    assert ci_cli.main([*ARGS, "--after-merge"]) == causes.exit_code("not_ci")
    assert _out(capsys)["cause"]["kind"] == "not_ci"


def test_cli_watch_rate_limit_is_infra_wait(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def limited(**_) -> Client:
        headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"}
        handler = lambda request: httpx.Response(403, json={"message": "rate"}, headers=headers)  # noqa: E731
        return Client("t", transport=httpx.MockTransport(handler), http_cache=False, auto_retry=False)

    monkeypatch.setattr(github, "client", limited)
    assert ci_cli.main(ARGS) == causes.exit_code("infra_wait")
    out = _out(capsys)
    assert out["cause"]["kind"] == "infra_wait" and out["cause"]["retry_after"] > 0


# --- 現状固定（I-008 required_key）: 必須のチェックの照合の鍵と、鍵に当たるチェック ---


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("pytest", "pytest"),
        ("pytest (0/2)", "pytest (0/2)"),
        ("CI / pytest (1/2)", "pytest (1/2)"),
        ("A / B / pytest (1/2)", "pytest (1/2)"),
        ("  pytest  ", "pytest"),
        ("pytest   (10/12)", "pytest   (10/12)"),
        ("pytest(1/2)", "pytest(1/2)"),
        ("pytest (1/2) (0/2)", "pytest (1/2) (0/2)"),
        ("pytest (1/2) extra", "pytest (1/2) extra"),
        ("pytest (a/b)", "pytest (a/b)"),
        ("runtime-smoke (kiro)", "runtime-smoke (kiro)"),
        ("CI/pytest", "CI/pytest"),
        (" / pytest", "pytest"),
        ("CI / ", ""),
        ("", ""),
    ],
)
def test_required_key_current_values(name: str, key: str) -> None:
    """現状固定: 分割を名指しした名前は分割を保ち（前置きだけ外す）、ほかは bare_name と同じ。"""
    assert ci.required_key(name) == key


def test_required_key_matches_named_shard_only_and_bare_name_any_shard() -> None:
    """現状固定: 分割を名指しした鍵は、前置きの有無によらずその分割だけに当たる。名指ししない鍵は全分割とまとめに当たる。"""
    runs = [run("CI / pytest (0/2)", rid=1), run("pytest (1/2)", conclusion="failure", rid=2), run("W / pytest", rid=3)]
    named = ci.read(runs, ["X / pytest (0/2)"], ["pytest"])
    assert (named.verdict, named.missing, named.failed_tests) == ("passed", [], [])
    bare = ci.read(runs, ["pytest"], ["pytest"])
    assert bare.verdict == "test_failed"
    assert [c.name for c in bare.failed_tests] == ["pytest (1/2)"]
    other = ci.read(runs, ["pytest (2/2)"], ["pytest"])
    assert (other.verdict, other.missing) == ("missing", ["pytest (2/2)"])


def test_required_key_dedupes_required_names_by_key() -> None:
    """現状固定: 前置きや分割の違う必須の名前は、同じ鍵に畳まれて 1 つだけ欠けたと挙がる。"""
    reading = ci.read([run("other", rid=1)], ["pytest", "CI / pytest", "pytest (0/2)", "W / pytest (0/2)"], ["pytest"])
    assert reading.missing == ["pytest", "pytest (0/2)"]


# --- 現状固定（I-009 bare_name）: 照合に使う名前（前置きと分割の末尾を外す） ---


@pytest.mark.parametrize(
    ("name", "bare"),
    [
        ("pytest", "pytest"),
        ("pytest (0/2)", "pytest"),
        ("CI / pytest (1/2)", "pytest"),
        ("A / B / pytest (1/2)", "pytest"),
        ("  pytest  ", "pytest"),
        ("pytest   (10/12)", "pytest"),
        ("pytest(1/2)", "pytest"),
        ("pytest (1/2) (0/2)", "pytest (1/2)"),
        ("pytest (1/2) extra", "pytest (1/2) extra"),
        ("pytest (a/b)", "pytest (a/b)"),
        ("runtime-smoke (kiro)", "runtime-smoke (kiro)"),
        ("CI/pytest", "CI/pytest"),
        (" / pytest", "pytest"),
        ("CI / ", ""),
        ("", ""),
    ],
)
def test_bare_name_current_values(name: str, bare: str) -> None:
    """現状固定: 最後の ` / ` より後ろを取り、末尾の分割 `(k/n)` を 1 つだけ外して前後の空白を除く。"""
    assert ci.bare_name(name) == bare


@pytest.mark.parametrize(
    ("name", "bare", "key"),
    [
        # 末尾の空白の後の分割は外さない（空白を除くのは分割を外した後）。現状の値として固定する
        ("pytest (0/2) ", "pytest (0/2)", "pytest (0/2)"),
        ("CI / pytest (1/2)\t", "pytest (1/2)", "pytest (1/2)"),
        ("A / B /  pytest  ", "pytest", "pytest"),
        ("CI / W / pytest (1/2)", "pytest", "pytest (1/2)"),
    ],
)
def test_bare_name_and_required_key_strip_after_removing_prefix(name: str, bare: str, key: str) -> None:
    """現状固定（I-009）: 前置きを最後の ` / ` で外し、bare_name は分割を外してから、required_key は分割を見る前に空白を除く。"""
    assert (ci.bare_name(name), ci.required_key(name)) == (bare, key)


def test_read_matches_required_with_multiple_prefixes_and_spaces() -> None:
    """現状固定（I-009）: 複数の前置きと前後の空白のある必須の名前も、前置きを外した鍵でチェックに当たる。"""
    runs = [run("A / B / pytest (0/2)", rid=1), run("lint ", conclusion="failure", rid=2)]
    reading = ci.read(runs, ["X / Y / pytest (0/2) ", " lint"], ["pytest"])
    assert (reading.verdict, reading.missing, [c.name for c in reading.failed_other]) == ("check_failed", [], ["lint "])


def test_bare_names_is_the_set_of_bare_names() -> None:
    """現状固定: 集合で返し、同じ照合の名前は 1 つに畳む。ジェネレーターも受ける。"""
    assert ci.bare_names(["CI / pytest (0/2)", "pytest", "W / lint"]) == {"pytest", "lint"}
    assert ci.bare_names(n for n in ["pytest (1/2)"]) == {"pytest"}
    assert ci.bare_names([]) == set()


@pytest.mark.parametrize(
    ("name", "tests", "is_test"),
    [
        ("W / pytest (1/2)", ["CI / pytest (0/3)"], True),
        ("pytest", ["pytest (1/2) (0/2)"], False),
        ("pytest (1/2)", ["pytest (1/2) (0/2)"], False),
        ("pytest (1/2) (5/9)", ["pytest (1/2) (0/2)"], True),
        ("CI/pytest", ["pytest"], False),
        ("pytest", [], False),
    ],
)
def test_is_test_check_compares_bare_names_on_both_sides(name: str, tests: list[str], is_test: bool) -> None:
    """現状固定: チェックの名前とテストのチェックの名前の両方を bare_name で揃えてから比べる。"""
    assert ci.is_test_check(name, tests) is is_test


def test_read_classifies_failures_by_bare_name() -> None:
    """現状固定: 落ちたチェックをテストかどうかで分けるときも、両側を bare_name で揃える。"""
    runs = [run("A / pytest (0/2)", conclusion="failure", rid=1), run("A / lint (0/2)", conclusion="failure", rid=2)]
    reading = ci.read(runs, ["pytest", "lint"], ["W / pytest (5/9)"])
    assert [c.name for c in reading.failed_tests] == ["A / pytest (0/2)"]
    assert [c.name for c in reading.failed_other] == ["A / lint (0/2)"]


# --- 現状固定（I-006 ci_cli._items）: missing・stale の行の test は分割ジョブを含むテストのチェックかで決まる ---


def test_cli_items_marks_missing_and_stale_test_checks() -> None:
    reading = _reading(missing=["CI / pytest (1/2)", "doc-lint"], stale=["pytest", "W / lint (0/2)"])
    assert ci_cli._items(reading, ["pytest (0/2)"]) == [
        {"kind": "check", "name": "CI / pytest (1/2)", "result": "missing", "test": True},
        {"kind": "check", "name": "doc-lint", "result": "missing", "test": False},
        {"kind": "check", "name": "pytest", "result": "stale", "test": True},
        {"kind": "check", "name": "W / lint (0/2)", "result": "stale", "test": False},
    ]
