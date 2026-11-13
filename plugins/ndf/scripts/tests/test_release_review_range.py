"""release_lib/review_range.py（#3163）: リリースブランチの PR のレビュー（衝突の解消の差分・範囲・直しの移し・片付け）。

origin は手元の bare リポジトリで、GitHub の操作は `FakeGitHub` に差し替える。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS / "lib")]
from release_lib import review_range as rr  # noqa: E402
from step_result import StepError  # noqa: E402

VERSION = "2.0.0"
RELEASE, SHOW, BASE = rr.names(VERSION)


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def commit(repo: Path, path: str, text: str, msg: str) -> str:
    (repo / path).write_text(text)
    git(repo, "add", path)
    git(repo, "commit", "-qm", msg)
    return git(repo, "rev-parse", "HEAD")


class FakeGitHub:
    """レビューの PR を手元で持つ（`review_range.GitHub` と同じメソッド）。"""

    def __init__(self):
        self.prs: dict[int, dict] = {}
        self.fail_close = False

    def list_prs(self, root, state, head=None):
        want = "OPEN" if state == "open" else "CLOSED"
        return [dict(p) for p in self.prs.values() if p["state"] == want and (head is None or p["headRefName"] == head)]

    def create_pr(self, root, base, head, title, body):
        n = 100 + len(self.prs)
        self.prs[n] = {"number": n, "headRefName": head, "baseRefName": base, "body": body, "state": "OPEN"}
        return n

    def edit_body(self, root, n, body):
        self.prs[n]["body"] = body

    def close(self, root, n):
        if self.fail_close:
            raise StepError(f"#{n} を閉じられない")
        self.prs[n]["state"] = "CLOSED"

    def open_numbers(self):
        return [n for n, p in self.prs.items() if p["state"] == "OPEN"]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for k in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(k, "t")
    for k in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(k, "t@example.com")
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    repo = tmp_path / "r"
    git(tmp_path, "clone", "-q", str(origin), str(repo))
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    commit(repo, "f.txt", "a\nB\nc\n", "init")
    for name, path, text in (("s1", "f.txt", "a\nB1\nc\n"), ("s2", "f.txt", "a\nB2\nc\n"), ("s3", "g.txt", "g\n"), ("s4", "h.txt", "h\n")):
        git(repo, "checkout", "-q", "-b", f"sprint/{name}", "main")
        commit(repo, path, text, name)
    git(repo, "push", "-q", "origin", "main", "sprint/s1", "sprint/s2", "sprint/s3", "sprint/s4")
    return repo, FakeGitHub()


def build_release(repo: Path, units: list[str], conflict: bool = True) -> str:
    """main から RELEASE を切り、単位を --no-ff で集める。s1 と s2 の衝突は B1+B2 に解く。"""
    git(repo, "checkout", "-q", "-B", RELEASE, "origin/main")
    for u in units:
        p = subprocess.run(["git", "-C", str(repo), "merge", "-q", "--no-ff", "--no-edit", f"sprint/{u}"], capture_output=True, text=True)
        if p.returncode != 0:
            assert conflict
            (repo / "f.txt").write_text("a\nB1+B2\nc\n")
            git(repo, "add", "f.txt")
            git(repo, "commit", "-q", "--no-edit")
    git(repo, "push", "-q", "-f", "origin", RELEASE)
    return git(repo, "rev-parse", "HEAD")


def add_merge(repo: Path, unit: str) -> str:
    git(repo, "checkout", "-q", RELEASE)
    git(repo, "pull", "-q", "--ff-only", "origin", RELEASE)
    git(repo, "merge", "-q", "--no-ff", "--no-edit", f"sprint/{unit}")
    git(repo, "push", "-q", "origin", RELEASE)
    return git(repo, "rev-parse", "HEAD")


def push_fix(repo: Path, branch: str, path: str, text: str) -> str:
    """レビューの直しを見せるブランチへ積む（cross-review の直しが PR のブランチへ送られる形）。"""
    git(repo, "fetch", "-q", "origin")
    git(repo, "checkout", "-q", "-B", "fix-tmp", f"origin/{branch}")
    sha = commit(repo, path, text, "review fix")
    git(repo, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
    git(repo, "checkout", "-q", "--detach")
    return sha


def remote_heads(repo: Path) -> set[str]:
    return {line.split("refs/heads/")[1] for line in git(repo, "ls-remote", "--heads", "origin").splitlines()}


def test_only_the_conflicting_merge_has_a_remerge_diff(env):
    """受け入れ条件 4: 再マージの差分は衝突したマージだけが持つ。衝突の無い範囲は空。"""
    repo, _ = env
    head = build_release(repo, ["s1", "s2", "s3"])
    patch = rr.remerge_patch(repo, "origin/main", head)
    assert "f.txt" in patch and "g.txt" not in patch and "+B1+B2" in patch
    clean = build_release(repo, ["s1", "s3"], conflict=False)
    assert rr.remerge_patch(repo, "origin/main", clean) == ""
    with pytest.raises(StepError):
        rr.remerge_patch(repo, "no-such-ref", clean)


def test_open_review_shows_only_the_conflict_resolution(env):
    """受け入れ条件 4・決定 5: レビューの PR の差分は衝突の解消の差分だけになる。"""
    repo, gh = env
    head = build_release(repo, ["s1", "s2", "s3"])
    got = rr.open_review(repo, VERSION, "resolution", "main", gh)
    assert got["review_pr"] == 100 and got["head"] == head and got["files"] == 1
    git(repo, "fetch", "-q", "origin")
    diff = git(repo, "diff", f"origin/{BASE}", f"origin/{SHOW}")
    assert "+B1+B2" in diff and "-<<<<<<<" in diff and "g.txt" not in diff
    assert git(repo, "rev-parse", f"origin/{SHOW}^{{tree}}") == git(repo, "rev-parse", f"{head}^{{tree}}")
    body = gh.prs[100]["body"]
    assert rr.read_mark(body, rr.RANGE_END_MARK) == head and rr.read_mark(body, rr.SHOWN_MARK)


def test_a_release_branch_without_conflicts_opens_nothing(env):
    """受け入れ条件 4・I8: 衝突の無いマージだけなら空で、ブランチも PR も作らない。"""
    repo, gh = env
    build_release(repo, ["s1", "s3"], conflict=False)
    got = rr.open_review(repo, VERSION, "resolution", "main", gh)
    assert got["review_pr"] is None and not gh.prs and SHOW not in remote_heads(repo)


def test_pr_diff_reviews_the_whole_range_from_production(env):
    repo, gh = env
    head = build_release(repo, ["s1", "s3"], conflict=False)
    got = rr.open_review(repo, VERSION, "pr", "main", gh)
    git(repo, "fetch", "-q", "origin")
    assert git(repo, "rev-parse", f"origin/{BASE}") == git(repo, "rev-parse", "origin/main")
    assert git(repo, "rev-parse", f"origin/{SHOW}") == head and got["files"] == 2


def test_close_review_moves_fixes_and_the_next_open_finds_nothing(env):
    """I9・I10: 直しをリリースブランチへ移し、送った後の先頭をレビュー済みとして書く。次の review-open は空になる。"""
    repo, gh = env
    build_release(repo, ["s1", "s2"])
    rr.open_review(repo, VERSION, "resolution", "main", gh)
    fix = push_fix(repo, SHOW, "f.txt", "a\nB1 and B2\nc\n")
    got = rr.close_review(repo, VERSION, gh)
    git(repo, "fetch", "-q", "origin")
    tip = git(repo, "rev-parse", f"origin/{RELEASE}")
    assert got["moved"] == 1 and got["head"] == tip and got["reviewed"] == tip
    assert (git(repo, "show", f"{tip}:f.txt")) == "a\nB1 and B2\nc"
    assert git(repo, "log", "-1", "--format=%s", tip) == git(repo, "log", "-1", "--format=%s", fix)
    assert gh.prs[100]["state"] == "CLOSED" and rr.read_mark(gh.prs[100]["body"], rr.REVIEWED_MARK) == tip
    assert not {SHOW, BASE} & remote_heads(repo)
    again = rr.open_review(repo, VERSION, "resolution", "main", gh)
    assert again["review_pr"] is None and again["from"] == tip and gh.open_numbers() == []


def test_close_review_after_a_merge_was_added_records_the_range_end_and_stops(env):
    """I10: レビューの後に足したマージがあれば、範囲の終わりを書いて閉じた後に止まる。次の範囲は範囲の終わりから。"""
    repo, gh = env
    head = build_release(repo, ["s1", "s2"])
    rr.open_review(repo, VERSION, "pr", "main", gh)
    add_merge(repo, "s3")
    with pytest.raises(StepError, match="review-open から打ち直す"):
        rr.close_review(repo, VERSION, gh)
    assert rr.read_mark(gh.prs[100]["body"], rr.REVIEWED_MARK) == head and gh.open_numbers() == []
    nxt = rr.open_review(repo, VERSION, "pr", "main", gh)
    assert nxt["from"] == head and nxt["files"] == 1


def test_rerun_of_close_after_the_close_failed_skips_moved_fixes(env):
    """I10: 直しを送った後に PR を閉じられず止まっても、打ち直しは移し済みの直しを移し直さずに閉じる。"""
    repo, gh = env
    build_release(repo, ["s1", "s2"])
    rr.open_review(repo, VERSION, "resolution", "main", gh)
    push_fix(repo, SHOW, "f.txt", "a\nB12\nc\n")
    gh.fail_close = True
    with pytest.raises(StepError):
        rr.close_review(repo, VERSION, gh)
    gh.fail_close = False
    got = rr.close_review(repo, VERSION, gh)
    assert got["moved"] == 0 and got["reviewed"] == got["head"] and gh.open_numbers() == []


def test_cherry_pick_conflict_leaves_the_release_branch_and_the_pr(env):
    """I9: 直しを移せなければリリースブランチを動かさず、PR と一時のブランチを残す。"""
    repo, gh = env
    build_release(repo, ["s1", "s2"])
    rr.open_review(repo, VERSION, "resolution", "main", gh)
    push_fix(repo, SHOW, "f.txt", "a\nX\nc\n")
    git(repo, "checkout", "-q", RELEASE)
    commit(repo, "f.txt", "a\nY\nc\n", "moved")
    git(repo, "push", "-q", "origin", RELEASE)
    before = git(repo, "rev-parse", "HEAD")
    with pytest.raises(StepError, match="移せない"):
        rr.close_review(repo, VERSION, gh)
    git(repo, "fetch", "-q", "origin")
    assert git(repo, "rev-parse", f"origin/{RELEASE}") == before
    assert gh.open_numbers() == [100] and {SHOW, BASE} <= remote_heads(repo)


def test_rerun_of_open_closes_a_review_pr_without_fixes(env):
    """I11: 直しの無いレビューの PR を開いたまま打ち直すと、前の PR を閉じて開いているものは 1 つになる。"""
    repo, gh = env
    build_release(repo, ["s1", "s2"])
    rr.open_review(repo, VERSION, "resolution", "main", gh)
    got = rr.open_review(repo, VERSION, "resolution", "main", gh)
    assert got["closed"] == [100] and gh.open_numbers() == [got["review_pr"]]


def test_open_reuses_a_pr_with_pending_fixes_for_the_same_head(env):
    """I11: 同じ版で範囲の終わりが先頭と同じ、移していない直しを持つ PR は使い直す。"""
    repo, gh = env
    build_release(repo, ["s1", "s2"])
    rr.open_review(repo, VERSION, "resolution", "main", gh)
    push_fix(repo, SHOW, "f.txt", "a\nB12\nc\n")
    got = rr.open_review(repo, VERSION, "resolution", "main", gh)
    assert got["review_pr"] == 100 and got["reused"] and gh.open_numbers() == [100]


def test_open_stops_on_pending_fixes_when_the_head_moved(env):
    """I11: 範囲の終わりが先頭と違う、移していない直しを持つ PR があれば、何も閉じずに止まる。"""
    repo, gh = env
    build_release(repo, ["s1", "s2"])
    rr.open_review(repo, VERSION, "resolution", "main", gh)
    push_fix(repo, SHOW, "f.txt", "a\nB12\nc\n")
    add_merge(repo, "s3")
    with pytest.raises(StepError, match="#100"):
        rr.open_review(repo, VERSION, "resolution", "main", gh)
    assert gh.open_numbers() == [100] and {SHOW, BASE} <= remote_heads(repo)


def test_open_stops_when_the_release_branch_of_an_open_pr_is_gone(env):
    """I11: 比べるリリースブランチを取得できない PR があれば止まる。"""
    repo, gh = env
    build_release(repo, ["s1", "s2"])
    rr.open_review(repo, VERSION, "resolution", "main", gh)
    gh.prs[100]["headRefName"] = rr.names("9.9.9")[1]
    with pytest.raises(StepError, match="取得できない"):
        rr.open_review(repo, VERSION, "resolution", "main", gh)
    assert gh.open_numbers() == [100]


def test_marks_round_trip():
    body = rr.with_mark("本文", rr.REVIEWED_MARK, "abc1234")
    assert rr.read_mark(rr.with_mark(body, rr.REVIEWED_MARK, "def5678"), rr.REVIEWED_MARK) == "def5678"
    assert rr.version_of(SHOW) == VERSION and rr.version_of(BASE) is None
