"""旧いキーの残る MVV の設定を読むと、旧いキーと新しいキーの位置と移行のコマンドを出して止まる（#3613。I6・受け入れ条件 13）。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import settings_legacy as legacy
from ndf.domains.records import mvv, preflight


def _write(root: Path, name: str, data: dict) -> Path:
    (root / ".ndf").mkdir(exist_ok=True)
    path = root / ".ndf" / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_worktree_mvv_with_a_legacy_key_stops(tmp_path: Path):
    _write(tmp_path, "mvv.json", legacy.mvv(2))
    with pytest.raises(mvv.MvvError, match="/versions/1/validation"):
        mvv.worktree_version(tmp_path)


def test_mvv_at_a_ref_with_a_legacy_key_stops(tmp_path: Path):
    _write(tmp_path, "mvv.json", legacy.mvv(1))
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "x"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True)
    with pytest.raises(mvv.MvvError, match="ndf settings migrate"):
        mvv.decl_at(tmp_path, "HEAD")


def test_preflight_stops_on_a_legacy_mvv_before_the_body_check(tmp_path: Path):
    _write(tmp_path, "mvv.json", legacy.mvv(1))
    got = preflight.mvv_checked(tmp_path)
    assert got.result["status"] == "stopped" and got.result["cause"]["kind"] == "precondition"
    assert "/versions/0/validation" in got.result["summary"]
