"""設計レビューの項目のルート（`spec_render.review_items`・`children` と、規則 `review-items`。#3684 の受け入れ条件 7）。"""

from __future__ import annotations

import json

import pytest

from ndf.checks import child_deps, spec_docs, spec_render
from ndf.checks.model import CheckError

ROUTED = ("1-1", "要旨", "S2", "10", "既存", "344", "11.0.0")
ELSEWHERE = ("1-2", "後で解く", "[11.x の対応表](later.md)", "—", "—", "無し", "11.x")


def _items(copy):
    return spec_render.review_items(copy.table, copy.spec["review_items_columns"])


def _regenerate(copy):
    """生成し直した中身にそろえ、ルートの指摘だけを残す。"""
    copy.write_children(_items(copy))


def test_rows_become_items(copy):
    copy.write_items(("1-1", "要旨", "S2・M3b", "2（比較の結論まで着手しない）", "既存", "344", "11.0.0"), ELSEWHERE)
    first, second = _items(copy)
    assert first == {
        "id": "1-1",
        "text_sha": first["text_sha"],
        "routed_to": ["S2", "M3b"],
        "order": 2,
        "kind": "既存",
        "requirement_line": 344,
        "release": "11.0.0",
    }
    assert second == {
        "id": "1-2",
        "text_sha": second["text_sha"],
        "routed_to": ["later.md"],
        "order": None,
        "kind": "—",
        "requirement_line": None,
        "release": "11.x",
    }
    assert len(first["text_sha"]) == spec_render.TEXT_SHA_LENGTH and first["text_sha"] != second["text_sha"]


def test_a_cell_may_hold_an_escaped_bar(copy):
    copy.write_items(("1-1", "a|b", *ROUTED[2:]))
    assert [i["routed_to"] for i in _items(copy)] == [["S2"]]


def test_an_item_without_a_route_is_kept_empty(copy):
    copy.write_items(ROUTED)
    copy.table.write_text(copy.table.read_text(encoding="utf-8").replace("| S2 |", "|  |"), encoding="utf-8")
    assert [i["routed_to"] for i in _items(copy)] == [[]]


@pytest.mark.parametrize("column", range(7))
def test_a_table_without_a_column_stops(copy, column):
    names = list(copy.spec["review_items_columns"].values())
    names.pop(column)
    copy.write_items(ROUTED[:column] + ROUTED[column + 1 :], columns=tuple(names))
    with pytest.raises(CheckError, match="項目の表を読めない"):
        copy.findings()


@pytest.mark.parametrize(
    "row",
    [
        ("1-1", "要旨", "S2", "二", "既存", "344", "11.0.0"),
        ("1-1", "要旨", "S2", "10", "既存", "どこか", "11.0.0"),
        ("1-1", "要旨", "S2", "10", "既存", "344"),
    ],
)
def test_a_broken_row_stops(copy, row):
    copy.write_items(row)
    with pytest.raises(CheckError, match="項目の表を読めない"):
        copy.findings()


def test_a_missing_table_stops(copy):
    copy.table.unlink()
    with pytest.raises(CheckError, match="項目の表を読めない"):
        copy.findings()


def test_the_generated_items_have_no_findings(copy):
    copy.write_items(ROUTED, ELSEWHERE)
    copy.write_correspondence("later.md", ("1-2", "Z1"))
    _regenerate(copy)
    assert copy.findings() == []


def test_one_item_without_a_route_is_one_finding(copy):
    copy.write_items(ROUTED, ("1-2", "要旨", *ROUTED[2:]))
    copy.table.write_text(copy.table.read_text(encoding="utf-8").replace("| S2 |", "|  |", 1), encoding="utf-8")
    _regenerate(copy)
    assert [(f.rule, f.path) for f in copy.findings()] == [(spec_docs.REVIEW_ITEMS, copy.spec["review_items_source"])]
    assert "1-1" in copy.findings()[0].detail


@pytest.mark.parametrize("rows", [(), (("9-9", "Z1"),), (("1-2", "—"),)])
def test_an_item_moved_to_11x_needs_a_route_in_the_correspondence(copy, rows):
    copy.write_items(ROUTED, ELSEWHERE)
    copy.write_correspondence("later.md", *rows)
    later = copy.table.parent / "later.md"
    later.write_text(later.read_text(encoding="utf-8").replace("| 1-2 | — |", "| 1-2 |  |"), encoding="utf-8")
    _regenerate(copy)
    assert [f.rule for f in copy.findings()] == [spec_docs.REVIEW_ITEMS]
    assert "1-2" in copy.findings()[0].detail and "later.md" in copy.findings()[0].detail


def test_a_missing_correspondence_stops(copy):
    copy.write_items(ELSEWHERE)
    with pytest.raises(CheckError, match="later.md"):
        copy.findings()


def test_children_without_review_items_is_one_finding_and_write_spec_restores_it(copy):
    copy.edit_json(child_deps.CHILDREN_REL, lambda d: d.pop("review_items"))
    assert [(f.rule, f.path) for f in copy.findings()] == [(spec_docs.DOCS, f"plugins/p/{child_deps.CHILDREN_REL}")]
    assert copy.findings(write_spec=True) == []
    assert json.loads(copy.path(child_deps.CHILDREN_REL).read_text(encoding="utf-8"))["review_items"] == _items(copy)


def test_a_hand_edited_item_is_caught(copy):
    path = copy.path(child_deps.CHILDREN_REL)
    path.write_text(path.read_text(encoding="utf-8").replace('"S2"', '"S3"'), encoding="utf-8")
    assert [(f.rule, f.path) for f in copy.findings()] == [(spec_docs.DOCS, f"plugins/p/{child_deps.CHILDREN_REL}")]


def test_write_spec_keeps_the_hand_written_part(copy):
    data = {"summary": "s", "children": {"X": {"needs": []}}, "tables": {"t.json": ["X"]}}
    copy.write(child_deps.CHILDREN_REL, spec_render.children(data, []))
    copy.findings(write_spec=True)
    held = json.loads(copy.path(child_deps.CHILDREN_REL).read_text(encoding="utf-8"))
    assert {k: v for k, v in held.items() if k != "review_items"} == data
    assert list(held) == ["summary", "children", "tables", "review_items"]
