"""lib/strategy_rows.py（#3672 の D11。#3121・#3163 から移した）: 取り込み方の表の行・組み直しの判定・リリースブランチの組み方・
レビューの単位の既定値と覆いの検査。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS / "lib"), str(SCRIPTS)]
import strategy_rows as sr  # noqa: E402
from release_lib.strategy_route import _build_release_branch  # noqa: E402


def test_rows_are_named_by_release_strategy_values():
    """行の名前はセクションの `release_strategy` の値と同じで、旧い名前（whole・selected）は無い。"""
    assert set(sr.ROWS) == {"merge_base_branch", "release_branch", "trunk"}
    assert [n for n, r in sr.ROWS.items() if r.selects_units] == ["release_branch"]
    assert [n for n, r in sr.ROWS.items() if r.keeps_sprint_branch] == ["release_branch"]


def test_trunk_row_watches_only_the_production_branch():
    row = sr.ROWS["trunk"]
    assert row.moved({"base": "a", "production": "p"}, {"base": "b", "production": "p"}) == []
    assert row.moved({"production": "p"}, {"production": "q"}) == ["production"]


def test_merge_base_branch_row_rebuilds_only_when_the_base_branch_moved():
    """受け入れ条件 3 と同じ判定: 丸ごと出す行はベースブランチの先頭だけを比べる。"""
    row = sr.ROWS["merge_base_branch"]
    pres = {"base": "a", "production": "p", "units": {}}
    assert row.moved(pres, {"base": "a", "production": "q"}) == []
    assert row.moved(pres, {"base": "b"}) == ["base"]


def test_release_branch_row_ignores_the_base_branch_and_watches_units_and_production():
    """受け入れ条件 6・I7: ベースブランチだけが動いても組み直さない。選んだ単位か本番チャネルが動けば組み直す。"""
    row = sr.ROWS["release_branch"]
    pres = {"base": "a", "production": "p", "units": {"sprint/x": "1"}}
    assert row.moved(pres, {"base": "b", "production": "p", "units": {"sprint/x": "1"}}) == []
    assert row.moved(pres, {"base": "b", "production": "p", "units": {"sprint/x": "2"}}) == ["sprint/x"]
    assert row.moved(pres, {"production": "q", "units": {"sprint/x": "1"}}) == ["production"]


def test_release_branch_range_starts_at_the_previous_tag():
    """受け入れ条件 6: 選ぶ行の承認資料の範囲は前の本番のタグ..リリースブランチの先頭。"""
    assert sr.ROWS["release_branch"].release_range("origin/main", "v1.0.0", "abc") == ("v1.0.0", "abc")
    assert sr.ROWS["merge_base_branch"].release_range("origin/main", "v1.0.0", "abc") == ("origin/main", "abc")


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def commit(repo: Path, path: str, text: str, msg: str) -> str:
    (repo / path).write_text(text)
    git(repo, "add", path)
    git(repo, "commit", "-qm", msg)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def history(tmp_path):
    """リリース 9 の形: main から切った 2 つのスプリント・develop にだけ入った別のスプリントの PR と開発版の版上げ。"""
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    # 組む側（lib）は利用者の設定でコミットするため、一時リポジトリに持たせる
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(repo, "config", k, v)
    commit(repo, "base.txt", "0\n", "init")
    git(repo, "branch", "develop")
    for name in ("a", "b"):
        git(repo, "checkout", "-q", "-b", f"sprint/{name}", "main")
        commit(repo, f"{name}.txt", f"{name}\n", f"sprint {name}")
    git(repo, "checkout", "-q", "develop")
    for name in ("a", "b"):
        git(repo, "merge", "-q", "--no-ff", "--no-edit", f"sprint/{name}")
    other = commit(repo, "other.txt", "x\n", "PR #3078 aixrepo-1")
    bump = commit(repo, "version.txt", "1.1.0-dev.1\n", "chore: 1.1.0-dev.1")
    return repo, other, bump


def test_release_branch_holds_only_the_selected_units(history):
    """受け入れ条件 5・16・17・I5: 本番チャネルから切り、選んだ単位だけを集める。選ばない単位・develop にだけある PR・
    開発版の版上げはリリースブランチの祖先に入らない。"""
    repo, other, bump = history
    row = sr.ROWS["release_branch"]
    head = _build_release_branch(row, repo, "release/v1.1.0", "main", ["sprint/a"])
    anc = lambda c: subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", c, head]).returncode == 0  # noqa: E731
    assert anc("sprint/a") and anc("main")
    assert not anc("sprint/b") and not anc(other) and not anc(bump)
    # 本番チャネルの先頭と、選んだ単位のマージだけからなる
    assert git(repo, "rev-list", "--first-parent", "--count", f"main..{head}") == "1"


def test_release_branch_stops_on_conflicts(history):
    """E10: 選んだ単位どうしが衝突すれば取り消して止める。"""
    repo, _, _ = history
    for name in ("c", "d"):
        git(repo, "checkout", "-q", "-b", f"sprint/{name}", "main")
        commit(repo, "same.txt", f"{name}\n", f"sprint {name}")
    with pytest.raises(sr.ReleaseBuildError):
        _build_release_branch(sr.ROWS["release_branch"], repo, "release/v2", "main", ["sprint/c", "sprint/d"])
    assert git(repo, "status", "--porcelain") == ""


def test_release_branch_reuses_rerere_resolution(history):
    """E10: 人が一度解いた衝突は、rerere の記録で解けたらマージを完了して次の単位へ進む。"""
    repo, _, _ = history
    for name in ("c", "d"):
        git(repo, "checkout", "-q", "-b", f"sprint/{name}", "main")
        commit(repo, "same.txt", f"{name}\n", f"sprint {name}")
    # 人が解いた記録を rerere に残す
    git(repo, "config", "rerere.enabled", "true")
    git(repo, "checkout", "-q", "--detach", "sprint/c")
    subprocess.run(["git", "-C", str(repo), "merge", "-q", "--no-edit", "sprint/d"], capture_output=True)
    (repo / "same.txt").write_text("c\nd\n")
    git(repo, "add", "same.txt")
    git(repo, "commit", "-q", "--no-edit")
    head = _build_release_branch(sr.ROWS["release_branch"], repo, "release/v2", "main", ["sprint/c", "sprint/d"])
    assert git(repo, "show", f"{head}:same.txt") == "c\nd"
    assert git(repo, "log", "-1", "--format=%s", head) == "Merge sprint/d into release/v2"


def test_release_branch_builds_while_branch_is_checked_out_elsewhere(history, tmp_path):
    """本番プランの worktree が同じリリースブランチを使っていても、detached HEAD で組み直せる。"""
    repo, _, _ = history
    git(repo, "branch", "release/v3", "main")
    git(repo, "worktree", "add", "-q", str(tmp_path / "prod"), "release/v3")
    before = git(repo, "rev-parse", "release/v3")
    head = _build_release_branch(sr.ROWS["release_branch"], repo, "release/v3", "main", ["sprint/a"])
    assert head != before
    assert git(repo, "rev-parse", "release/v3") == before


def test_merge_base_branch_row_does_not_build_release_branches(history):
    with pytest.raises(ValueError):
        _build_release_branch(sr.ROWS["merge_base_branch"], history[0], "release/v1", "main", ["sprint/a"])


def test_release_branch_keeps_sprint_branches_until_production_holds_them(history):
    """決定 7: 本番チャネルに含まれないスプリントブランチは残し、含まれたものは消してよい。丸ごと出す行は残さない。"""
    repo, _, _ = history
    sel, whole = sr.ROWS["release_branch"], sr.ROWS["merge_base_branch"]
    assert sr.sprint_branch_kept(repo, "sprint/a", "main", sel)
    assert not sr.sprint_branch_kept(repo, "sprint/a", "main", whole)
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "--no-edit", "sprint/a")
    assert not sr.sprint_branch_kept(repo, "sprint/a", "main", sel)


# --- レビューの単位（#3163） ---------------------------------------------------------------


def test_written_rows_are_returned_regardless_of_bundles():
    """書いた値を読み分けが返す。checks は CHECKS の順に並べ直す。"""
    written = {
        "impl": {"checks": ["cross-review"], "diff": "pr"},
        "sprint": {"checks": ["behavior", "cross-refactoring"], "diff": "unchecked"},
        "design": {"checks": [], "diff": "none"},
    }
    units = sr.reviews_of(written)
    for kind, v in written.items():
        for bundled in (True, False):
            row = units.row(kind, bundled)
            assert set(row.checks) == set(v["checks"]) and row.diff == v["diff"]
    assert units.row("sprint", True).checks == ("cross-refactoring", "behavior")


def test_unwritten_kinds_resolve_from_the_default_table():
    """I5: reviews の無いセクションは既定値の表の 10 セル。"""
    units = sr.reviews_of(None)
    got = {(k, b): (units.row(k, b).checks, units.row(k, b).diff) for k in sr.REVIEW_KINDS for b in (True, False)}
    assert got == {
        ("impl", True): (("cross-review",), "pr"),
        ("impl", False): ((), "none"),
        ("sprint", True): (("cross-refactoring", "behavior"), "unchecked"),
        ("sprint", False): (sr.CHECKS, "unchecked"),
        ("release", True): ((), "none"),
        ("release", False): ((), "none"),
        ("design", True): (("cross-review",), "pr"),
        ("design", False): (("cross-review",), "pr"),
        ("spec", True): ((), "none"),
        ("spec", False): ((), "none"),
    }


def test_covers_impl_counts_only_the_covering_diffs():
    assert sr.ReviewRow("impl", ("cross-review",), "pr").covers_impl()
    assert not sr.ReviewRow("release", ("cross-review",), "resolution").covers_impl()
    assert not sr.ReviewRow("design", ("cross-review",), "pr").covers_impl()
    assert not sr.ReviewRow("impl", (), "none").runs("cross-review")


@pytest.mark.parametrize(
    "reviews",
    [
        {"impl": {"checks": ["cross-review"], "diff": "none"}},
        {"spec": {"checks": [], "diff": "pr"}},
    ],
)
def test_checks_and_diff_that_disagree_stop(reviews):
    assert sr.review_problems(reviews)


@pytest.mark.parametrize(
    "reviews",
    [
        {"sprint": {"checks": ["behavior"], "diff": "unchecked"}},
        {
            "impl": {"checks": [], "diff": "none"},
            "sprint": {"checks": ["cross-refactoring"], "diff": "unchecked"},
            "release": {"checks": ["cross-review"], "diff": "resolution"},
        },
    ],
)
def test_reviews_that_leave_impl_diff_unreviewed_stop(reviews):
    """I4: どの PR でも cross-review されない実装の差分が出る値は止まる。"""
    got = sr.review_problems(reviews)
    assert got and all("cross-review されない" in g for g in got)


def test_release_pr_diff_covers_impl():
    reviews = {
        "impl": {"checks": [], "diff": "none"},
        "sprint": {"checks": ["behavior"], "diff": "unchecked"},
        "release": {"checks": ["cross-review"], "diff": "pr"},
    }
    assert sr.review_problems(reviews) == []
    assert sr.reviews_of(reviews).row("release", True).covers_impl()


def test_default_overlapping_a_written_row_stops_but_written_overlaps_pass():
    """I12: impl だけに cross-review を書くと既定値の sprint と重なり止まる。両方に書けば通る。"""
    got = sr.review_problems({"impl": {"checks": ["cross-review"], "diff": "pr"}})
    assert got and all(g.startswith("reviews.sprint") for g in got)
    both = {"impl": {"checks": ["cross-review"], "diff": "pr"}, "sprint": {"checks": list(sr.CHECKS), "diff": "unchecked"}}
    assert sr.review_problems(both) == []
