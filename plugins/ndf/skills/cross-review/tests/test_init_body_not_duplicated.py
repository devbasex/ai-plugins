"""新規 init 経路の本体が 2 回現れないことを機械で見る。

**構造改善（`extract_method`）で旧本体の削除が漏れると、抽出後の本体がそのまま
2 回並ぶ。** 実際に PR #549 の `cmd_init` の抽出でこれが起き、`_fetch_pr_metadata` /
`gh api user` / `_fetch_changed_files` / `fetch-pr-comments.sh` / `_create_worktree` /
`auth.check_auth` が二重に走り、機械可読ブロック（`PR=…RESUMED=0`）が標準出力へ
2 回出ていた。

**経路そのものは `gh` を要するため実行では確かめない。** 関数の構造（同じ文が 2 回
現れない・出力が 1 回だけ）を構文木で見る。

各段はモジュールレベル関数へ切り出したため、`_init_new_state` 単体ではなく
新規 init 経路を構成する関数群（`INIT_PATH_FUNCTIONS`）をまとめて見る。
"""
from __future__ import annotations

import ast
import collections
import pathlib

import pytest

STATE_PY = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "state.py"

# 新規 init 経路を構成する関数群。`_init_new_state` から順に呼ばれる各段。
INIT_PATH_FUNCTIONS = (
    "_init_new_state",
    "_resolve_pr_and_ownership",
    "_prepare_review_instructions",
    "_prepare_worktree_and_comments",
    "_finalize_initial_state",
    "_prepare_initial_assignment",
    "_build_initial_review_state",
)

# 1 回しか呼んではいけないもの。**副作用を持つ**か、標準出力の機械可読ブロックを書く。
SINGLE_CALL = (
    "_print_init_result",
    "_fetch_pr_metadata",
    "_fetch_changed_files",
    "_tmp_dir",
)


@pytest.fixture(scope="module")
def init_functions() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(STATE_PY.read_text(encoding="utf-8"))
    found: dict[str, ast.FunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in INIT_PATH_FUNCTIONS:
            found[node.name] = node
    missing = [name for name in INIT_PATH_FUNCTIONS if name not in found]
    assert not missing, f"新規 init 経路の関数が見つからない: {missing}"
    return found


def test_no_statement_appears_twice(
    init_functions: dict[str, ast.FunctionDef],
) -> None:
    """各段の本体の直下に、まったく同じ文が 2 回並ばない。

    丸ごとの複製はこの形でしか起こらない（`if meta is None:` も
    `_print_init_result(...)` も 2 回現れていた）。
    """
    for name, func in init_functions.items():
        dumps = [ast.dump(stmt) for stmt in func.body]
        repeated = [d for d, n in collections.Counter(dumps).items() if n > 1]
        assert not repeated, (
            f"{name} の本体に同じ文が {len(repeated)} 種類、2 回以上現れる"
        )


@pytest.mark.parametrize("name", SINGLE_CALL)
def test_the_call_appears_once(
    init_functions: dict[str, ast.FunctionDef], name: str
) -> None:
    calls = [
        node
        for func in init_functions.values()
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    ]
    assert len(calls) == 1, f"{name} が {len(calls)} 回呼ばれている"
