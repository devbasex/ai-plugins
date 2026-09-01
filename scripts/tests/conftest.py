"""`scripts/` のテストのフィクスチャ。

検査は一時ディレクトリへ作った木に対して実行する。実物の説明文書は読むだけで、
書き換えない（実物を崩すテストは、失敗したときにリポジトリを壊れたまま残す）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from branch_repo_helpers import init_origin_repo
from doc_staleness_helpers import build_tree


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """突き合わせ元と突き合わせ先が一致している状態の木。"""
    return build_tree(tmp_path)


@pytest.fixture()
def origin_repo(tmp_path: Path) -> Path:
    """origin を持つリポジトリ。既定ブランチは main で、他のブランチは無い。"""
    return init_origin_repo(tmp_path)
