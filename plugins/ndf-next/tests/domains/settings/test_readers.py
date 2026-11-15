"""旧いキーの残る設定を読むと、旧いキーと新しいキーの位置と移行のコマンドを出して止まる（#3613。I6・受け入れ条件 13）。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import settings_legacy as legacy
from ndf.domains.records import mvv, preflight
from ndf.domains.worktree import merged, ndf_settings


def _write(root: Path, name: str, data: dict) -> Path:
    (root / ".ndf").mkdir(exist_ok=True)
    path = root / ".ndf" / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_worktree_settings_with_a_legacy_key_stop(tmp_path: Path):
    _write(tmp_path, "worktree.json", legacy.worktree())
    with pytest.raises(ndf_settings.NdfSettingsError, match="ndf settings migrate --root") as got:
        ndf_settings.load(tmp_path)
    assert "/branch_flow/release_strategy" in str(got.value)


def test_worktree_settings_with_the_new_key_are_read(tmp_path: Path):
    _write(
        tmp_path,
        "worktree.json",
        {**legacy.worktree(), "branch_flow": {"start": "production", "release_strategy": "release_branch", "doc_prs": "sprint"}},
    )
    got = ndf_settings.load(tmp_path)
    assert got is not None


def test_flow_record_with_a_legacy_key_stops_before_cleanup(tmp_path: Path):
    path = tmp_path / "flow.json"
    path.write_text(json.dumps(legacy.worktree()["branch_flow"]), encoding="utf-8")
    with pytest.raises(ndf_settings.NdfSettingsError, match="/branch_flow/release_strategy"):
        merged.read_flow_record(path)
    path.write_text(json.dumps({"release_strategy": "release_branch"}), encoding="utf-8")
    assert merged.read_flow_record(path) == {"release_strategy": "release_branch"}
    assert merged.read_flow_record(None) is None


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
