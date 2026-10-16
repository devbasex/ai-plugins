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


SWITCH = Path(__file__).resolve().parents[1] / "statusline-switch.sh"


def _linked(tmp_path: Path) -> tuple[Path, Path, Path]:
    """共有の settings.json への symlink を持つアカウントの設定ディレクトリ（#1576）。"""
    home, shared, account = tmp_path / "home", tmp_path / "shared", tmp_path / "account"
    for d in (home, shared, account):
        d.mkdir()
    (shared / "settings.json").write_text('{"model": "sonnet"}')
    os.symlink(shared / "settings.json", account / "settings.json")
    return home, shared, account


def test_retention_writes_through_symlink(tmp_path: Path) -> None:
    """受け入れ条件 15・I16: symlink は symlink のまま、値と印・排他は参照先の側に入る。"""
    home, shared, account = _linked(tmp_path)
    result = _run(home, account)
    assert result.returncode == 0, result.stderr
    assert (account / "settings.json").is_symlink()
    data = json.loads((shared / "settings.json").read_text())
    assert data == {"model": "sonnet", "cleanupPeriodDays": 90}
    assert (shared / ".ndf-retention-checked").exists() and not (account / ".ndf-retention-checked").exists()


def test_statusline_ensure_writes_through_symlink(tmp_path: Path) -> None:
    """受け入れ条件 15・I16: statusline-switch.sh ensure も symlink を残して参照先へ書く。"""
    home, shared, account = _linked(tmp_path)
    env = {**os.environ, "HOME": str(home), "CLAUDE_CONFIG_DIR": str(account)}
    result = subprocess.run(["bash", str(SWITCH), "ensure"], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert (account / "settings.json").is_symlink()
    data = json.loads((shared / "settings.json").read_text())
    assert data["model"] == "sonnet" and "statusLine" in data
    assert not any(p.name.startswith(".ndf-statusline") for p in account.iterdir())
