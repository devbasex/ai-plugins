"""`measure_project` がリモートのブランチの状態を測定の結果と CI の測定へ渡す振る舞い（#3569）。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import git_sandbox
import pytest

from ndf.domains.project import cli


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    """リモート追跡の ref だけを持つ一時のリポジトリ（通信しない）。"""
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    (tmp_path / "gitconfig").write_text("", encoding="utf-8")
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("x\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "init")
    _git(path, "remote", "add", "origin", "https://github.com/o/r.git")
    for name in ("main", "develop", "trunk", "release", "other"):
        _git(path, "update-ref", f"refs/remotes/origin/{name}", "HEAD")
    return path


def _declare(repo: Path, base: str, production: str) -> None:
    """ブランチ戦略のセクションを持つ設定を書く（スキーマが要るキーを埋める）。"""
    section = {
        "kind": "plugin",
        "release_strategy": "trunk",
        "branches": {"base": base, "production": production},
        "sprint": {"start": "production", "integrate": "check", "doc_prs": "sprint"},
        "release": {
            "form": "package-plugin",
            "version_store": {"kind": "plugin"},
            "tag": None,
            "github_release": False,
            "cd": None,
            "deliveries": [],
            "plugin": "x",
            "runtimes": ["claude"],
        },
        "verify": None,
        "channels": [{"name": "stable", "ref": production}],
        "approval": {"target": "release_pr", "via": "gate"},
        "merge": {"actor": "ndf", "method": "merge-queue", "commit": "merge", "admin": False},
    }
    (repo / ".ndf").mkdir(exist_ok=True)
    (repo / ".ndf" / "project.json").write_text(json.dumps({"version": 1, "branch_strategy": section}), encoding="utf-8")


def _measure(repo: Path, monkeypatch) -> tuple[dict[str, Any], list[tuple[Any, Any]]]:
    """`gh` を呼ばずに測り、測定の結果と、CI の測定が受けた（リポジトリ, 既定ブランチ）を返す。"""
    seen: list[tuple[Any, Any]] = []

    def fake_ci(tree, gh_repo, head, deadline):
        seen.append((gh_repo, head))
        return {"ci": {"status": "unknown", "reason": "x"}, "merges": None, "github_issues": None, "hosts": [], "notes": []}

    monkeypatch.setattr(cli.measure_ci, "measure", fake_ci)
    return cli.measure_project(repo, 30), seen


def test_the_remote_head_goes_to_ci_and_the_result(repo, monkeypatch):
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    got, seen = _measure(repo, monkeypatch)
    assert seen == [("o/r", "trunk")]
    assert got["branch_state"] == {"head": "trunk", "present": ["develop", "main"]}


def test_without_a_remote_head_the_head_is_none(repo, monkeypatch):
    got, seen = _measure(repo, monkeypatch)
    assert seen == [("o/r", None)]
    assert got["branch_state"] == {"head": None, "present": ["develop", "main"]}


def test_declared_branches_are_added_to_the_present_names(repo, monkeypatch):
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _declare(repo, "trunk", "release")
    got, seen = _measure(repo, monkeypatch)
    assert seen == [("o/r", "main")]
    assert got["branch_state"] == {"head": "main", "present": ["develop", "main", "release", "trunk"]}
