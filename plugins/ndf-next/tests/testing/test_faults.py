from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from ndf.testing import faults

HANG_SCRIPT = Path(__file__).with_name("_durable_hang.py")


def _logged(log: Path) -> list[str]:
    return log.read_text().split() if log.exists() else []


def _hang(root: Path, log: Path, hang_at: int, env: dict[str, str]) -> subprocess.Popen:
    args = [sys.executable, str(HANG_SCRIPT), str(root), "fault-key", "wf-fault", str(log), str(hang_at)]
    return subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)


@pytest.fixture
def plugin_env(tmp_path, src_env) -> dict[str, str]:
    manifest = tmp_path / "plugin" / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"name": "ndf-next", "version": "11.0.0-dev.1"}))
    return {**src_env, "CLAUDE_PLUGIN_ROOT": str(manifest.parents[1])}


def test_rerun_after_kill_9_resumes_from_the_step_that_was_killed(tmp_path, plugin_env):
    """C-6: 手 2 の途中で組ごと SIGKILL で落とし、打ち直すと済んだ手 1 を流さず手 2 から流し直す。"""
    root, log = tmp_path / "state", tmp_path / "acts.log"
    proc = _hang(root, log, 2, plugin_env)
    try:
        code = faults.kill_group_when(proc, lambda: _logged(log) == ["1", "2"], what="手 2 の開始")
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
    assert code == -signal.SIGKILL

    again = _hang(root, log, 0, plugin_env)
    out, err = again.communicate(timeout=120)
    assert again.returncode == 0, err
    assert json.loads(out.strip().splitlines()[-1]) == [10, 20, 30]
    assert _logged(log) == ["1", "2", "2", "3"]


def test_child_that_ends_before_the_point_fails_the_test(tmp_path):
    proc = subprocess.Popen(["true"], start_new_session=True)
    with pytest.raises(AssertionError, match="前に子が終わった"):
        faults.kill_group_when(proc, lambda: False, what="来ない時点")
