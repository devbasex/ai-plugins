"""state.py の追加レビュー観点オプションのテスト。"""

from __future__ import annotations

import argparse


def test_extra_review_instructions_accepts_focus(state_mod):
    args = argparse.Namespace(
        focus="ドキュメントとコードの整合性を重点的に確認",
        extra_instructions_file=None,
    )

    assert state_mod._extra_review_instructions(args) == "ドキュメントとコードの整合性を重点的に確認"


def test_extra_review_instructions_combines_focus_and_file(tmp_path, state_mod):
    extra = tmp_path / "focus.md"
    extra.write_text("公開 API の説明と実装差分も確認\n", encoding="utf-8")
    args = argparse.Namespace(
        focus="ドキュメント整合性",
        extra_instructions_file=str(extra),
    )

    assert state_mod._extra_review_instructions(args) == (
        "ドキュメント整合性\n\n公開 API の説明と実装差分も確認"
    )


def test_extra_review_instructions_ignores_empty_values(tmp_path, state_mod):
    extra = tmp_path / "empty.md"
    extra.write_text("  \n", encoding="utf-8")
    args = argparse.Namespace(focus="  ", extra_instructions_file=str(extra))

    assert state_mod._extra_review_instructions(args) == ""
