"""進捗ログの alive と step の行の負荷とメモリの値とマシンの上の pytest の数（#2998 の受け入れ条件 1〜4・12）。"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import procs  # noqa: E402
from supervise_lib import engine  # noqa: E402

pytestmark = pytest.mark.usefixtures("issue_bodies")

LOADAVG = "5.35 5.61 5.84 3/1200 4242\n"
MEMINFO = "MemTotal:       32768000 kB\nMemAvailable:   18402304 kB\nSwapTotal:       8388604 kB\nSwapFree:         320512 kB\nHugePages_Total:       0\n"
EXPECTED = {"load": [5.35, 5.61, 5.84], "mem_available_mib": 17971, "swap_total_mib": 8191, "swap_free_mib": 313}


@pytest.fixture
def fake_proc(tmp_path, monkeypatch):
    d = tmp_path / "proc"
    d.mkdir()
    (d / "loadavg").write_text(LOADAVG)
    (d / "meminfo").write_text(MEMINFO)
    monkeypatch.setattr(procs, "PROC_LOADAVG", d / "loadavg")
    monkeypatch.setattr(procs, "PROC_MEMINFO", d / "meminfo")
    return d


def run_plan(tmp_path, steps, **extra):
    plan = {"フェーズ": "試験", "課題": [858], "作業場所": str(tmp_path), "steps": steps, **extra}
    s = engine.Engine(plan, tmp_path / "state")
    return s, s.run()


def rows(s):
    return [json.loads(l) for l in (s.state.dir / "progress.jsonl").read_text().splitlines()]


def pick(host):
    return {k: host[k] for k in EXPECTED if k in host}


def test_alive_and_step_lines_carry_host_values(tmp_path, fake_proc):
    """受け入れ条件 1・3・4・12: alive と step の行に host があり、既存の項目も残る。run の行には載せない。"""
    s, text = run_plan(tmp_path, [{"id": "a", "type": "run", "cmd": "echo x >&2; sleep 0.6", "next": "end"}], report_interval=0.2)
    assert "結果: 完了" in text
    got = rows(s)
    alive = [r for r in got if r["kind"] == "alive"]
    steps = [r for r in got if r["kind"] == "step"]
    assert alive and steps
    for r in [*alive, *steps]:
        assert pick(r["host"]) == EXPECTED
        assert isinstance(r["host"]["pytest"], int) and r["host"]["pytest"] >= 1  # このテストを流す pytest
        assert isinstance(r["host"]["pytest_workers"], int)
    assert {"step", "type", "elapsed", "worker", "last_output"} <= set(alive[-1])
    assert all("host" not in r for r in got if r["kind"] not in ("alive", "step"))


def test_condition_step_line_carries_host(tmp_path, fake_proc):
    """受け入れ条件 3: 実行の条件の行（kind: step）にも同じ値が載る。"""
    s, _ = run_plan(tmp_path, [{"id": "a", "type": "run", "cmd": "true", "next": "end"}], **{"実行の条件": {"cmd": "exit 0"}})
    cond = [r for r in rows(s) if r["kind"] == "step" and r["step"] == "実行の条件"]
    assert cond and pick(cond[0]["host"]) == EXPECTED


def test_unreadable_proc_omits_values_and_keeps_writing(tmp_path, monkeypatch):
    """受け入れ条件 2: /proc を読めず pytest も数えられなければ host を省き、行は今どおり書く。"""
    monkeypatch.setattr(procs, "PROC_LOADAVG", tmp_path / "無い")
    monkeypatch.setattr(procs, "PROC_MEMINFO", tmp_path / "無い")
    monkeypatch.setattr(procs, "pytest_counts", lambda: None)
    s, text = run_plan(tmp_path, [{"id": "a", "type": "run", "cmd": "sleep 0.5", "next": "end"}], report_interval=0.2)
    assert "結果: 完了" in text
    got = rows(s)
    alive = [r for r in got if r["kind"] == "alive"]
    assert alive and all("host" not in r for r in got)
    assert {"step", "type", "elapsed", "worker"} <= set(alive[0])


def test_pytest_count_failure_does_not_break_lines(tmp_path, fake_proc, monkeypatch):
    """I1: 数える処理が例外を上げても、読めた値だけで行を書く。"""

    def boom():
        raise RuntimeError("壊れた")

    monkeypatch.setattr(procs, "pytest_counts", boom)
    s, _ = run_plan(tmp_path, [{"id": "a", "type": "run", "cmd": "true", "next": "end"}])
    step = [r for r in rows(s) if r["kind"] == "step"][0]
    assert step["host"] == EXPECTED


def test_only_readable_values_are_kept(tmp_path):
    """I2: 片方だけ読めるときは読めた鍵だけを持ち、0 で埋めない。"""
    (tmp_path / "meminfo").write_text("MemAvailable:   2048 kB\nSwapTotal: 不明 kB\n")
    host = procs.host_sample(tmp_path / "無い", tmp_path / "meminfo")
    assert "load" not in host and "swap_total_mib" not in host and "swap_free_mib" not in host
    assert host["mem_available_mib"] == 2
    (tmp_path / "loadavg").write_text("1.0 abc\n")
    assert "load" not in procs.host_sample(tmp_path / "loadavg", tmp_path / "無い")


@pytest.mark.parametrize(
    ("line", "main"),
    [
        (["/repo/.venv/bin/python3", "/repo/.venv/bin/pytest", "-q", "-n", "4"], True),
        (["pytest", "tests"], True),
        (["/usr/bin/py.test"], True),
        (["python3.14", "-I", "-m", "pytest", "-q"], True),
        (["python", "-X", "dev", "-m", "pytest"], True),
        (["uv", "run", "--frozen", "pytest", "-q"], False),
        (["/bin/bash", "-c", "uv run pytest . -q"], False),
        (["python", "-u", "-c", "import sys;exec(eval(sys.stdin.readline()))"], False),
        (["python3", "script.py", "pytest"], False),
        ([], False),
    ],
)
def test_pytest_main_is_told_by_command_shape(line, main):
    """決定 4: uv の親とシェルは本体に数えず、命令行の形で見分ける。"""
    assert procs.is_pytest_main(line) is main


def test_pytest_started_outside_supervise_is_counted(tmp_path):
    """受け入れ条件 4: 外で打った pytest（xdist の作業プロセス 2 本）を数える。"""
    pytest.importorskip("xdist")
    (tmp_path / "test_wait.py").write_text("import time\n\ndef test_a():\n    time.sleep(30)\n\ndef test_b():\n    time.sleep(30)\n")
    before = procs.pytest_counts()
    p = subprocess.Popen(
        [sys.executable, "-m", "pytest", "-q", "-n", "2", "-p", "no:cacheprovider", str(tmp_path)],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if len(procs.child_pids(p.pid, recursive=False)) >= 2:
                break
            time.sleep(0.2)
        mains, workers = procs.pytest_counts()
        assert mains >= before[0] + 1
        assert workers >= before[1] + 2
    finally:
        procs.stop_tree(p.pid, grace=2)
        p.wait(timeout=10)


def test_host_sample_is_fast_with_many_processes(monkeypatch):
    """非機能: 300 本以上のプロセスを見せても 1 秒以内に返る（一覧は 1 回だけ回す）。"""

    class P:
        def __init__(self, i):
            self.info = {
                "pid": 10_000 + i,
                "ppid": 10_000 if i else 1,
                "cmdline": ["python3", "-m", "pytest"] if i == 0 else ["python3", "-c", "x"],
            }

    calls = []

    def fake_iter(attrs):
        calls.append(attrs)
        return [P(i) for i in range(400)]

    monkeypatch.setattr(procs.psutil, "process_iter", fake_iter)
    t = time.monotonic()
    host = procs.host_sample()
    assert time.monotonic() - t < 1
    assert len(calls) == 1 and host["pytest"] == 1 and host["pytest_workers"] == 399
