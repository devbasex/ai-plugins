"""hook の結線を検証する（受け入れ条件 6〜8 の各ランタイム）。

誘導の対象になる tool 名は共通ライブラリが 1 箇所で持つ。hook の matcher が
その一覧とずれると、判定を足しても hook が起動しない。実際に起きた不具合
（Codex CLI の `apply_patch` が matcher に無く、パッチ本文の解析が使われなかった）
を繰り返さないため、両者の一致を検査する。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from worktree_helpers import SCRIPTS_DIR, run_lib

HOOKS_DIR = SCRIPTS_DIR.parent / "hooks"
INSTALL_SH = SCRIPTS_DIR.parent / "dev.kiro" / "install.sh"


def matcher_from_lib() -> str:
    got = run_lib("wt_tool_matcher")
    return got.stdout.strip()


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_pretooluse_matcher_matches_the_library(runtime: str) -> None:
    config = json.loads((HOOKS_DIR / f"{runtime}.json").read_text(encoding="utf-8"))
    entries = config["hooks"]["PreToolUse"]
    matchers = {entry.get("matcher") for entry in entries}
    assert matcher_from_lib() in matchers, matchers


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_pretooluse_runs_the_guard(runtime: str) -> None:
    config = json.loads((HOOKS_DIR / f"{runtime}.json").read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for entry in config["hooks"]["PreToolUse"]
        for hook in entry["hooks"]
    ]
    assert any("worktree-guard.sh" in c for c in commands), commands


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_sessionstart_runs_the_session_script(runtime: str) -> None:
    config = json.loads((HOOKS_DIR / f"{runtime}.json").read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for entry in config["hooks"]["SessionStart"]
        for hook in entry["hooks"]
    ]
    assert any("worktree-session.sh" in c for c in commands), commands


def test_kiro_installer_wires_both_hooks() -> None:
    """Kiro CLI は導入スクリプトがエージェント定義へ書き込む。"""
    body = INSTALL_SH.read_text(encoding="utf-8")
    assert "worktree-session.sh" in body
    assert "worktree-guard.sh" in body
    assert '"userPromptSubmit"' in body
    assert 'hooks.setdefault("agentSpawn"' in body
