"""ベースブランチ（ブランチ戦略の宣言の `branches.base`。issue #202）と本番ブランチ（`branches.production`。#424）の解決に共通する振る舞い。

どちらも宣言（`.ndf/project.json` の `branch_strategy`。#3672）だけから読み、宣言が無い・旧いキーが残るときは既定ブランチへ倒さずに
止まる。宣言した枝があればそれを返す。鍵ごとに固有の振る舞いは `test_base_branch.py` と `test_production_branch.py` にある。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worktree_helpers import add_origin, push_branch, resolve, write_strategy

RESOLVERS = pytest.mark.parametrize(
    "fn, branches, branch",
    [
        ("wt_base_branch", {"base": "develop", "production": "main"}, "develop"),
        ("wt_production_branch", {"base": "develop", "production": "release"}, "release"),
    ],
)


@RESOLVERS
def test_without_declaration_does_not_fall_back_to_the_default_branch(main_repo: Path, fn: str, branches: dict, branch: str) -> None:
    add_origin(main_repo)
    out, _, rc = resolve(main_repo, fn)
    assert rc != 0
    assert out == ""


@RESOLVERS
def test_a_section_without_the_strategy_does_not_fall_back(main_repo: Path, fn: str, branches: dict, branch: str) -> None:
    add_origin(main_repo)
    (main_repo / ".ndf").mkdir(exist_ok=True)
    (main_repo / ".ndf" / "project.json").write_text(json.dumps({"version": 1}))
    out, _, rc = resolve(main_repo, fn)
    assert rc != 0
    assert out == ""


@RESOLVERS
def test_the_declared_branch_is_used(main_repo: Path, fn: str, branches: dict, branch: str) -> None:
    add_origin(main_repo)
    push_branch(main_repo, branch)
    write_strategy(main_repo, **branches)
    out, _, rc = resolve(main_repo, fn)
    assert rc == 0
    assert out == branch
