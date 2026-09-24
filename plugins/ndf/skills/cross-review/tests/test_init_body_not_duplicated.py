"""`_init_new_state` の本体が 2 回現れないことを機械で見る。

**構造改善（`extract_method`）で旧本体の削除が漏れると、抽出後の本体がそのまま
2 回並ぶ。** 実際に PR #549 の `cmd_init` の抽出でこれが起き、`_fetch_pr_metadata` /
`gh api user` / `_fetch_changed_files` / `fetch-pr-comments.sh` / `_create_worktree` /
`auth.check_auth` が二重に走り、機械可読ブロック（`PR=…RESUMED=0`）が標準出力へ
2 回出ていた。

**経路そのものは `gh` を要するため実行では確かめない。** 関数の構造（同じ文が 2 回
現れない・出力が 1 回だけ）を構文木で見る。
"""
from __future__ import annotations

import ast
import collections
import pathlib

import pytest

STATE_PY = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "state.py"

# 1 回しか呼んではいけないもの。**副作用を持つ**か、標準出力の機械可読ブロックを書く。
SINGLE_CALL = (
    "_print_init_result",
    "_fetch_pr_metadata",
    "_fetch_changed_files",
    "_tmp_dir",
)

# `_init_new_state` から切り出した段。呼び出しの数は、本体とこの段を合わせて数える。
INIT_STEPS = (
    "_resolve_pr_and_ownership",
    "_prepare_review_instructions",
    "_prepare_worktree_and_comments",
    "_prepare_initial_assignment",
    "_build_initial_review_state",
    "_finalize_initial_state",
)


@pytest.fixture(scope="module")
def top_level_functions() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(STATE_PY.read_text(encoding="utf-8"))
    return {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }


@pytest.fixture(scope="module")
def init_new_state(top_level_functions: dict[str, ast.FunctionDef]) -> ast.FunctionDef:
    if "_init_new_state" not in top_level_functions:
        raise AssertionError("_init_new_state が見つからない")
    return top_level_functions["_init_new_state"]


def test_no_statement_appears_twice(init_new_state: ast.FunctionDef) -> None:
    """本体の直下に、まったく同じ文が 2 回並ばない。

    丸ごとの複製はこの形でしか起こらない（`if meta is None:` も
    `_print_init_result(...)` も 2 回現れていた）。
    """
    dumps = [ast.dump(stmt) for stmt in init_new_state.body]
    repeated = [d for d, n in collections.Counter(dumps).items() if n > 1]
    assert not repeated, (
        f"_init_new_state の本体に同じ文が {len(repeated)} 種類、2 回以上現れる"
    )


@pytest.mark.parametrize("name", SINGLE_CALL)
def test_the_call_appears_once(
    top_level_functions: dict[str, ast.FunctionDef], name: str
) -> None:
    missing = [
        step for step in ("_init_new_state", *INIT_STEPS)
        if step not in top_level_functions
    ]
    assert not missing, f"{missing} が見つからない"
    calls = [
        node
        for step in ("_init_new_state", *INIT_STEPS)
        for node in ast.walk(top_level_functions[step])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    ]
    assert len(calls) == 1, f"{name} が {len(calls)} 回呼ばれている"
