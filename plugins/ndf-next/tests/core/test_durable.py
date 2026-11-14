"""DBOS の殻を通した判断のループ（耐久を通す代表）と、鍵の名前・アプリの版（決定 81・84）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ndf.core import durable, loop

from ._counting_loader import _load_counting

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SRC = PLUGIN_ROOT / "src"
CRASH_SCRIPT = Path(__file__).with_name("_durable_crash.py")
# _durable_crash.py が落ちる手で落ちたときの終了コード（同名の定数と同じ値。そちらは import すると引数を読む）
CRASH_EXIT_CODE = 9

performed: list[int] = []

decide = _load_counting().decide


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
    root = tmp_path / "state"
    performed.clear()
    with durable.session(root, "test-key"):
        yield root
    durable.release_store(root)


def test_run_name_is_a_valid_dbos_app_name_and_stable():
    name = durable.run_name("plan-7/run-1")
    assert name == durable.run_name("plan-7/run-1") != durable.run_name("plan-7/run-2")
    assert name.startswith(durable.RUN_PREFIX)
    assert 3 <= len(name) <= 256
    assert all(c.islower() or c.isdigit() or c in "-_" for c in name)


def test_run_url_points_at_the_key_file(tmp_path):
    assert durable.run_url(tmp_path, "k") == f"sqlite:///{tmp_path / 'dbos' / (durable.run_name('k') + '.sqlite')}"


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


def test_launch_names_executor_and_application_after_the_key(tmp_path):
    root = tmp_path / "state"
    with durable.session(root, "named-key"):
        durable.start(flow, "wf-named", [])
    durable.release_store(root)

    name = durable.run_name("named-key")
    retired = root / "dbos" / "retired" / f"{name}.sqlite"
    records = durable.workflows(f"sqlite:///{retired}")
    assert [(r.run_name, r.workflow_id, r.status) for r in records] == [(name, "wf-named", "SUCCESS")]


def _write_plugin(root: Path, version: str) -> None:
    manifest = root / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"name": "ndf-next", "version": version}))


def _crash_run(root: Path, log: Path, crash_at: int, plugin: Path) -> subprocess.CompletedProcess[str]:
    """_durable_crash.py を子プロセスで流す。crash_at の手で落ちると終了コードは CRASH_EXIT_CODE になる。"""
    env = {**os.environ, "PYTHONPATH": str(SRC), "CLAUDE_PLUGIN_ROOT": str(plugin)}
    return subprocess.run(
        [sys.executable, str(CRASH_SCRIPT), str(root), "crash-key", "wf-crash", str(log), str(crash_at)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )


def test_recovery_continues_after_the_plugin_version_is_raised(tmp_path):
    """I7: アプリの版は形の番号と鍵の名前だけで決まり、plugin.json の版を上げて起動し直しても途中の実行が回復する。"""
    root = tmp_path / "state"
    plugin = tmp_path / "plugin"
    log = tmp_path / "acts.log"

    _write_plugin(plugin, "11.0.0-dev.1")
    first = _crash_run(root, log, 3, plugin)
    assert first.returncode == CRASH_EXIT_CODE, first.stderr
    assert log.read_text().split() == ["1", "2", "3"]

    _write_plugin(plugin, "11.0.0-dev.2")
    second = _crash_run(root, log, 0, plugin)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout.strip().splitlines()[-1]) == [10, 20, 30]
    # 済んだ手 1 と 2 は流し直さず、落ちた手 3 だけを流し直す
    assert log.read_text().split() == ["1", "2", "3", "3"]

    retired = root / "dbos" / "retired" / f"{durable.run_name('crash-key')}.sqlite"
    records = durable.workflows(f"sqlite:///{retired}")
    assert {r.app_version for r in records} == {durable.app_version("crash-key")}
