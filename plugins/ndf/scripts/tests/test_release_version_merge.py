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
    text = 'x\n<<<<<<< ours\n  "version": "10.28.0",\n=======\n  "version": "10.28.0-dev.4",\n>>>>>>> theirs\ny\n'
    assert version_merge.resolve_text(text, {"10.28.0"}) == 'x\n  "version": "10.28.0",\ny\n'


def test_hunks_that_differ_beyond_versions_are_not_resolved():
    text = "<<<<<<< ours\nv 10.28.0 a\n=======\nv 10.28.0-dev.4 b\n>>>>>>> theirs\n"
    assert version_merge.resolve_text(text, {"10.28.0"}) is None


def test_hunks_with_other_versions_than_the_release_wrote_are_not_resolved():
    """同じ塊にある依存ライブラリの版数の更新を、本番の側の古い値へ戻さない。"""
    text = '<<<<<<< ours\n  "version": "10.28.0",\n  "dep": "1.2.3",\n=======\n  "version": "10.28.0-dev.4",\n  "dep": "1.2.4",\n>>>>>>> theirs\n'
    assert version_merge.resolve_text(text, {"10.28.0"}) is None


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


def test_sync_stops_when_a_dependency_version_shares_the_hunk(repo):
    """版数の衝突と同じ塊にある依存の版数の更新は、本番の側の古い値へ戻さずに止める。"""
    dep = lambda v, d: f'{{\n  "version": "{v}",\n  "dep": "{d}"\n}}\n'  # noqa: E731
    commit(repo, {"pkg.json": dep("10.27.14", "1.2.3")}, "pkg")
    tip = branch_release(repo, {"pkg.json": dep("10.28.0", "1.2.3")})
    dev = commit(repo, {"pkg.json": dep("10.28.0-dev.4", "1.2.4")}, "dev")
    with pytest.raises(version_merge.VersionConflict) as e:
        version_merge.sync_commit(repo, tip, dev, "sync")
    assert e.value.paths == ["pkg.json"]


def test_sync_without_conflicts_is_a_plain_merge(repo):
    tip = branch_release(repo, {})
    dev = commit(repo, {"src/b.py": "b = 2\n"}, "dev")
    sha, resolved = version_merge.sync_commit(repo, tip, dev, "sync")
    assert resolved == [] and git(repo, "rev-parse", f"{sha}^1", f"{sha}^2").split() == [tip, dev]


def test_sections_take_the_release_section_and_keep_the_base_branch_rest():
    """戻すマージ（#3121 の決定 17）: 同じ見出しの節はリリースブランチの中身、ほかの節と起点のブランチだけの節は起点のブランチのまま。"""
    base = "# C\n\n## [ndf 1.0.0]\n- old\n"
    ours = "# C\n\n## [ndf 1.1.0]\n- #11\n\n## [ndf 1.0.0]\n- old\n"
    theirs = "# C\n\n## [ndf 1.1.0]\n- #12\n- #13\n\n## [ndf 1.0.0]\n- old\n- fixed on develop\n"
    want = "# C\n\n## [ndf 1.1.0]\n- #11\n\n## [ndf 1.0.0]\n- old\n- fixed on develop\n"
    assert version_merge.resolve_sections(base, ours, theirs) == want
    assert version_merge.resolve_sections(base, ours, "# C\n\n## [ndf 1.0.0]\n- old\n") == ours  # 起点に無い節は先頭の節の前へ足す


def test_sync_with_sections_resolves_the_changelog_section_written_by_both_sides(repo):
    """開発版のデプロイが develop に書いた同じ版の節と、リリースブランチの節の衝突は、sections のときだけ解く。"""
    commit(repo, {"CHANGELOG.md": "# C\n\n## [ndf 10.27.14]\n- a\n"}, "changelog")
    tip = branch_release(repo, {"CHANGELOG.md": "# C\n\n## [ndf 10.28.0]\n- #11\n\n## [ndf 10.27.14]\n- a\n"})
    dev = commit(repo, {"CHANGELOG.md": "# C\n\n## [ndf 10.28.0]\n- #12\n\n## [ndf 10.27.14]\n- a\n"}, "dev changelog")
    with pytest.raises(version_merge.VersionConflict):
        version_merge.sync_commit(repo, tip, dev, "sync")
    sha, resolved = version_merge.sync_commit(repo, tip, dev, "sync", sections=True)
    assert "CHANGELOG.md" in resolved and git(repo, "rev-parse", f"{sha}^1", f"{sha}^2").split() == [tip, dev]
    assert git(repo, "show", f"{sha}:CHANGELOG.md") == "# C\n\n## [ndf 10.28.0]\n- #11\n\n## [ndf 10.27.14]\n- a"
