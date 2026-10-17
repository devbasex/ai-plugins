"""release_lib/materials.py の unconfirmed_items: 設計文書の `## 未確認のまま残ること` の項目（現状固定）。

表の経路は test_release_materials.py が通す。ここは箇条の経路・「無し」の除外・節が無いときを固定する。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from release_lib import materials  # noqa: E402


def test_bullets_become_items():
    text = "# d\n\n## 未確認のまま残ること\n\n- 置き場が未定\n*   所要が増える\n地の文は拾わない\n  - 字下げの箇条\n"
    assert materials.unconfirmed_items(text) == ["置き場が未定", "所要が増える", "字下げの箇条"]


def test_none_bullet_is_dropped():
    assert materials.unconfirmed_items("## 未確認のまま残ること\n\n- 無し。\n- なし\n- 残る\n") == ["残る"]


def test_table_row_without_content_keeps_item_only():
    text = "## 未確認のまま残ること\n\n| 項目 | 内容 |\n| --- | --- |\n| 置き場 |  |\n|  | 内容だけ |\n| 無し | |\n"
    assert materials.unconfirmed_items(text) == ["置き場"]


def test_no_section_is_empty():
    assert materials.unconfirmed_items("# d\n\n## 別の節\n\n- 項目\n") == []
    assert materials.unconfirmed_items("") == []
