"""配分の表を初期値・設定・履歴から読む口と、履歴へ実行の行を書く口（#3643）。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ndf.adapters import git
from ndf.domains.crossrefactor import allocation


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> git.Git:
    monkeypatch.delenv("NDF_STATE_HOME", raising=False)
    monkeypatch.delenv("NDF_METRICS_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/o/r.git"], check=True)
    return git.Git(repo)


def _declare(root: Path, body: dict) -> None:
    (root / ".ndf").mkdir(exist_ok=True)
    (root / ".ndf" / "estimate.json").write_text(json.dumps(body), encoding="utf-8")


def test_without_declaration_or_history_uses_the_initial_values(work: git.Git) -> None:
    got = allocation.table(work, work.cwd)
    initial = json.loads(allocation.DEFAULTS_PATH.read_text(encoding="utf-8"))["values"]
    assert got["source"] == "defaults" and got["test"] == initial["test"]["minutes"]


def test_declaration_overrides_the_initial_values(work: git.Git) -> None:
    _declare(work.cwd, {"allocation_minutes": {"test": 9}})
    assert allocation.table(work, work.cwd)["test"] == 9.0


def test_history_written_by_a_run_drives_the_next_table(work: git.Git) -> None:
    _declare(work.cwd, {"allocation_minutes": {"test": 9}})
    state = {"pr": 1, "items": [{"status": "adopted", "technique": "inline", "seconds": {"test": 120, "implement": 60}}]}
    path = allocation.record_run(work, state, "2026-11-17T00:00:00+00:00")
    assert path == allocation.history_path("o/r") and path.is_file()
    path.write_text(path.read_text(encoding="utf-8") + "壊れた行\n", encoding="utf-8")
    got = allocation.table(work, work.cwd)
    assert got["source"] == "history" and got["test"] == 2.0 and got["kinds"] == {"structure/inline": 1.0}


def test_without_a_github_remote_history_is_neither_read_nor_written(work: git.Git) -> None:
    work.run("remote", "set-url", "origin", "/somewhere/else.git")
    assert allocation.record_run(work, {"items": []}, "t") is None
    assert allocation.table(work, work.cwd)["source"] == "defaults"


def test_metrics_dir_overrides_the_history_root(tmp_path: Path) -> None:
    got = allocation.history_path("o/r", {"NDF_METRICS_DIR": str(tmp_path / "m"), "XDG_STATE_HOME": str(tmp_path / "x")})
    assert got == tmp_path / "m" / "o--r" / allocation.HISTORY_NAME
