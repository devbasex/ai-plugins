"""MCP プラグインの Codex の定義（`mcpServers`）のチェック。

Codex だけ別の起動定義を読ませるプラグインがある（mcp-serena の `.codex.mcp.json`。#818）。
指す先が実在すれば通し、無い・形が違えば落とす。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from validate_manifests_helpers import build_tree, output_of, run_check


def _mcp_plugin(root: Path, declared, files=(".mcp.json",)) -> None:
    plugin = root / "plugins/mcp/mcp-x"
    for rel in (".claude-plugin", ".codex-plugin", "dev.kiro"):
        (plugin / rel).mkdir(parents=True)
    for name in files:
        (plugin / name).write_text('{"mcpServers": {}}\n', encoding="utf-8")
    (plugin / ".claude-plugin/plugin.json").write_text('{"name": "mcp-x"}\n', encoding="utf-8")
    (plugin / ".codex-plugin/plugin.json").write_text(
        json.dumps({"name": "mcp-x", "mcpServers": declared}), encoding="utf-8")
    (plugin / "dev.kiro/install.sh").write_text("#!/bin/sh\n", encoding="utf-8")


@pytest.mark.parametrize("declared,files", [
    ("./.mcp.json", (".mcp.json",)),
    ("./.codex.mcp.json", (".mcp.json", ".codex.mcp.json")),
])
def test_existing_mcp_definition_passes(tmp_path, declared, files):
    root = build_tree(tmp_path / "tree")
    _mcp_plugin(root, declared, files)
    result = run_check(root, tmp_path)
    assert result.returncode == 0, output_of(result)


@pytest.mark.parametrize("declared,files", [
    (None, (".mcp.json",)),
    ("./.codex.mcp.json", (".mcp.json",)),
    ("./other.json", (".mcp.json", "other.json")),
    ("../.mcp.json", (".mcp.json",)),
])
def test_missing_or_misnamed_definition_fails(tmp_path, declared, files):
    root = build_tree(tmp_path / "tree")
    _mcp_plugin(root, declared, files)
    result = run_check(root, tmp_path)
    assert result.returncode == 1
    assert "mcpServers" in output_of(result)
