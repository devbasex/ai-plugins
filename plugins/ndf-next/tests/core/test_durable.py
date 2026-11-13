"""DBOS の殻を通した判断のループ（耐久を通す代表）と、DBOS を import する場所の決まり。"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ndf.core import durable, loop
from ndf.core.loop import Next

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SRC = PLUGIN_ROOT / "src"
CRASH_SCRIPT = Path(__file__).with_name("_durable_crash.py")

performed: list[int] = []


def decide(state: list[int], result: int | None) -> Next[int, list[int]]:
    seen = state if result is None else [*state, result]
    if len(seen) == 3:
        return Next.finish(seen)
    return Next.step(len(seen) + 1, seen)


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
    assert first.returncode == 9, first.stderr
    assert log.read_text().split() == ["1", "2", "3"]

    second = _crash_run(url, log, crash_at=0)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout.strip().splitlines()[-1]) == [10, 20, 30]
    # 済んだ手 1 と 2 は流し直さず、落ちた手 3 だけを流し直す
    assert log.read_text().split() == ["1", "2", "3", "3"]


def _dbos_importers(root: Path) -> set[str]:
    found = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            if any(name.split(".")[0] == "dbos" for name in names):
                found.add(path.relative_to(PLUGIN_ROOT).as_posix())
    return found


def test_only_durable_imports_dbos():
    importers = _dbos_importers(PLUGIN_ROOT / "src") | _dbos_importers(PLUGIN_ROOT / "tests")
    assert importers == {"src/ndf/core/durable.py"}


def test_loop_reads_neither_durable_nor_dbos():
    tree = ast.parse((SRC / "ndf" / "core" / "loop.py").read_text())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    assert not any(m.startswith(("dbos", "ndf")) for m in modules), modules
