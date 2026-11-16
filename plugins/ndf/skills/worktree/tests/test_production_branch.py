"""本番ブランチの解決を検証する（#424・#3672）。

**ベースブランチと本番ブランチは別物である。** 本番ブランチはブランチ戦略の宣言の `branches.production` だけが決め、
ベースブランチ（`branches.base`）を流用しない。流用すると開発版のチャネルへのマージが本番の扱いになる（決定 10）。
宣言が無いリポジトリでは既定ブランチへ倒さずに止まる（I3）。
"""

from __future__ import annotations

from pathlib import Path

from worktree_helpers import add_origin, git, resolve, write_strategy


def test_the_base_branch_is_not_borrowed(main_repo: Path) -> None:
    add_origin(main_repo)
    git(main_repo, "branch", "develop")
    write_strategy(main_repo, base="develop", production="main")
    out, _, rc = resolve(main_repo, "wt_production_branch")
    assert rc == 0
    assert out == "main"


def test_a_branch_that_does_not_exist_is_refused(main_repo: Path) -> None:
    """本番ブランチを取り違えると、リリースの承認の対象が変わる。既定へ落とさない。"""
    add_origin(main_repo)
    git(main_repo, "branch", "develop")
    write_strategy(main_repo, base="develop", production="nowhere")
    _out, err, rc = resolve(main_repo, "wt_production_branch")
    assert rc == 1
    assert "branches.production" in err


def test_without_a_declaration_it_stops_instead_of_using_the_default(main_repo: Path) -> None:
    add_origin(main_repo)
    out, err, rc = resolve(main_repo, "wt_production_branch")
    assert rc != 0
    assert out == ""
    assert "branch-strategy" in err
