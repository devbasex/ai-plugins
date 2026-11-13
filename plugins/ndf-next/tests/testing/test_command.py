from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ndf.testing import records, slots

PLUGIN_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def project(tmp_path):
    """宣言の runner が `fakerun` のプロジェクト。`fakerun` は始まりと終わりを記録へ足して少し眠る。"""
    root = tmp_path / "project"
    (root / ".ndf").mkdir(parents=True)
    suites = [{"name": "fake", "runner": "fakerun", "command": "fakerun ."}]
    (root / ".ndf" / "project.json").write_text(json.dumps({"test": {"suites": suites}}))
    (root / ".ndf" / "resources.json").write_text(
        json.dumps({"test_slots": {"max_runs": 1, "memory_floor_mib": 1, "wait_limit_seconds": 120}})
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "fakerun"
    fake.write_text(
        '#!/bin/sh\necho "start $(date +%s.%N)" >> "$LOG"\nsleep 0.3\necho "end $(date +%s.%N)" >> "$LOG"\necho out; echo err >&2\nexit "${FAKE_EXIT:-0}"\n'
    )
    fake.chmod(0o755)
    return root, bin_dir, tmp_path / "log"


@pytest.fixture
def slots_dir(forbid_real_state):
    """枠の置き場。forbid_real_state が `NDF_TEST_SLOTS_DIR` をテストの一時ディレクトリへ向けている。"""
    return Path(os.environ[slots.SLOTS_DIR_ENV])


def _ndf(args, cwd, bin_dir=None, log=None, **extra):
    # CI（GITHUB_ACTIONS）と入れ子の印は枠を飛ばす（決定 12・I3）。手元の振る舞いを確かめるので外す
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_ACTIONS", slots.HELD_ENV)}
    env.update({"PYTHONPATH": str(PLUGIN_ROOT / "src"), **extra})
    if bin_dir:
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
    if log:
        env["LOG"] = str(log)
    return subprocess.Popen(
        [sys.executable, "-m", "ndf", "test-slot", *args], cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


def test_run_keeps_output_and_exit_code(project, slots_dir):
    root, bin_dir, log = project
    for code in ("0", "1"):
        proc = _ndf(["run", "--", "fakerun ."], root, bin_dir, log, FAKE_EXIT=code)
        out, err = proc.communicate()
        assert (proc.returncode, out, err) == (int(code), "out\n", "err\n")
    assert [row["result"] for row in records.read_events(slots_dir)] == ["finished", "finished"]


def test_parallel_runs_wait_for_each_other(project):
    root, bin_dir, log = project
    procs = [_ndf(["run", "--", "fakerun ."], root, bin_dir, log) for _ in range(3)]
    assert [p.wait() for p in procs] == [0, 0, 0]
    marks = sorted((float(t), kind) for kind, t in (line.split() for line in log.read_text().splitlines()))
    running = peak = 0
    for _, kind in marks:
        running += 1 if kind == "start" else -1
        peak = max(peak, running)
    assert peak == 1


def test_light_command_runs_without_a_slot(project, slots_dir):
    root, bin_dir, log = project
    proc = _ndf(["run", "--", "fakerun one_file.py"], root, bin_dir, log)
    assert proc.wait() == 0
    assert records.read_events(slots_dir) == []


def test_wrong_setting_refuses_to_run(project):
    root, bin_dir, log = project
    (root / ".ndf" / "resources.json").write_text(json.dumps({"test_slots": {"max_runs": 0}}))
    proc = _ndf(["run", "--", "fakerun ."], root, bin_dir, log)
    _, err = proc.communicate()
    assert proc.returncode == 2
    assert "max_runs" in json.loads(err)["items"][0]
    assert not log.exists()


def test_report_reads_the_records(tmp_path, slots_dir):
    directory = slots_dir
    directory.mkdir(parents=True)
    records.append_event(
        directory,
        {"at": datetime.fromtimestamp(time.time(), timezone.utc).isoformat(timespec="seconds"), "waited_seconds": 3, "result": "gave_up"},
    )
    proc = _ndf(["report", "--since", "1d"], tmp_path)
    out, _ = proc.communicate()
    assert proc.returncode == 0
    metrics = json.loads(out)["metrics"]
    assert (metrics["gave_up"], metrics["waited_runs"]) == (1, 1)


def test_report_refuses_a_wrong_period(tmp_path):
    proc = _ndf(["report", "--since", "seven"], tmp_path)
    proc.communicate()
    assert proc.returncode == 2


# --- 現状固定テスト（_run の経路: 待ちの上限の上書き・諦め・置き場を使えない・シグナル・CI・入力の誤り） ---


def test_wait_limit_overrides_the_setting_and_gives_up_with_a_stopped_result(project, slots_dir):
    from ndf.testing.limits import Limits

    root, bin_dir, log = project
    directory = slots_dir
    holding = Limits(max_runs=1, memory_floor_mib=1, wait_limit_seconds=60)
    with slots.gate("fakerun .", root, kind="whole", limits=holding, environ={}, directory=directory, read_memory=lambda: 10**9):
        proc = _ndf(["run", "--wait-limit", "1", "--", "fakerun ."], root, bin_dir, log)
        out, err = proc.communicate()
    assert (proc.returncode, out) == (2, "")
    result = json.loads(err)
    assert result["status"] == "stopped"
    assert result["cause"] == {"kind": slots.GAVE_UP}
    assert result["metrics"]["available_mib"] is None
    assert result["metrics"]["waited_seconds"] >= 1
    assert "諦めた" in result["summary"]
    assert not log.exists()
    rows = records.read_events(directory)
    assert [row["result"] for row in rows] == [slots.GAVE_UP, slots.FINISHED]
    assert rows[0]["kind"] == slots.DIRECT and rows[1]["kind"] == "whole"


def test_unusable_slots_dir_still_runs_and_prints_the_note(project, tmp_path):
    root, bin_dir, log = project
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    proc = _ndf(["run", "--", "fakerun ."], root, bin_dir, log, **{slots.SLOTS_DIR_ENV: str(blocker / "slots")})
    out, err = proc.communicate()
    assert (proc.returncode, out) == (0, "out\n")
    assert "err" in err.splitlines()
    assert err.splitlines()[-1].startswith("枠の置き場を使えないので枠を取らずに走らせた: ")
    assert log.exists()


def test_signal_is_forwarded_and_recorded_as_interrupted(project, slots_dir):
    import signal

    from ndf.testing import waits

    root, bin_dir, log = project
    proc = _ndf(["run", "--", "fakerun . && sleep 60"], root, bin_dir, log)
    try:
        waits.wait_until(lambda: log.exists() and "start" in log.read_text(), what="fakerun の開始")
        proc.send_signal(signal.SIGTERM)
        with waits.returns_before(60):
            _, err = proc.communicate()
    finally:
        proc.kill()
    assert proc.returncode == 128 + signal.SIGTERM
    rows = records.read_events(slots_dir)
    assert [row["result"] for row in rows] == [slots.INTERRUPTED]
    assert rows[0]["kind"] == slots.DIRECT


def test_signal_while_waiting_for_a_slot_ends_the_wait(project, slots_dir):
    import signal

    from ndf.testing import waits
    from ndf.testing.limits import Limits

    root, bin_dir, log = project
    holding = Limits(max_runs=1, memory_floor_mib=1, wait_limit_seconds=60)
    with slots.gate("fakerun .", root, kind="whole", limits=holding, environ={}, directory=slots_dir, read_memory=lambda: 10**9):
        proc = _ndf(["run", "--", "fakerun ."], root, bin_dir, log)
        try:
            # 枠が埋まっているので待ちに入る（起動して待ちに入るまでの時間を空ける）。待ちの間の SIGTERM で、
            # 上限（120 秒）を待たずに抜ける
            time.sleep(3)
            assert proc.poll() is None
            proc.send_signal(signal.SIGTERM)
            with waits.returns_before(30):
                proc.communicate()
        finally:
            proc.kill()
    assert proc.returncode == 128 + signal.SIGTERM
    assert not log.exists()
    assert [row["result"] for row in records.read_events(slots_dir)] == [slots.INTERRUPTED, slots.FINISHED]


def test_ci_run_takes_no_slot_and_keeps_the_exit_code(project, slots_dir):
    root, bin_dir, log = project
    proc = _ndf(["run", "--", "fakerun ."], root, bin_dir, log, GITHUB_ACTIONS="true", FAKE_EXIT="3")
    out, err = proc.communicate()
    assert (proc.returncode, out, err) == (3, "out\n", "err\n")
    assert records.read_events(slots_dir) == []


def test_command_without_double_dash_is_accepted(project):
    root, bin_dir, log = project
    proc = _ndf(["run", "fakerun ."], root, bin_dir, log)
    out, _ = proc.communicate()
    assert (proc.returncode, out) == (0, "out\n")


@pytest.mark.parametrize(
    ("args", "summary"),
    [
        (["run", "--wait-limit", "0", "--", "fakerun ."], "--wait-limit は 1 以上の秒にする"),
        (["run", "--", "a", "b"], "run には -- の後ろにシェルのコマンドを 1 つ（引用して）渡す"),
        (["run", "--"], "run には -- の後ろにシェルのコマンドを 1 つ（引用して）渡す"),
        (["run", "--", "  "], "run には -- の後ろにシェルのコマンドを 1 つ（引用して）渡す"),
    ],
)
def test_wrong_run_arguments_are_stopped_before_running(project, args, summary):
    root, bin_dir, log = project
    proc = _ndf(args, root, bin_dir, log)
    out, err = proc.communicate()
    assert (proc.returncode, out) == (2, "")
    assert json.loads(err) == {"status": "stopped", "summary": summary}
    assert not log.exists()
