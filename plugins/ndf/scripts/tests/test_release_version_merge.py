"""release_lib/version_merge.py: release/v<版> を develop へ取り込むときの版数の衝突（#2436 の R5）。

一時の git リポジトリで、develop が開発版の版数へ上がった後に本番の版の release/v<版> を取り込む。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from release_lib import version_merge  # noqa: E402

MANIFEST = "plugins/ndf/.claude-plugin/plugin.json"


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)


def manifest(version: str) -> str:
    return json.dumps({"name": "ndf", "version": version, "description": f"NDF (v{version})", "author": "a"}, indent=2) + "\n"


def commit(root: Path, files: dict, msg: str) -> str:
    for rel, text in files.items():
        write(root, rel, text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q", "-b", "develop")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    commit(root, {MANIFEST: manifest("10.27.14"), "src/a.py": "a = 1\n"}, "init")
    return root


def branch_release(root: Path, files: dict) -> str:
    """承認したコミット（今の develop）から release/v10.28.0 を切り、版上げを積む。"""
    git(root, "checkout", "-q", "-b", "release/v10.28.0")
    tip = commit(root, {MANIFEST: manifest("10.28.0"), **files}, "bump 10.28.0")
    git(root, "checkout", "-q", "develop")
    return tip


def test_hunks_that_differ_only_in_versions_take_the_release_side():
    text = "x\n<<<<<<< ours\n  \"version\": \"10.28.0\",\n=======\n  \"version\": \"10.28.0-dev.4\",\n>>>>>>> theirs\ny\n"
    assert version_merge.resolve_text(text) == 'x\n  "version": "10.28.0",\ny\n'


def test_hunks_that_differ_beyond_versions_are_not_resolved():
    text = "<<<<<<< ours\nv 10.28.0 a\n=======\nv 10.28.0-dev.4 b\n>>>>>>> theirs\n"
    assert version_merge.resolve_text(text) is None


def test_sync_takes_the_release_version_and_keeps_the_release_tip_as_a_parent(repo):
    """develop の版数が 10.28.0-dev.4 に上がっていても、本番の版を取り、release/v<版> の先頭を親に持つ。"""
    tip = branch_release(repo, {})
    dev = commit(repo, {MANIFEST: manifest("10.28.0-dev.4"), "src/b.py": "b = 2\n"}, "dev 10.28.0-dev.4")
    sha, resolved = version_merge.sync_commit(repo, tip, dev, "sync")
    assert resolved == [MANIFEST]
    assert git(repo, "rev-parse", f"{sha}^1", f"{sha}^2").split() == [tip, dev]
    assert json.loads(git(repo, "show", f"{sha}:{MANIFEST}"))["version"] == "10.28.0"
    assert git(repo, "show", f"{sha}:src/b.py") == "b = 2"
    assert git(repo, "rev-parse", "release/v10.28.0") == tip  # release/v<版> そのものへは develop を取り込まない


def test_sync_stops_when_a_place_without_versions_conflicts(repo):
    """版数を持たない箇所の衝突は解かずに止める（R5）。"""
    tip = branch_release(repo, {"src/a.py": "a = 3\n"})
    dev = commit(repo, {MANIFEST: manifest("10.28.0-dev.4"), "src/a.py": "a = 4\n"}, "dev")
    with pytest.raises(version_merge.VersionConflict) as e:
        version_merge.sync_commit(repo, tip, dev, "sync")
    assert e.value.code == 1 and e.value.paths == ["src/a.py"]


def test_sync_without_conflicts_is_a_plain_merge(repo):
    tip = branch_release(repo, {})
    dev = commit(repo, {"src/b.py": "b = 2\n"}, "dev")
    sha, resolved = version_merge.sync_commit(repo, tip, dev, "sync")
    assert resolved == [] and git(repo, "rev-parse", f"{sha}^1", f"{sha}^2").split() == [tip, dev]
