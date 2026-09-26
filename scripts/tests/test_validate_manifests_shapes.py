"""定義ファイルの項目の形の誤り（scripts/lib/validate_manifests.py。#1142 の D8 で lib/schema.py へ移した）。

移す前に isinstance で見ていた項目の誤りが、同じ文言の ERROR になることを固定する。
"""
from __future__ import annotations

import json
from pathlib import Path

from validate_manifests_helpers import FAMILY, build_tree, output_of, run_check


def _edit(path: Path, **changes) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_the_shapes_of_the_real_fixture_pass(tmp_path: Path) -> None:
    result = run_check(build_tree(tmp_path), tmp_path)
    assert result.returncode == 0, output_of(result)


def test_a_marketplace_source_that_is_not_a_string_is_reported(tmp_path: Path) -> None:
    root = build_tree(tmp_path)
    market = root / ".claude-plugin/marketplace.json"
    data = json.loads(market.read_text(encoding="utf-8"))
    data["plugins"][0]["source"] = 3
    market.write_text(json.dumps(data), encoding="utf-8")
    out = output_of(run_check(root, tmp_path))
    assert f"ERROR: .claude-plugin marketplace plugin {FAMILY} has invalid source" in out


def test_a_claude_version_that_is_not_a_string_is_reported(tmp_path: Path) -> None:
    root = build_tree(tmp_path)
    _edit(root / f"plugins/{FAMILY}/.claude-plugin/plugin.json", version=9)
    assert f"ERROR: {FAMILY} の claude plugin.json に version がない" in output_of(run_check(root, tmp_path))


def test_a_skills_field_that_is_not_an_array_is_reported(tmp_path: Path) -> None:
    root = build_tree(tmp_path)
    _edit(root / f"plugins/{FAMILY}/.codex-plugin/plugin.json", skills="./skills/")
    out = output_of(run_check(root, tmp_path))
    assert f"ERROR: {FAMILY} の codex plugin.json の skills が配列ではない（実際: str）" in out


def test_a_skills_entry_that_is_not_a_string_is_reported_and_the_rest_are_compared(tmp_path: Path) -> None:
    root = build_tree(tmp_path)
    path = root / f"plugins/{FAMILY}/.codex-plugin/plugin.json"
    skills = json.loads(path.read_text(encoding="utf-8"))["skills"]
    _edit(path, skills=[*skills, 7])
    out = output_of(run_check(root, tmp_path))
    assert "ERROR: codex plugin.json の skills 配列に文字列以外の項目がある（int）" in out
    assert "載っていない" not in out and "余分な項目" not in out
