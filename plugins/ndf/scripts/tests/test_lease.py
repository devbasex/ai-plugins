"""持ち主の部品（lib/lease.py）と実行の核の持ち主の段（lib/lease_stage.py）（#2342）。

終わり方（正常・例外・終了コード・SIGTERM・時間切れ・SIGKILL）ごとの記録、pid の使い回し、取り合い、祖先からの継承、
古い記録の削除、記録の形、ランタイムのセッションの外を確かめる。入口のプロセスの振る舞いは、契約を差し替えた小さな
入口（`DRIVER`）をサブプロセスで起動して確かめる。
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))

import lease  # noqa: E402
import procs  # noqa: E402
import lease_stage  # noqa: E402
import run_core  # noqa: E402

KEY = "test:pr={pr}"
REPO = "o--r"

# 契約を差し替えた入口。mode で終わり方を選ぶ。結果の JSON を標準出力へ出す（stdout は step-result）
DRIVER = textwrap.dedent(
    """
    import os, signal, subprocess, sys, time
    sys.path.insert(0, {lib!r})
    import lease, lease_stage, run_core, step_result
    lease.lease_repo = lambda start=None: {repo!r}
    decl = {{"key": {key!r}, "unit": "run", "window_seconds": 60}}
    run_core.contract_of = lambda e: {{"stdout": "step-result", "exit": {{"0": "x", "1": "x", "3": "x", "4": "x"}}, "lease": {{"": decl}}}}
    mode, marker = sys.argv[1], sys.argv[2]

    def body():
        out = lease_stage.hold("", pr=7)
        open(marker, "w").write(out.outcome)
        if mode == "child":
            subprocess.run([sys.executable, __file__, "plain", marker + ".child"], check=True)
        if mode == "raise":
            raise RuntimeError("boom")
        if mode == "exit4":
            raise SystemExit(4)
        if mode in ("term", "kill", "hold"):
            open(marker + ".ready", "w").close()
            time.sleep(30)
        if mode == "slow":
            time.sleep(30)
        step_result.emit(step_result.result("drv", "ok", "done"))

    if mode == "slow":
        run_core.main("", body)
    body()
    """
)


@pytest.fixture
def driver(tmp_path):
    path = tmp_path / "drv.py"
    path.write_text(DRIVER.format(lib=str(LIB), repo=REPO, key=KEY), encoding="utf-8")

    def run(mode: str, *, wait: bool = True, env: dict | None = None):
        marker = tmp_path / f"{mode}-{time.monotonic_ns()}.out"
        argv = [sys.executable, str(path), mode, str(marker)]
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env={**os.environ, **(env or {})})
        if wait:
            out, err = p.communicate(timeout=60)
            return p.returncode, out, err, marker
        return p, marker

    return run


def record() -> dict | None:
    return lease.read_lease("test:pr=7", REPO)


def ready(marker: Path) -> None:
    deadline = time.monotonic() + 20
    while not Path(f"{marker}.ready").exists():
        assert time.monotonic() < deadline, "入口が持ち主の記録を取らないまま止まった"
        time.sleep(0.05)


def write(pid: int, started: float, *, finished: bool = False, host: str | None = None, seen: str = "2026-11-08T00:00:00Z") -> None:
    rec = {"version": 1, "key": "test:pr=7", "entry": "t", "run": "old", "started_at": seen, "seen_at": seen}
    rec.update(finished_at=seen if finished else None, pid=pid, pid_started=started, host=host or socket.gethostname())
    lease._store(lease.path_of("test:pr=7", REPO), rec)


@pytest.fixture
def sleeper():
    p = subprocess.Popen(["sleep", "60"])
    try:
        yield p.pid, procs.start_time(p.pid)
    finally:
        p.kill()
        p.wait()


# ---------------- 終わり方（受け入れ条件 1・I3） ----------------


@pytest.mark.parametrize("mode, code", [("plain", 0), ("raise", 1), ("exit4", 4)])
def test_every_end_writes_the_finished_time_and_the_next_run_starts(driver, mode, code) -> None:
    """正常・例外・打ち切りの終了コード（#2321 の着手前のテストの打ち切り）のどれでも終わった時刻が書かれ、次が取れる。"""
    got, _, err, marker = driver(mode)
    assert got == code, err
    assert marker.read_text() == "acquired" and record()["finished_at"]

    got, out, err, marker = driver("plain")
    assert got == 0, err
    assert marker.read_text() == "acquired" and json.loads(out)["lease"]["outcome"] == "acquired"


def test_sigterm_writes_the_finished_time(driver) -> None:
    p, marker = driver("term", wait=False)
    ready(marker)
    p.send_signal(signal.SIGTERM)
    p.communicate(timeout=30)
    assert record()["finished_at"]


def test_the_core_timeout_writes_the_finished_time(driver) -> None:
    """時間切れ（核の NDF_RUN_TIMEOUT）は核の finally を通って返す。"""
    got, out, err, _ = driver("slow", env={"NDF_RUN_TIMEOUT": "2"})
    assert got == 1, err
    assert json.loads(out)["gaps"][0]["kind"] == "timeout" and record()["finished_at"]


def test_sigkill_leaves_a_record_the_next_run_reclaims(driver) -> None:
    """受け入れ条件 2: SIGKILL で終わった持ち主の記録は終わった時刻が無いまま残り、次の実行が引き取る。"""
    p, marker = driver("kill", wait=False)
    ready(marker)
    p.kill()
    p.communicate(timeout=30)
    assert record()["finished_at"] is None

    got, out, err, _ = driver("plain")
    assert got == 0, err
    assert json.loads(out)["lease"]["outcome"] == "reclaimed" and json.loads(out)["lease"]["holder_pid"] == p.pid


# ---------------- 生死の判定（受け入れ条件 2・I4） ----------------


def test_a_reused_pid_is_a_dead_holder(sleeper) -> None:
    """pid が生きていても開始時刻が違えば、使い回された pid として死んだ持ち主に扱う。"""
    write(sleeper[0], sleeper[1] - 100)
    out = lease.acquire(lease.Decl(KEY, "run", 60), "test:pr=7", "t", "new", REPO)
    assert out.outcome == "reclaimed" and record()["pid"] == os.getpid()


def test_a_live_holder_is_refused_with_its_fields(sleeper) -> None:
    """受け入れ条件 3: 生きた持ち主がいれば例外でなく refused を返し、持ち主の 3 項目を添える。記録は変えない。"""
    write(*sleeper)
    before = lease.path_of("test:pr=7", REPO).read_text()
    out = lease.acquire(lease.Decl(KEY, "run", 60), "test:pr=7", "t", "new", REPO)
    assert out.as_json() == {
        "key": "test:pr=7",
        "outcome": "refused",
        "holder_run": "old",
        "holder_pid": sleeper[0],
        "holder_seen_at": "2026-11-08T00:00:00Z",
    }
    assert lease.path_of("test:pr=7", REPO).read_text() == before


@pytest.mark.parametrize("seen, outcome", [("2000-01-01T00:00:00Z", "reclaimed"), (None, "refused")])
def test_another_host_is_judged_by_the_window(seen, outcome) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write(1, 1.0, host="elsewhere", seen=seen or now)
    assert lease.acquire(lease.Decl(KEY, "run", 3600), "test:pr=7", "t", "new", REPO).outcome == outcome


def test_release_and_touch_write_only_for_the_holder(sleeper) -> None:
    write(*sleeper)
    before = lease.path_of("test:pr=7", REPO).read_text()
    me = (os.getpid(), procs.start_time(os.getpid()))
    assert not lease.release_lease("test:pr=7", me, REPO) and not lease.touch_lease("test:pr=7", me, REPO)
    assert lease.path_of("test:pr=7", REPO).read_text() == before
    assert lease.release_lease("test:pr=7", sleeper, REPO) and record()["finished_at"]


# ---------------- 取り合い（受け入れ条件 4・5・I1・I2） ----------------


def test_the_second_process_is_refused_with_code_3(driver) -> None:
    first, marker = driver("hold", wait=False)
    try:
        ready(marker)
        got, out, err, _ = driver("plain")
        res = json.loads(out)
        assert got == 3, err
        assert res["status"] == "stopped" and res["lease"]["outcome"] == "refused" and res["lease"]["holder_pid"] == first.pid
        assert all(w not in res["next"] for w in ("--run", "finish", "kill"))
    finally:
        first.kill()
        first.communicate(timeout=30)


def test_many_processes_at_once_leave_one_holder(driver) -> None:
    runs = [driver("hold", wait=False) for _ in range(6)]
    try:
        deadline = time.monotonic() + 30
        while sum(p.poll() is not None for p, _ in runs) < 5:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        alive = [(p, m) for p, m in runs if p.poll() is None]
        assert len(alive) == 1 and alive[0][1].read_text() == "acquired"
        assert sorted(p.returncode for p, _ in runs if p.poll() is not None) == [3] * 5
    finally:
        for p, _ in runs:
            p.kill()
            p.communicate(timeout=30)


# ---------------- 祖先からの継承（I6） ----------------


def test_a_child_inherits_the_key_of_its_ancestor_and_does_not_release(driver) -> None:
    got, _, err, marker = driver("child")
    assert got == 0, err
    assert Path(f"{marker}.child").read_text() == "inherited"
    assert record()["finished_at"] and record()["pid"] != 0  # 返したのは親（持ち主）の終わり


# ---------------- 削除・形・セッションの外 ----------------


def test_prune_removes_only_old_finished_records_and_keeps_the_locks() -> None:
    write(1, 1.0, finished=True, seen="2000-01-01T00:00:00Z")
    path = lease.path_of("test:pr=7", REPO)
    with lease.locks.exclusive(path):
        pass
    lease._store(lease.path_of("test:pr=8", REPO), {**json.loads(path.read_text()), "key": "test:pr=8", "finished_at": None})

    removed = lease.prune(30)

    assert removed == [str(path)] and not path.exists() and lease.locks.lock_path(path).exists()
    assert lease.read_lease("test:pr=8", REPO) is not None


def test_a_record_with_extra_keys_is_not_written() -> None:
    """受け入れ条件 11・I5: 記録が持つのは決めた鍵だけ（引数の並びや秘密を持たない）。"""
    with pytest.raises(lease.LeaseError):
        lease._store(lease.path_of("test:pr=7", REPO), {**dict.fromkeys(lease.FIELDS, ""), "argv": ["--token", "x"]})
    assert record() is None


def test_no_session_stops_a_taking_subcommand_and_lets_a_releasing_one_run(monkeypatch) -> None:
    """受け入れ条件 6: ランタイムのセッションの外では、鍵を取る副命令は stopped・1、返す副命令は本体を走らせる。"""
    monkeypatch.delenv(lease.RUNTIME_SESSION_ENV)
    monkeypatch.setattr(lease.procs, "runtime_ancestor", lambda: None)
    decls = {"loop start": {"key": "loop:name={name}", "unit": "session", "window_seconds": 60}}
    decls["loop stop"] = {**decls["loop start"], "release": True}
    monkeypatch.setattr(run_core, "contract_of", lambda e: {"stdout": "json", "lease": decls})
    monkeypatch.setattr(lease_stage, "_hook_standalone", lambda e: None)
    monkeypatch.setattr(lease_stage, "_HELD", [])

    with pytest.raises(SystemExit) as e:
        lease_stage.hold("x", "loop start", name="l1")
    assert e.value.code == 1
    assert lease_stage.hold("x", "loop stop", name="l1").outcome == "no_session"
    assert not lease.root().exists()


def test_the_key_repo_comes_from_the_given_root(monkeypatch, tmp_path) -> None:
    """鍵のリポジトリは入口の --root（repo_root）から決め、作業ディレクトリから決めない。"""
    seen: list = []
    monkeypatch.setattr(lease, "lease_repo", lambda start=None: seen.append(start) or REPO)
    monkeypatch.setattr(
        run_core, "contract_of", lambda e: {"stdout": "json", "lease": {"": {"key": KEY, "unit": "run", "window_seconds": 60}}}
    )
    monkeypatch.setattr(lease_stage, "_hook_standalone", lambda e: None)
    monkeypatch.setattr(lease_stage, "_HELD", [])

    assert lease_stage.hold("x", repo_root=tmp_path, pr=7).repo == REPO
    assert seen == [tmp_path]
    lease_stage.release_held()
