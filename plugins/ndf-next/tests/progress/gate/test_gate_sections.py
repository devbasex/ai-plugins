"""MVV 判定の前の検査 6: 対応表が「記載した」と書く PR 本文の節に中身があるか（#2853）。"""

from __future__ import annotations

from dataclasses import replace

from ndf.progress import gate

SHA = "a" * 64
FACTS = gate.MvvFacts(status="approved", current=SHA, approved=SHA, referenced=SHA, mode="standard")
BODY = "## 概要\n\n設計を足した\n\n## テスト\n\n- requirements-check: 終了コード 0\n\n## 出し直しの指摘への対応\n\n| 指摘 | 対応 |\n"


def test_sections_split_the_body_by_headings():
    held = gate.sections(BODY)
    assert held["テスト"] == "- requirements-check: 終了コード 0"
    assert set(held) == {"概要", "テスト", "出し直しの指摘への対応"}


def test_claimed_section_with_content_passes():
    assert gate.check_claimed_sections(replace(FACTS, body=BODY, claimed=("テスト",))) is None
    assert gate.refusals(replace(FACTS, body=BODY, claimed=("テスト",))) == []


def test_claimed_section_that_is_empty_or_missing_is_refused():
    body = "## 概要\n\n設計を足した\n\n## テスト\n\n## 出し直しの指摘への対応\n\n| 指摘 | 対応 |\n"
    why = gate.check_claimed_sections(replace(FACTS, body=body, claimed=("テスト", "再現")))
    assert why is not None and "テスト" in why and "再現" in why
    skip, reasons = gate.may_skip(
        replace(FACTS, body=body, claimed=("テスト",)), {"verdict": "follow", "reasons": ["Value 1"], "boundary": []}
    )
    assert skip is False and reasons == [why.replace(" / 再現", "")]


def test_nothing_claimed_needs_no_body():
    assert gate.check_claimed_sections(FACTS) is None
