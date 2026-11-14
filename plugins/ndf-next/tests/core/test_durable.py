"""DBOS の殻を通した判断のループ（耐久を通す代表）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ndf.core import durable, loop

from ._counting import decide

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SRC = PLUGIN_ROOT / "src"
CRASH_SCRIPT = Path(__file__).with_name("_durable_crash.py")
# _durable_crash.py が落ちる手で落ちたときの終了コード（同名の定数と同じ値。そちらは import すると引数を読む）
CRASH_EXIT_CODE = 9

performed: list[int] = []


@durable.step
def perform(act: int) -> int:
    performed.append(act)
    return act * 10


@durable.workflow
def flow(start: list[int]) -> list[int]:
    assert durable.current_id() is not None
    return loop.run(decide, perform, start)


@pytest.fixture
def launched(tmp_path):
    url = f"sqlite:///{tmp_path / 'durable.sqlite'}"
    performed.clear()
    durable.launch(url, "test", "durable-test")
    yield url
    durable.shutdown()


def test_workflow_runs_loop_and_rerun_returns_recorded_result(launched):
    assert durable.current_id() is None
    assert durable.start(flow, "wf-1", []) == [10, 20, 30]
    assert performed == [1, 2, 3]
    assert durable.status("wf-1") == "SUCCESS"
    assert durable.status("wf-missing") is None

    # 同じ ID は流し直さず、記録した結果を返す
    assert durable.start(flow, "wf-1", []) == [10, 20, 30]
    assert durable.result("wf-1") == [10, 20, 30]
    assert performed == [1, 2, 3]


def _crash_run(url: str, log: Path, crash_at: int) -> subprocess.CompletedProcess[str]:
    """_durable_crash.py を子プロセスで流す。crash_at の手で落ちると終了コードは CRASH_EXIT_CODE になる。"""
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    return subprocess.run(
        [sys.executable, str(CRASH_SCRIPT), url, "wf-crash", str(log), str(crash_at)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )


def test_recovery_continues_without_rerunning_finished_steps(tmp_path):
    url = f"sqlite:///{tmp_path / 'crash.sqlite'}"
    log = tmp_path / "acts.log"

    first = _crash_run(url, log, crash_at=3)
    assert first.returncode == CRASH_EXIT_CODE, first.stderr
    assert log.read_text().split() == ["1", "2", "3"]

    second = _crash_run(url, log, crash_at=0)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout.strip().splitlines()[-1]) == [10, 20, 30]
    # 済んだ手 1 と 2 は流し直さず、落ちた手 3 だけを流し直す
    assert log.read_text().split() == ["1", "2", "3", "3"]
