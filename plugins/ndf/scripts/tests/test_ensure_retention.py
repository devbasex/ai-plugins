"""ensure-retention.sh が書く settings.json の場所を検証する。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

if shutil.which("bash") is None or shutil.which("jq") is None:
    pytest.skip("bash / jq not available", allow_module_level=True)

SCRIPT = Path(__file__).resolve().parents[1] / "ensure-retention.sh"


def _run(home: Path, config_dir: Path | None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("CLAUDE_CONFIG_DIR", None)
    if config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)


def test_writes_under_home_claude_without_config_dir(tmp_path: Path) -> None:
    result = _run(tmp_path, None)
    assert result.returncode == 0, result.stderr
    data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert data["cleanupPeriodDays"] == 90


def test_writes_under_claude_config_dir(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cfg = tmp_path / "cfg"
    home.mkdir()
    result = _run(home, cfg)
    assert result.returncode == 0, result.stderr
    data = json.loads((cfg / "settings.json").read_text())
    assert data["cleanupPeriodDays"] == 90
    assert not (home / ".claude" / "settings.json").exists()
