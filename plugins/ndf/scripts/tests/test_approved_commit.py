"""lib/approved_commit.py: 承認したコミットの読み書きと比較（#815）。一時の git リポジトリで縛る。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import approved_commit as ac  # noqa: E402


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(root: Path, name: str, text: str, msg: str | None = None) -> str:
    (root / name).write_text(text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg or f"{name}: {text}")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q", "-b", "develop")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    commit(root, "a.txt", "a\n")
    return root


def release_branch(root: Path, approved: str) -> AllowedPRInfo:
    """approved から release ブランチを切り、版と changelog の 2 コミットを積む。"""
    git(root, "checkout", "-q", "-b", "release", approved)
    c1 = commit(root, "version.txt", "1.0.0\n")
    c2 = commit(root, "CHANGELOG.md", "## 1.0.0\n")
    git(root, "checkout", "-q", "develop")
    return AllowedPRInfo(c2, [c1, c2])


class AllowedPRInfo:
    def __init__(self, head, commits):
        self.head, self.commits = head, commits

    def pr(self, merge_commit=None):
        return ac.AllowedPR(1, self.head, list(self.commits), merge_commit)


def land(root: Path, method: str) -> str | None:
    """release を develop へ入れ、GitHub が返す mergeCommit に当たるものを返す。"""
    if method == "merge":
        git(root, "merge", "-q", "--no-ff", "-m", "Merge pull request #1", "release")
    elif method == "squash":
        git(root, "merge", "-q", "--squash", "release")
        git(root, "commit", "-q", "-m", "Release (#1)")
    else:
        git(root, "checkout", "-q", "-b", "rb", "release")
        git(root, "rebase", "-q", "develop")
        git(root, "checkout", "-q", "develop")
        git(root, "merge", "-q", "--ff-only", "rb")
    return git(root, "rev-parse", "HEAD")


@pytest.mark.parametrize("method", ["merge", "squash", "rebase"])
def test_only_the_release_pr_after_approval_matches(repo, method):
    """受け入れ条件 3: 承認の後が配布の PR だけなら、マージの方法によらず match。"""
    approved = git(repo, "rev-parse", "HEAD")
    rel = release_branch(repo, approved)
    mc = land(repo, method)
    v = ac.compare(repo, approved, git(repo, "rev-parse", "HEAD"), [rel.pr(mc)])
    assert (v.ok, v.reason, v.outside) == (True, ac.MATCH, [])


@pytest.mark.parametrize("method", ["merge", "squash", "rebase"])
def test_an_outside_change_after_the_release_pr_is_caught(repo, method):
    """受け入れ条件 5（後に入った）: 配布の PR の後に外の変更が入れば outside_commits で SHA と件名が並ぶ。"""
    approved = git(repo, "rev-parse", "HEAD")
    rel = release_branch(repo, approved)
    mc = land(repo, method)
    other = commit(repo, "statusline.txt", "x\n", "statusline (#806)")
    v = ac.compare(repo, approved, other, [rel.pr(mc)])
    assert not v.ok and v.reason == ac.OUTSIDE_COMMITS
    assert [(o.sha, o.subject) for o in v.outside] == [(other, "statusline (#806)")]


def test_an_outside_change_before_the_release_branch_is_caught(repo):
    """受け入れ条件 5（前に入った・I3）: 承認の後の先端から配布のブランチを切ると、その起点の外の変更を拾う。"""
    approved = git(repo, "rev-parse", "HEAD")
    other = commit(repo, "statusline.txt", "x\n", "statusline (#806)")
    rel = release_branch(repo, other)
    mc = land(repo, "merge")
    v = ac.compare(repo, approved, git(repo, "rev-parse", "HEAD"), [rel.pr(mc)])
    assert v.reason == ac.OUTSIDE_COMMITS and [o.sha for o in v.outside] == [other]


def test_an_outside_change_and_its_revert_are_caught_although_the_tree_matches(repo):
    """I9: 外の変更とその revert が入ると木は一致するが、コミットの確かめで止まる。"""
    approved = git(repo, "rev-parse", "HEAD")
    rel = release_branch(repo, approved)
    mc = land(repo, "merge")
    other = commit(repo, "statusline.txt", "x\n")
    git(repo, "revert", "--no-edit", other)
    tip = git(repo, "rev-parse", "HEAD")
    v = ac.compare(repo, approved, tip, [rel.pr(mc)])
    assert v.reason == ac.OUTSIDE_COMMITS and len(v.outside) == 2


def test_a_merge_commit_carrying_an_outside_change_differs_in_tree(repo):
    """I4: 外の変更をマージコミットの中で入れる（コミットが外に残らない）と tree_differs。"""
    approved = git(repo, "rev-parse", "HEAD")
    rel = release_branch(repo, approved)
    git(repo, "merge", "-q", "--no-ff", "--no-commit", "release")
    (repo / "evil.txt").write_text("x\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Merge pull request #1")
    tip = git(repo, "rev-parse", "HEAD")
    v = ac.compare(repo, approved, tip, [rel.pr(tip)])
    assert v.reason == ac.TREE_DIFFERS and v.expected_tree != v.actual_tree


def test_not_ancestor_and_unknown_commits(repo):
    """受け入れ条件 7・I2: 祖先でない SHA は not_ancestor、無い SHA は unknown_commit。"""
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "side")
    side = commit(repo, "s.txt", "s\n")
    git(repo, "checkout", "-q", "develop")
    tip = commit(repo, "t.txt", "t\n")
    assert ac.compare(repo, side, tip).reason == ac.NOT_ANCESTOR
    assert ac.compare(repo, "f" * 40, tip).reason == ac.UNKNOWN_COMMIT
    assert ac.compare(repo, base, "e" * 40).reason == ac.UNDECIDABLE


def test_merge_tree_conflict_is_undecidable(repo):
    """I6: merge-tree が衝突すれば通さない。"""
    root_c = git(repo, "rev-parse", "HEAD")
    approved = commit(repo, "a.txt", "approved\n")
    git(repo, "checkout", "-q", "-b", "release", root_c)
    head = commit(repo, "a.txt", "release\n")
    git(repo, "checkout", "-q", "develop")
    v = ac.compare(repo, approved, approved, [ac.AllowedPR(1, head, [head])])
    assert (v.ok, v.reason) == (False, ac.UNDECIDABLE)


def test_without_allowed_prs_the_tip_must_keep_the_approved_tree(repo):
    """昇格の経路の形: allowed が空なら、先端に入ったコミットは承認の外。同じなら match。"""
    approved = git(repo, "rev-parse", "HEAD")
    assert ac.compare(repo, approved, approved).ok
    tip = commit(repo, "b.txt", "b\n")
    assert ac.compare(repo, approved, tip).reason == ac.OUTSIDE_COMMITS


MATERIAL = "# t\n\n## 2. 承認の判断に使うもの\n\n| 項目 | 内容 |\n| --- | --- |\n| {row} | {sha} |\n| 版数 | 1.0.0 |\n"


def test_material_read_record_and_rewrite(tmp_path):
    """I10: 承認の記録が無い・違うと Unapproved。record は提示した SHA が資料と違えば書かない。"""
    a, b = "a" * 40, "b" * 40
    path = tmp_path / "approval.md"
    path.write_text(MATERIAL.format(row=ac.ROW, sha=a))
    assert ac.material_sha(path) == a
    with pytest.raises(ac.Unapproved) as e:
        ac.from_material(path)
    assert e.value.recorded is None
    with pytest.raises(ac.Unapproved):
        ac.record(path, b, "user", "2026-10-05T00:00:00Z")
    assert ac.recorded_sha(path) is None
    ac.record(path, a, "mvv", "2026-10-05T00:00:00Z")
    ac.record(path, a, "user", "2026-10-05T00:00:01Z")  # 置き換える（2 行にならない）
    assert path.read_text().count(ac.RECORD) == 1
    assert ac.from_material(path) == a
    path.write_text(MATERIAL.format(row=ac.ROW, sha=b))  # approval-facts の書き直しで記録が消える
    with pytest.raises(ac.Unapproved):
        ac.from_material(path)


@pytest.mark.parametrize("text", ["", "abc", "g" * 40, "a" * 39])
def test_parse_sha_rejects_malformed(text):
    with pytest.raises(ValueError):
        ac.parse_sha(text)


def test_material_without_the_row_is_value_error(tmp_path):
    path = tmp_path / "approval.md"
    path.write_text(MATERIAL.format(row="版", sha="x"))
    with pytest.raises(ValueError) as e:
        ac.from_material(path)
    assert not isinstance(e.value, ac.Unapproved)
    with pytest.raises(ValueError):
        ac.material_sha(tmp_path / "none.md")
