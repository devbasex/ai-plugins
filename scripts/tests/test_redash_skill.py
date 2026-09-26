"""mcp-redash の操作 Skill は `/redash <add|list|remove|status> [suffix]` の 1 本である（#876）。

SKILL.md の bash ブロックを、Claude Code と同じく `${CLAUDE_PLUGIN_ROOT}` と `$ARGUMENTS` を
置き換えてから実行し、4 つの操作がプロジェクトの `.mcp.json` に効くことを確かめる。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN = REPO_ROOT / "plugins/mcp/mcp-redash"
SKILL_MD = PLUGIN / "skills/redash/SKILL.md"
BASH_BLOCK = re.compile(r"^```bash\n(.*?)^```", re.MULTILINE | re.DOTALL)

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が無い")


def _skill_dirs() -> set[str]:
    return {d.name for d in (PLUGIN / "skills").iterdir() if (d / "SKILL.md").is_file()}


def test_the_operations_are_one_skill_beside_the_guide():
    assert _skill_dirs() == {"redash", "redash-guide"}


def test_the_claude_manifest_lists_the_skill_directories():
    manifest = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    listed = {Path(p).name for p in manifest["skills"]}
    assert listed == _skill_dirs()
    for p in manifest["skills"]:
        assert (PLUGIN / p / "SKILL.md").is_file()


def test_the_skill_has_one_bash_block():
    assert len(BASH_BLOCK.findall(SKILL_MD.read_text(encoding="utf-8"))) == 1


def _run(project: Path, arguments: str) -> subprocess.CompletedProcess[str]:
    block = BASH_BLOCK.findall(SKILL_MD.read_text(encoding="utf-8"))[0]
    script = block.replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN)).replace("$ARGUMENTS", arguments)
    env = {k: v for k, v in os.environ.items()
           if k not in {"WORKSPACE_ROOT", "GIT_WORK_TREE", "CLAUDE_PROJECT_DIR",
                        "CODEX_WORKSPACE_ROOT", "KIRO_WORKSPACE_ROOT"}}
    env["PROJECT_ROOT"] = str(project)
    env["HOME"] = str(project / "home")
    return subprocess.run(["bash", "-euc", script], cwd=project, env=env,
                          capture_output=True, text=True, check=False)


def _servers(project: Path) -> dict:
    return json.loads((project / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]


def test_add_list_status_remove_act_on_the_project(tmp_path):
    (tmp_path / "home").mkdir()
    added = _run(tmp_path, "add dev")
    assert added.returncode == 0, added.stderr
    assert "redash-dev" in _servers(tmp_path)

    listed = _run(tmp_path, "list")
    assert listed.returncode == 0, listed.stderr
    assert "redash-dev" in listed.stdout

    status = _run(tmp_path, "status")
    assert status.returncode == 0, status.stderr
    assert "REDASH_DEV_URL" in status.stdout

    removed = _run(tmp_path, "remove dev")
    assert removed.returncode == 0, removed.stderr
    assert "redash-dev" not in _servers(tmp_path)


@pytest.mark.parametrize("arguments", ["", "drop dev"])
def test_an_unknown_operation_fails(tmp_path, arguments):
    (tmp_path / "home").mkdir()
    r = _run(tmp_path, arguments)
    assert r.returncode != 0
    assert not (tmp_path / ".mcp.json").exists()
