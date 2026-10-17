"""要求の検証手段に「手動確認は無い」と書いた行を手動確認の行にしない（#1675）。

`rows_of` が落とした行は、PR 本文の「手動確認」の節にも merge-gate の数え方（`unchecked_before_merge`）にも届かない。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import manual_checks  # noqa: E402


def requirement(*rows: tuple[str, str]) -> str:
    table = "\n".join(f"| {item} | {what} |" for item, what in rows)
    return f"# 要求\n\n## 検証手段\n\n| 項目 | 手段 |\n| --- | --- |\n| テスト | `pytest` |\n{table}\n"


def pr_body(text: str) -> str:
    return "## 概要\n\nx\n\n" + manual_checks.manual_section(manual_checks.rows_of(text, 7))


def test_a_none_row_makes_no_section_and_no_gate():
    """1675-1・1675-3: 「無し（…）」だけの課題は行が 0 件で、節を置かず、merge-gate が数える行も無い。"""
    text = requirement(("手動確認（マージ前）", "無し（AC はテストで確かめられる）"))
    assert manual_checks.rows_of(text, 7) == []
    assert manual_checks.manual_section([]) == ""
    assert manual_checks.unchecked_before_merge(pr_body(text)) == []


@pytest.mark.parametrize("item", ["手動確認", "手動確認（マージ前）"])
@pytest.mark.parametrize(
    "what",
    [
        "無し",
        "なし",
        "不要",
        "ない",
        "-",
        "—",
        "―",
        "なし（自動で足りる）",
        "不要。",
        "- 無い",
        "不要(テストで足りる)",
        "ない、テストで足りる",
    ],
)
def test_none_forms_are_not_manual_rows(item, what):
    """1675-2: 「無し」の語の単独と、直後が括弧・句読点・空白の形は、どちらの項目でも行にならない。"""
    assert manual_checks.rows_of(requirement((item, what)), 7) == []


@pytest.mark.parametrize("what", ["無しの場合は画面で見る", "画面で一覧の並びを見る"])
def test_real_checks_still_stop_the_merge_gate(what):
    """1675-4: 語の続きがある文と実際の確認の文は、今どおりマージ前の未チェック行になり merge-gate が数える。"""
    text = requirement(("手動確認（マージ前）", what))
    assert manual_checks.rows_of(text, 7) == [manual_checks.ManualRow(7, what, True)]
    assert manual_checks.unchecked_before_merge(pr_body(text)) == [f"#7 マージ前: {what}"]
