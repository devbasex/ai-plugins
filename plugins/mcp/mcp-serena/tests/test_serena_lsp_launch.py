"""起動定義（AC1・AC2）と lspServers を宣言しないこと（AC16）。"""
import json
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[2]

COMMON_TAIL = [
    "--project-from-cwd",
    "--add-mode", "no-memories",
    "--add-mode", "no-onboarding",
    "--enable-web-dashboard", "False",
]


def _server(name):
    return json.loads((PLUGIN / name).read_text())["mcpServers"]["serena"]


def _expected(context):
    return ["--from", "serena-agent==1.7.0", "serena", "start-mcp-server",
            "--context", context, *COMMON_TAIL]


def test_claude_code_launch_is_pinned_with_claude_code_context():
    server = _server(".mcp.json")
    assert server["command"] == "uvx"
    assert server["args"] == _expected("claude-code")
    assert server["env"] == {"SERENA_HOME": ".serena"}
    assert not any("git+" in a for a in server["args"])


def test_codex_launch_differs_only_in_context():
    server = _server(".codex.mcp.json")
    assert server["command"] == "uvx"
    assert server["args"] == _expected("codex")
    assert server["env"] == {"SERENA_HOME": ".serena"}


def test_codex_manifest_points_to_codex_definitions():
    manifest = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text())
    assert manifest["mcpServers"] == "./.codex.mcp.json"
    assert manifest["hooks"] == "./hooks/codex.json"
    assert "./skills/language-servers" in manifest["skills"]


def test_no_manifest_declares_lsp_servers():
    manifests = list((REPO / "plugins").glob("**/.claude-plugin/plugin.json"))
    manifests += list((REPO / "plugins").glob("**/.codex-plugin/plugin.json"))
    manifests.append(REPO / ".claude-plugin/marketplace.json")
    assert len(manifests) > 3
    for path in manifests:
        assert "lspServers" not in path.read_text(), path
