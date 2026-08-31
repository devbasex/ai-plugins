"""`scripts/` のテストのフィクスチャ。

検査は一時ディレクトリへ作った木に対して実行する。実物の説明文書は読むだけで、
書き換えない（実物を崩すテストは、失敗したときにリポジトリを壊れたまま残す）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from doc_staleness_helpers import build_tree


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """突き合わせ元と突き合わせ先が一致している状態の木。"""
    return build_tree(tmp_path)
