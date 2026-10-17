"""MCP プラグインの Kiro 用 installer が default.json に `tools` を持たせることを固定する（#1873）。

Kiro CLI はエージェント定義の `tools` に挙がったツールだけをモデルへ見せる。欄が無いと
MCP サーバーへの接続は成功しても、MCP のツールを呼べない。

見るのは 3 つである。**新しく作る default.json に `tools: ["*"]` が入ること**・
**既存の default.json に `tools` が無ければ足すこと**・**利用者が書いた `tools` は
上書きしないこと**。installer は `scripts/build-runtime-plugins.sh` が全 MCP プラグインへ
同じ内容で書き出すため、全プラグインの installer を通す。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLERS = sorted(ROOT.glob("plugins/mcp/mcp-*/dev.kiro/install.sh"))


def install(installer: Path, project: Path, home: Path) -> dict:
    env = {**os.environ, "HOME": str(home)}
    subprocess.run(
        ["bash", str(installer), "--project", str(project)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return json.loads((project / ".kiro/agents/default.json").read_text(encoding="utf-8"))


def test_installers_exist() -> None:
    assert INSTALLERS


@pytest.mark.parametrize("installer", INSTALLERS, ids=lambda p: p.parents[1].name)
def test_new_agent_file_allows_all_tools(tmp_path: Path, installer: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    agent = install(installer, project, tmp_path)

    assert agent["tools"] == ["*"]


def test_existing_agent_file_without_tools_gets_all_tools(tmp_path: Path) -> None:
    project = tmp_path / "project"
    agent_file = project / ".kiro/agents/default.json"
    agent_file.parent.mkdir(parents=True)
    agent_file.write_text(json.dumps({"name": "default", "mine": 1}), encoding="utf-8")

    agent = install(INSTALLERS[0], project, tmp_path)

    assert agent["tools"] == ["*"]
    assert agent["mine"] == 1


def test_user_tools_are_kept(tmp_path: Path) -> None:
    project = tmp_path / "project"
    agent_file = project / ".kiro/agents/default.json"
    agent_file.parent.mkdir(parents=True)
    agent_file.write_text(json.dumps({"name": "default", "tools": ["fs_read"]}), encoding="utf-8")

    agent = install(INSTALLERS[0], project, tmp_path)

    assert agent["tools"] == ["fs_read"]


# 以下は現状固定テスト。hooks.json の取り込み（埋め込み Python）の振る舞いを、今の出力どおりに固定する。
PLACEHOLDER = "${PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}}"


def fake_plugin(tmp_path: Path, hooks: dict | None) -> Path:
    """installer を偽のプラグインへ写す。installer は自分の置き場所からプラグインを決める。"""
    plugin = tmp_path / "plugins" / "mcp-fake"
    (plugin / "dev.kiro").mkdir(parents=True)
    (plugin / ".mcp.json").write_text(json.dumps({"mcpServers": {"fake": {"command": "fake"}}}), encoding="utf-8")
    if hooks is not None:
        (plugin / "hooks").mkdir()
        (plugin / "hooks/hooks.json").write_text(json.dumps(hooks), encoding="utf-8")
    installer = plugin / "dev.kiro/install.sh"
    installer.write_text(INSTALLERS[0].read_text(encoding="utf-8"), encoding="utf-8")
    return installer


def new_project(tmp_path: Path, agent: dict | None = None) -> Path:
    project = tmp_path / "project"
    (project / ".kiro/agents").mkdir(parents=True)
    if agent is not None:
        (project / ".kiro/agents/default.json").write_text(json.dumps(agent), encoding="utf-8")
    return project.resolve()


def test_hooks_are_mapped_rewritten_and_filtered(tmp_path: Path) -> None:
    installer = fake_plugin(
        tmp_path,
        {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "startup",
                        "hooks": [
                            {"type": "command", "command": f"python3 {PLACEHOLDER}/a.py {PLACEHOLDER}/b", "timeout": 10},
                            {"type": "command", "command": "", "timeout_ms": 1},
                            {"type": "command"},
                        ],
                    },
                    {"matcher": "resume"},
                ],
                "Stop": [{"hooks": [{"command": "stop.sh", "timeout_ms": 500, "description": "x"}]}],
                "PreToolUse": [{"matcher": "Read", "hooks": [{"command": "pre.sh"}, {"command": "pre.sh"}]}],
            }
        },
    )
    project = new_project(tmp_path)
    link = f"{project}/.kiro/mcp_runtime/mcp-fake"

    agent = install(installer, project, tmp_path)

    assert agent["hooks"] == {
        "agentSpawn": [{"command": f"python3 {link}/a.py {link}/b"}],
        "stop": [{"command": "stop.sh", "timeout_ms": 500}],
        "PreToolUse": [{"command": "pre.sh"}],
    }


def test_existing_hook_entries_are_kept_and_not_duplicated(tmp_path: Path) -> None:
    installer = fake_plugin(
        tmp_path,
        {"hooks": {"Stop": [{"hooks": [{"command": "kept.sh", "timeout_ms": 9}, {"command": "new.sh"}]}]}},
    )
    project = new_project(
        tmp_path,
        {
            "name": "default",
            "hooks": {
                "stop": ["not-a-dict", {"command": "kept.sh"}, {"command": "mine.sh"}],
                "userPromptSubmit": [{"command": "other.sh"}],
            },
        },
    )

    agent = install(installer, project, tmp_path)

    assert agent["hooks"] == {
        "stop": ["not-a-dict", {"command": "kept.sh"}, {"command": "mine.sh"}, {"command": "new.sh"}],
        "userPromptSubmit": [{"command": "other.sh"}],
    }


def test_reinstall_does_not_duplicate_hooks(tmp_path: Path) -> None:
    installer = fake_plugin(tmp_path, {"hooks": {"SessionStart": [{"hooks": [{"command": f"{PLACEHOLDER}/s.sh"}]}]}})
    project = new_project(tmp_path)
    link = f"{project}/.kiro/mcp_runtime/mcp-fake"

    install(installer, project, tmp_path)
    agent = install(installer, project, tmp_path)

    assert agent["hooks"] == {"agentSpawn": [{"command": f"{link}/s.sh"}]}


def test_without_hooks_file_agent_has_no_hooks(tmp_path: Path) -> None:
    installer = fake_plugin(tmp_path, None)
    project = new_project(tmp_path)

    agent = install(installer, project, tmp_path)

    assert agent == {
        "name": "default",
        "resources": ["skill://.kiro/skills/**/SKILL.md"],
        "tools": ["*"],
        "mcpServers": {"fake": {"command": "fake"}},
    }


def test_hooks_file_without_hooks_key_adds_empty_hooks(tmp_path: Path) -> None:
    installer = fake_plugin(tmp_path, {"description": "no hooks key"})
    project = new_project(tmp_path)

    agent = install(installer, project, tmp_path)

    assert agent["hooks"] == {}


def test_real_plugin_hooks(tmp_path: Path) -> None:
    project = new_project(tmp_path)
    serena = f"{project}/.kiro/mcp_runtime/mcp-serena"
    playwright = f"{project}/.kiro/mcp_runtime/mcp-playwright"

    install(ROOT / "plugins/mcp/mcp-serena/dev.kiro/install.sh", project, tmp_path)
    agent = install(ROOT / "plugins/mcp/mcp-playwright/dev.kiro/install.sh", project, tmp_path)

    assert agent["hooks"] == {
        "agentSpawn": [
            {"command": f'python3 "{serena}/scripts/serena-lsp.py" hook session-start --client claude-code'},
            {"command": f"node {playwright}/scripts/ensure-playwright.js"},
        ],
        "PreToolUse": [
            {
                "command": 'sh -c \'[ -x "$0" ] || exit 0; exec "$0" "$@"\' '
                f'"$HOME/.cache/mcp-serena/roots{serena}/bin/python" "{serena}/scripts/serena-lsp.py" '
                "hook pre-tool-use --client claude-code"
            }
        ],
    }
