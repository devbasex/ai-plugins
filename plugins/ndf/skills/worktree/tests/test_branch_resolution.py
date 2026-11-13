"""開発の起点（`base_branch`。issue #202）と本番のチャネル（`production_branch`。#424）の解決に共通する振る舞い。

どちらも宣言が無い・鍵が無いときは既定ブランチを返し、宣言した枝があればそれを返す。鍵ごとに固有の振る舞いは
`test_base_branch.py` と `test_production_branch.py` にある。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worktree_helpers import add_origin, push_branch, resolve, write_declaration

RESOLVERS = pytest.mark.parametrize(
    "fn, key, branch",
    [("wt_base_branch", "base_branch", "develop"), ("wt_production_branch", "production_branch", "release")],
)


@RESOLVERS
def test_without_declaration_uses_the_default_branch(main_repo: Path, fn: str, key: str, branch: str) -> None:
    add_origin(main_repo)
    out, _, rc = resolve(main_repo, fn)
    assert rc == 0
    assert out == "main"


@RESOLVERS
def test_without_the_key_uses_the_default_branch(main_repo: Path, fn: str, key: str, branch: str) -> None:
    add_origin(main_repo)
    write_declaration(main_repo, json.dumps({"version": 1}))
    out, _, rc = resolve(main_repo, fn)
    assert rc == 0
    assert out == "main"


@RESOLVERS
def test_the_declared_branch_is_used(main_repo: Path, fn: str, key: str, branch: str) -> None:
    add_origin(main_repo)
    push_branch(main_repo, branch)
    write_declaration(main_repo, json.dumps({"version": 1, key: branch}))
    out, _, rc = resolve(main_repo, fn)
    assert rc == 0
    assert out == branch
