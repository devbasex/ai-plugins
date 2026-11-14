"""ブランチの運用の宣言（`.ndf/worktree.json` の `branch_flow`）を取り込み方の表（branch-flows）で照らす（D2+。#3378）。

`flow_of` はこのテストの中の最小の写しで、宣言を読む子が同じ規則で表を読む。運用の違うリポジトリの宣言
（ベースブランチを丸ごと出す ai-plugins と、選んだスプリントを本番チャネルから出すリポジトリ）を見本にする。
"""

from __future__ import annotations

from typing import Any

import pytest

from ndf.contracts import facts

FLOWS = "branch-flows"
CHOICES = ("start", "doc_prs", "integrate")
KEYS = {"promotion", *CHOICES, "reviews"}


class FlowError(ValueError):
    """宣言が表に合わない。既定へ倒さずに止める。"""


def flow_of(decl: dict[str, Any] | None) -> dict[str, Any]:
    """宣言の `branch_flow` を、表の行と選んだ値に解く。無ければ既定の行の各列の先頭。"""
    (default,) = facts.rows(FLOWS, default=True)
    if decl is None:
        return {"row": default["id"], **{c: default[c][0] for c in CHOICES}}
    unknown = set(decl) - KEYS
    if unknown:
        raise FlowError(f"知らないキー: {sorted(unknown)}")
    row = _row_for(decl, default)
    return {"row": row["id"], **_picked(decl, row)}


def _row_for(decl: dict[str, Any], default: dict[str, Any]) -> dict[str, Any]:
    """宣言の取り込み方（無ければ既定の行の取り込み方）の表の行。表に無ければ止める。"""
    rows = facts.rows(FLOWS, promotion=decl.get("promotion", default["promotion"]))
    if not rows:
        raise FlowError(f"取り込み方が表に無い: {decl.get('promotion')!r}")
    return rows[0]


def _picked(decl: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """行に対して選んだ値（無ければ行の各列の先頭）。行が許さない値・書けないレビューの単位なら止める。"""
    picked = {c: decl.get(c, row[c][0]) for c in CHOICES}
    bad = [c for c in CHOICES if picked[c] not in row[c]]
    if bad:
        raise FlowError(f"{row['promotion']} が許さない値: {bad}")
    kinds = set(decl.get("reviews", {})) - set(row["review_kinds"])
    if kinds:
        raise FlowError(f"{row['promotion']} で書けないレビューの単位: {sorted(kinds)}")
    return picked


def test_one_default_row_and_values_come_from_the_table():
    assert len(facts.rows(FLOWS, default=True)) == 1
    assert flow_of(None) == {"row": "promote-whole", "start": "base", "doc_prs": "base", "integrate": "check"}


def test_selected_flow_of_another_repository():
    decl = {"promotion": "selected", "start": "production", "doc_prs": "sprint", "reviews": {"release": {"checks": ["cross-review"]}}}
    assert flow_of(decl) == {"row": "promote-selected", "start": "production", "doc_prs": "sprint", "integrate": "check"}
    assert facts.value(FLOWS, "promote-selected", "keeps_sprint") is True
    assert facts.value(FLOWS, "promote-selected", "release_from") == "production"


@pytest.mark.parametrize(
    "decl",
    [
        {"promotion": "selected", "start": "base"},
        {"promotion": "whole", "reviews": {"release": {}}},
        {"promotion": "merge"},
        {"promotion": "whole", "squash": True},
    ],
)
def test_declarations_outside_the_table_stop(decl):
    with pytest.raises(FlowError):
        flow_of(decl)


def test_release_reviews_only_where_units_are_selected():
    for r in facts.rows(FLOWS):
        assert ("release" in r["review_kinds"]) is r["release_units"], r["id"]


def test_branch_flow_is_read_from_the_main_directory():
    assert "branch_flow" in facts.value("ndf-files", "worktree", "main_keys")
