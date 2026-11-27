"""設計の検査（`ndf records design check`。#2912 の受け入れ条件 4・決定 1）。

判定（`design_check_decide`）は文字列だけで、集め方（`design_check`）は Git のアダプターを差し替えた偽物で確かめる。git は起動しない。
"""

from __future__ import annotations

import pytest

from ndf.domains.records import design_check_decide as dc

TABLE = "\n".join(
    [
        "## 人へ残す判断",
        "",
        "| 判断 | 当たるレッドライン | なぜ当たるか |",
        "| --- | --- | --- |",
        "| 認可の役割を足す | `C2` | 認可を変える |",
    ]
)
ROWS = dc.decision_rows(TABLE)


def _doc(*lines: str, section: str = "決定の記録") -> str:
    return "\n".join(["# 設計", "", f"## {section}", "", *lines, "", TABLE, ""])


# --- 委ねる語（設計書の「入出力のコントラクト」の行の例） ---

HITS = [
    "利用者が決めます", "利用者が確認します", "利用者に委ねます", "利用者の判断を待つ", "利用者の判断を待ちます",
    "承認ゲート 1 で利用者が選定する", "承認ゲートで人が決めてから進める", "承認ゲートで利用者が確かめてからマージする",
    "承認ゲートで人が選択しないか確かめる",
    "利用者が決める", "人が決める", "利用者が後で決める", "承認ゲートで人が選ぶ", "承認ゲートで利用者が選択する", "利用者が確認する",
    "利用者に委ねる", "利用者に委ねている", "利用者に委ねたままにする", "利用者が決めるまで進めない",
    "利用者が決めることはないか確かめる", "利用者に委ねるのではないか", "利用者に委ねるのではなく人が決める",
]  # fmt: skip
MISSES = [
    "利用者が決めた", "承認ゲートで利用者が選んだ", "承認ゲート 1 で利用者が選定した", "利用者に委ねた結果、A を採った",
    "利用者が決めました", "利用者の判断を待った", "利用者の判断を待つ必要はない",
    "利用者が決める必要はない", "利用者が決めるまでもない", "利用者が決めることはない", "利用者に委ねるのではなく AI が決める",
    "承認ゲートで人が決めずに進める", "承認ゲートで人が選びません", "承認ゲートで人が選択しない", "承認ゲートで人が選ばない",
    "承認ゲートで利用者が選定せずに進める",
    "本人が決める", "個人が決める", "`利用者が決める` という語",
]  # fmt: skip


@pytest.mark.parametrize("line", HITS)
def test_delegating_lines_are_found(line: str) -> None:
    assert dc.delegates(line)
    assert [f.text for f in dc.findings(_doc(line), "d.md")] == [line]


@pytest.mark.parametrize("line", MISSES)
def test_settled_negated_and_non_delegating_lines_pass(line: str) -> None:
    assert not dc.delegates(line)
    assert dc.findings(_doc(line), "d.md") == []


# --- 番号を名指しした行 ---


@pytest.mark.parametrize(
    ("line", "found"),
    [
        ("認可の役割を足すかは C2 に当たるため、利用者が決める", False),
        ("認可の役割を足すかは `C2` に当たるため、利用者が決める", False),
        ("一覧の表示順は C2 を参照し、利用者が決める", True),
        ("認可の役割を足すかは C2 には当たらないため、利用者が決める", True),
        ("認可の役割を足すかは `C2` に該当しないため、利用者が決める", True),
        ("認可の役割を足すかは C6 に当たるため、利用者が決める", True),
    ],
)
def test_named_lines_need_the_row_decision_and_its_number(line: str, found: bool) -> None:
    assert bool(dc.findings(_doc(line), "d.md")) is found


def test_an_empty_decision_row_is_not_a_partner_and_is_found_itself() -> None:
    text = TABLE + "\n| | C2 | 理由 |\n\n一覧の表示順は C2 に当たるため、利用者が決める\n"
    found = dc.findings(text, "d.md")
    assert [f.line for f in found] == [6, 8]


@pytest.mark.parametrize(
    "cells", ["| | C2 | 理由 |", "| 判断 | | 理由 |", "| 判断 | 要確認 | 理由 |", "| 判断 | C2 | — |", "| ** | C2 | 理由 |"]
)
def test_table_rows_missing_a_cell_are_found(cells: str) -> None:
    found = dc.findings(TABLE + "\n" + cells + "\n", "d.md")
    assert [(f.path, f.line, f.text) for f in found] == [("d.md", 6, cells)]


def test_a_table_without_the_column_headers_is_found_as_missing_cells() -> None:
    text = "## 人へ残す判断\n\n| a | b |\n| --- | --- |\n| 認可 | C2 |\n"
    assert [f.line for f in dc.findings(text, "d.md")] == [5]
    assert dc.decision_rows(text) == [dc.DecisionRow(5, "", (), "")]


def test_redline_rows_returns_only_rows_with_numbers() -> None:
    text = TABLE + "\n| 表示順 | | 理由 |\n| 削除 | C4・`P1` | 戻せない |\n"
    assert [(r.decision, r.numbers) for r in dc.redline_rows(text)] == [("認可の役割を足す", ("C2",)), ("削除", ("C4", "P1"))]
    assert dc.redline_rows("# 題\n\n| 判断 | 当たるレッドライン | なぜ当たるか |\n| - | - | - |\n| a | C2 | b |\n") == []


def test_lines_outside_the_sections_and_inside_code_are_not_found() -> None:
    text = "\n".join(
        ["# 設計", "", "## 処理の流れ", "", "利用者が決める", "", "## 未確認のまま残ること", "", "```", "利用者が決める", "```", ""]
    )
    assert dc.findings(text, "d.md") == []
    nested = "## 決定の記録\n\n### 決定 1\n\n利用者に委ねる\n\n## 用語\n\n利用者に委ねる\n"
    assert [f.line for f in dc.findings(nested, "d.md")] == [5]


def test_lines_limit_the_findings_to_the_changed_lines() -> None:
    """差分の外の欄を欠く表の行と番号の無い委ね方は、`lines` の中に正常な行しか無ければ指摘しない。"""
    text = "\n".join(
        [
            "## 人へ残す判断",  # 1
            "",  # 2
            "| 判断 | 当たるレッドライン | なぜ当たるか |",  # 3
            "| --- | --- | --- |",  # 4
            "| 認可の役割を足す | C2 | 認可を変える |",  # 5
            "| 表示順 | | |",  # 6
            "",  # 7
            "利用者が決める",  # 8
            "認可の役割を足すかは C2 に当たるため、利用者が決める",  # 9
        ]
    )
    assert [f.line for f in dc.findings(text, "d.md")] == [6, 8]
    assert dc.findings(text, "d.md", frozenset({5, 9})) == []
    assert dc.findings(text, "d.md", frozenset()) == []


# --- 差分の行と見出し ---


def test_added_lines_reads_hunks_including_omitted_counts() -> None:
    patch = "diff --git a/d.md b/d.md\n--- a/d.md\n+++ b/d.md\n@@ -3,0 +4,2 @@\n+a\n+b\n@@ -9 +11 @@\n-x\n+y\n@@ -20,2 +22,0 @@\n-p\n-q\n"
    assert dc.added_lines(patch) == frozenset({4, 5, 11})
    assert dc.added_lines("@@ -1 +1 @@ x\n-a\n+b\n") == frozenset({1})
    assert dc.added_lines("") == frozenset()
    assert dc.added_lines("@@ broken @@\n+a\n") is None


def test_heading_touched_sees_added_changed_and_removed_headings() -> None:
    before = "# 題\n\n段落\n\n## 決定の記録\n"
    after = "# 題\n\n## 未確認のまま残ること\n\n段落\n\n## 決定の記録\n"
    assert dc.heading_touched(before, after, "@@ -1,0 +3,2 @@\n+## 未確認のまま残ること\n+\n")
    assert dc.heading_touched(before, "# 題\n\n段落\n\n## 用語\n", "@@ -5 +5 @@\n-## 決定の記録\n+## 用語\n")
    assert dc.heading_touched(before, "# 題\n\n段落\n", "@@ -4,2 +3,0 @@\n-\n-## 決定の記録\n")
    assert not dc.heading_touched(before, "# 題\n\n文\n\n## 決定の記録\n", "@@ -3 +3 @@\n-段落\n+文\n")
    assert dc.heading_touched(before, before, "@@ x @@\n")


def test_anchors_lost_when_a_partner_row_disappears() -> None:
    row = "| 認可の役割を足す | `C2` | 認可を変える |"
    before = TABLE
    assert not dc.anchors_lost(before, before + "\n| 削除 | C4 | 戻せない |")
    assert not dc.anchors_lost(before, before.replace("認可を変える", "役割の付与を変える"))
    assert dc.anchors_lost(before, before.replace(row, ""))
    assert dc.anchors_lost(before, before.replace("認可の役割を足す", "役割を足す"))
    assert dc.anchors_lost(before, before.replace("`C2`", "C6"))
    assert dc.anchors_lost(before, before.replace("認可を変える", "—"))
    assert not dc.anchors_lost("# 題\n", "# 題\n")


def test_blank_and_numbers() -> None:
    assert dc.blank(" ** ") and dc.blank("—") and dc.blank("`-`") and not dc.blank("要確認")
    assert dc.numbers_in("C2 と P10、R3 と AC1 と C2", negatable=False) == ("C2", "P10", "R3")
    assert dc.numbers_in("C2 に当たらない・C4 に当たる", negatable=True) == ("C4",)
