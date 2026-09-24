"""Kiro の installer（AC29）。agentSpawn へ写った SessionStart は Kiro では何も出さない（決定 15）。"""
import json
import os
import subprocess

from serena_lsp_testlib import PLUGIN, files_of, make_repo


def test_kiro_agent_spawn_prints_nothing_without_claude_plugin_root(tmp_path):
    project = make_repo(tmp_path / "p", files_of(py=30))
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT", "CODEX_PLUGIN_ROOT")}
    subprocess.run(["bash", str(PLUGIN / "dev.kiro/install.sh"), "--project", str(project)],
                   check=True, capture_output=True, env=env)
    agent = json.loads((project / ".kiro/agents/default.json").read_text())
    commands = [h["command"] for h in agent["hooks"]["agentSpawn"] if "serena-lsp.py" in h["command"]]
    assert commands
    for command in commands:
        proc = subprocess.run(["sh", "-c", command], input=json.dumps({"cwd": str(project)}),
                              capture_output=True, text=True, env=env, cwd=project)
        assert (proc.returncode, proc.stdout) == (0, "")
