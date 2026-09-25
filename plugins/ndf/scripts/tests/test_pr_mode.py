"""lib/pr_mode.py（#1005）: 宛て先の区別と、本文の末尾のモードの 1 行。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from pr_mode import needs_review, pr_target, split_stages, with_mode_line  # noqa: E402


def test_target_separates_mission_and_develop():
    assert pr_target("mission/v10-18") == "mission" and not needs_review("mission/v10-18")
    assert pr_target("develop") == "develop" and needs_review("develop")


def test_split_stages_accepts_commas_and_arrows():
    assert split_stages("設計, 実装、構造改善 → 実装レビュー") == ["設計", "実装", "構造改善", "実装レビュー"]
    assert split_stages(None) == []


def test_mode_line_goes_before_signature():
    body = "要約\n\n## テスト\n\n| a |\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n"
    got = with_mode_line(body, "standard", ["実装", "完了判定"])
    lines = [l for l in got.splitlines() if l.strip()]
    assert lines[-2] == "モード: standard / 通した工程: 実装 → 完了判定"
    assert lines[-1].startswith("🤖 Generated with")


def test_without_mode_body_is_unchanged_and_empty_stages_say_none():
    assert with_mode_line("本文\n", None, ["実装"]) == "本文\n"
    assert with_mode_line("本文\n", "light", []).rstrip().endswith("モード: light / 通した工程: 無し")
