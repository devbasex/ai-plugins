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
