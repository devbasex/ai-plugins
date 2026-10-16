"""耐久の記録の包み（lib/durable.py・#1142 の決定 26〜28・34）。外部パッケージは全体テストの環境（根の pyproject.toml）が入れる。

耐久の記録の置き場は根の conftest.py がテストごとの一時ディレクトリ（`NDF_DBOS_DIR`）へ向ける。
落ちた後の続きは、別のプロセスで耐久ワークフローを流して `kill -9` し、同じ起動を打ち直して確かめる。
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import dbos  # noqa: E402,F401  包みの外部パッケージ。無ければ集めるところで落とす
import durable  # noqa: E402
import locks  # noqa: E402

# 別のプロセスで流す耐久ワークフロー。引数: モード・記録のファイル・（形式の版）
CHILD = textwrap.dedent(
    """
    import json, os, sys, time
    sys.path.insert(0, {lib!r})
    import durable

    mode, log = sys.argv[1], sys.argv[2]
    if len(sys.argv) > 3:
        durable.FORMAT = sys.argv[3]

    def note(text):
        with open(log, "a") as f:
            f.write(text + "\\n")

    @durable.step
    def s(i):
        note(f"start {{i}}")
        if i == 2 and mode in ("slow", "parent"):
            time.sleep(120)
        note(f"end {{i}}")
        return i

    @durable.workflow
    def plan(n):
        return [s(i) for i in range(n)]

    @durable.workflow
    def child(i):
        return s(i)

    @durable.workflow
    def parent(n):
        wid = durable.submit("res", child, 2, id=durable.current_id() + "-t1")
        return [s(0), durable.output_of(wid)]

    @durable.workflow
    def paused():
        a = s(0)
        msg = durable.pause(1, result={{"stage": "fix"}}, code=10)
        return [a, msg["seq"], msg.get("note")]

    if mode in ("slow", "fast"):
        durable.launch("run", "ident")
        ref = durable.resolve("plan-x")
        print(json.dumps({{"action": ref.action, "id": ref.id}}), flush=True)
        print(json.dumps(durable.output_of(durable.start(ref, plan, 4))), flush=True)
    elif mode == "parent":
        durable.launch("run", "ident", queues={{"res": {{"concurrency": 1}}}})
        durable.start(durable.resolve("plan-old"), parent, 1)
        time.sleep(120)
    elif mode == "keep":
        durable.launch("run", "ident", queues={{"res": {{"concurrency": 1}}}}, keep="plan-new")
        time.sleep(2)
        print(json.dumps({{w: durable.status(w) for w in durable.workflow_ids("plan-old")}}), flush=True)
    elif mode == "pause":
        durable.launch("drive", "ident")
        wid = durable.start(durable.resolve("review-x"), paused)
        print(json.dumps(durable.wait(wid, "pause").value), flush=True)
        durable.exit_leaving_pending(10)
    elif mode == "resume":
        durable.launch("drive", "ident")
        ref = durable.resolve("review-x")
        seen = durable.event(ref.id)
        durable.resume(ref, seen["seq"], note="written")
        print(json.dumps({{"action": ref.action, "out": durable.output_of(ref.id)}}), flush=True)
    elif mode == "hold":
        durable.launch("run", "ident")
        print("held", flush=True)
        time.sleep(120)
    elif mode == "ports":
        import psutil

        durable.launch("run", "ports", queues={{"plans": {{"worker_concurrency": 2}}}})
        time.sleep(0.5)
        conns = psutil.Process().net_connections(kind="inet")
        print(json.dumps([c.laddr.port for c in conns if c.status == psutil.CONN_LISTEN]), flush=True)
    elif mode == "second":
        try:
            durable.launch("run", "ident", lock_timeout=0.5)
        except durable.DurableError as exc:
            print("locked", exc, flush=True)
    durable.exit_leaving_pending(0)
    """
).format(lib=str(LIB))


@pytest.fixture
def child(tmp_path: Path):
    script = tmp_path / "child.py"
    script.write_text(CHILD)
    log = tmp_path / "steps.log"

    def run(mode: str, *extra: str, timeout: float = 60) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(script), mode, str(log), *extra], capture_output=True, text=True, timeout=timeout, env=os.environ.copy()
        )

    def spawn(mode: str, *extra: str) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, str(script), mode, str(log), *extra],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=os.environ.copy(),
        )

    run.spawn = spawn  # type: ignore[attr-defined]
    run.log = log  # type: ignore[attr-defined]
    return run


@pytest.fixture
def opened():
    """このプロセスで耐久の記録を開くテストの後始末。"""
    yield
    durable.close()


def lines(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


def wait_for(cond, timeout: float = 30.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise AssertionError("待ちの上限を過ぎた")
        time.sleep(0.05)


def kill(proc: subprocess.Popen) -> None:
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=10)


# ---- 置き場と鍵 ----


def test_records_dir_follows_the_env_then_xdg_then_home():
    assert durable.records_dir({"NDF_DBOS_DIR": "/x/d", "XDG_STATE_HOME": "/s", "HOME": "/h"}) == Path("/x/d")
    assert durable.records_dir({"XDG_STATE_HOME": "/s", "HOME": "/h"}) == Path("/s/ndf/dbos")
    assert durable.records_dir({"HOME": "/h"}) == Path("/h/.local/state/ndf/dbos")


def test_the_tests_write_records_under_a_temporary_directory():
    assert Path(os.environ["NDF_DBOS_DIR"]) == durable.records_dir()
    assert Path.home() / ".local" / "state" not in durable.records_dir().parents


def test_run_key_is_kind_and_the_first_12_chars_of_sha256():
    key = durable.launch_key("run", "/p/plan.json")
    assert key.startswith("run-") and len(key) == len("run-") + 12
    assert key == durable.launch_key("run", "/p/plan.json") != durable.launch_key("run", "/p/other.json")
    assert durable.record_path("queue", "d") == durable.records_dir() / f"{durable.launch_key('queue', 'd')}.sqlite"
    with pytest.raises(durable.DurableError):
        durable.launch_key("Run/x", "i")


# ---- 起動・実行の回 ----


@durable.step
def double(x: int) -> int:
    CALLS.append(x)
    return x * 2


@durable.workflow
def twice(x: int) -> list:
    return [double(x), double(x + 1)]


@durable.workflow
def waits_for_resume() -> list:
    first = durable.pause(1, result="stop")
    second = durable.pause(2, result="again")
    return [first["seq"], second["seq"], second.get("note")]


CALLS: list[int] = []


def test_resolve_starts_run_1_then_returns_the_finished_output_without_running_again(opened):
    CALLS.clear()
    got = durable.launch("run", "same")
    assert got.path == durable.records_dir() / f"{got.key}.sqlite" and got.path.exists()
    ref = durable.resolve("plan-a")
    assert (ref.id, ref.number, ref.action) == ("plan-a-1", 1, "start")
    assert durable.output_of(durable.start(ref, twice, 1)) == [2, 4]
    again = durable.resolve("plan-a", finished=lambda out: out == [2, 4])
    assert (again.id, again.action, again.output) == ("plan-a-1", "done", [2, 4])
    assert durable.start(again, twice, 1) == "plan-a-1"
    assert CALLS == [1, 2]
    fresh = durable.resolve("plan-a", finished=lambda out: False)
    assert (fresh.id, fresh.number, fresh.action) == ("plan-a-2", 2, "start")
    assert durable.resolve("plan-a-other").id == "plan-a-other-1"


def test_one_process_opens_one_record(opened):
    durable.launch("run", "one")
    with pytest.raises(durable.DurableError):
        durable.launch("run", "two")


def test_a_second_launch_in_one_process_does_not_flush_a_closed_log_stream(opened):
    """DBOS のログの出力先が前の起動の（閉じた）標準エラーに残っても、開き直しは落ちない（テストの capsys の形）。"""
    durable.launch("run", "first")
    durable.close()
    closed = open(os.devnull, "w")
    closed.close()
    for h in logging.getLogger("dbos").handlers:
        if h.name == "__dbos_console_log_handler__":
            h.stream = closed
    assert durable.launch("run", "second").key == durable.launch_key("run", "second")


def test_no_port_is_listened_while_launched(child):
    pytest.importorskip("psutil")
    out = child("ports")
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == []
    assert list(durable.records_dir().glob("run-*.sqlite"))


def test_enqueue_runs_on_a_registered_queue_and_an_empty_listen_leaves_it_queued(opened):
    durable.launch("posts", "q1", queues={"posts": {"worker_concurrency": 1}})
    assert durable.output_of(durable.submit("posts", twice, 3, id="post-0001")) == [6, 8]
    with pytest.raises(durable.DurableError):
        durable.submit("nope", twice, 1)
    durable.close()
    durable.launch("posts", "q2", queues={"posts": {"worker_concurrency": 1}}, listen=[])
    wid = durable.submit("posts", twice, 5, id="post-0002")
    time.sleep(1.0)
    assert durable.status(wid) == "ENQUEUED"


def test_pause_sets_an_event_and_resume_continues_past_stale_messages(opened):
    durable.launch("review", "p")
    wid = durable.start(durable.resolve("review-p"), waits_for_resume)
    first = durable.wait(wid, "pause", timeout=30)
    assert first == durable.Outcome("event", {"seq": 1, "result": "stop"})
    durable.resume(wid, 1)
    assert durable.wait(wid, "pause", after=1, timeout=30).value == {"seq": 2, "result": "again"}
    durable.resume(wid, 1)  # 前の止まりへの 2 度目の続きは数えない
    durable.resume(wid, 2, note="done")
    assert durable.wait(wid, "pause", after=2, timeout=30) == durable.Outcome("done", [1, 2, "done"])


# ---- 落ちた後の打ち直し ----


def test_kill_9_then_the_same_command_continues_from_the_running_step(child):
    proc = child.spawn("slow")
    wait_for(lambda: "start 2" in lines(child.log))
    kill(proc)
    out = child("fast")
    assert out.returncode == 0, out.stderr
    action, result = (json.loads(x) for x in out.stdout.splitlines())
    assert action == {"action": "continue", "id": "plan-x-1"}
    assert result == [0, 1, 2, 3]
    log = lines(child.log)
    assert log.count("start 0") == 1 and log.count("start 1") == 1  # 済んだ耐久ステップは流れない
    assert log.count("start 2") == 2 and log.count("end 3") == 1


def test_a_record_of_another_format_is_not_continued(child):
    proc = child.spawn("slow", "ndf-durable-0")
    wait_for(lambda: "start 2" in lines(child.log))
    kill(proc)
    out = child("fast")
    assert out.returncode == 0, out.stderr
    action, result = (json.loads(x) for x in out.stdout.splitlines())
    assert action == {"action": "start", "id": "plan-x-2"}
    assert result == [0, 1, 2, 3]
    assert "形式の版 ndf-durable-0" in out.stderr
    assert lines(child.log).count("start 0") == 2  # 実行の回 2 を頭から流す


def test_keep_cancels_other_pending_workflows_and_their_children_before_launch(child):
    proc = child.spawn("parent")
    wait_for(lambda: lines(child.log).count("end 0") == 1 and "start 2" in lines(child.log))
    kill(proc)
    before = list(lines(child.log))
    out = child("keep")
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.splitlines()[-1]) == {"plan-old-1": "CANCELLED", "plan-old-1-t1": "CANCELLED"}
    assert lines(child.log) == before  # 古い耐久ステップは 1 つも流れない


def test_a_paused_workflow_survives_the_exit_and_resumes_in_the_next_process(child):
    first = child("pause")
    assert first.returncode == 10, first.stderr
    assert json.loads(first.stdout) == {"seq": 1, "result": {"stage": "fix"}, "code": 10}
    second = child("resume")
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout) == {"action": "continue", "out": [0, 1, "written"]}
    assert lines(child.log).count("start 0") == 1


def test_a_second_launch_of_the_same_key_waits_for_the_lock(child):
    holder = child.spawn("hold")
    try:
        assert holder.stdout.readline().strip() == "held"
        out = child("second")
        assert out.stdout.startswith("locked") and "同じ実行の鍵" in out.stdout
    finally:
        kill(holder)


# ---- 古い記録の削除 ----


def test_purge_old_removes_unused_records_older_than_30_days(tmp_path: Path):
    now = time.time()
    old = 31 * 86400

    def make(key: str, age: float) -> list[Path]:
        files = [tmp_path / f"{key}.sqlite", tmp_path / f"{key}.sqlite-wal", tmp_path / f"{key}.sqlite-shm"]
        for f in files:
            f.write_text("x")
            os.utime(f, (now - age, now - age))
        locks.lock_path(tmp_path / key).write_text("")
        return files

    stale, fresh, busy, mine = make("run-a", old), make("run-b", 86400), make("run-c", old), make("run-d", old)
    with locks.exclusive(tmp_path / "run-c", timeout=1):
        removed = durable.purge_old(tmp_path, skip=["run-d"], now=now)
    assert removed == ["run-a"]
    assert not any(f.exists() for f in stale) and not locks.lock_path(tmp_path / "run-a").exists()
    assert all(f.exists() for f in fresh + busy + mine)


# ---- 2 つの drive.py の Drive.run が頼る起動の形（現状固定） ----


def test_review_key_built_from_key_hash_equals_launch_key():
    """cross-review の Drive.run は `review-{key_hash}`、cross-refactoring は `launch_key` で鍵を作る。どちらも同じ鍵になる。"""
    identity = "/tmp/x/.cross_review"
    assert f"review-{durable.key_hash(identity)}" == durable.launch_key("review", identity)


def test_launched_path_matches_record_path_of_the_same_kind_and_identity(opened):
    """cross-refactoring の Drive.run は開いた記録の置き場を `record_path` と比べて開き直すかを決める。"""
    got = durable.launch("refactor", "same-id")
    assert durable.launched().path == got.path == durable.record_path("refactor", "same-id")
    assert durable.launched().path != durable.record_path("refactor", "other-id")


def test_continue_reads_the_event_seq_and_resume_accepts_a_ref_or_an_id(opened):
    """止まった耐久ワークフローを resolve すると continue になり、event の seq から続きを送る（ref でも ID でも同じ）。"""
    durable.launch("review", "cont")
    ref = durable.resolve("review-cont")
    wid = durable.start(ref, waits_for_resume)
    assert wid == ref.id
    assert durable.wait(wid, "pause", timeout=30) == durable.Outcome("event", {"seq": 1, "result": "stop"})
    again = durable.resolve("review-cont")
    assert (again.id, again.action) == (wid, "continue")
    assert durable.start(again, waits_for_resume) == wid
    seen = durable.event(again.id)
    assert isinstance(seen, dict) and int(seen["seq"]) == 1
    durable.resume(again, 1)
    assert durable.wait(wid, "pause", after=1, timeout=30).value == {"seq": 2, "result": "again"}
    durable.resume(wid, 2, note="done")
    assert durable.wait(wid, "pause", after=2, timeout=30) == durable.Outcome("done", [1, 2, "done"])


def test_resume_paused_sends_the_seq_on_continue_and_returns_0_on_start(opened):
    durable.launch("review", "paused")
    ref = durable.resolve("review-paused")
    assert durable.resume_paused(ref) == 0
    wid = durable.start(ref, waits_for_resume)
    assert durable.wait(wid, "pause", timeout=30).value == {"seq": 1, "result": "stop"}
    again = durable.resolve("review-paused")
    assert again.action == "continue" and durable.resume_paused(again) == 1
    assert durable.wait(wid, "pause", after=1, timeout=30).value == {"seq": 2, "result": "again"}
    durable.resume(wid, 2)
    assert durable.wait(wid, "pause", after=2, timeout=30).kind == "done"
