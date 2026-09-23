"""3 層の worker のエージェント定義が Skill と Agent のツールを外すこと（#828 の AC14）。

worker が Skill を起動しない規則は、文面だけでなく定義で守らせる。Agent も外すのは、
Skill だけを外した子が Agent で起こした孫は Skill を使えたためである（設計の表の H）。
"""
from __future__ import annotations

import json
import pathlib
import re

PLUGIN = pathlib.Path(__file__).resolve().parents[2]
WORKER = PLUGIN / "agents" / "worker.md"


def frontmatter(path: pathlib.Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "frontmatter が無い"
    return dict(
        (k.strip(), v.strip())
        for k, v in (line.split(":", 1) for line in m.group(1).splitlines() if ":" in line and not line.startswith(" "))
    )


def test_the_worker_definition_disallows_skill_and_agent():
    fm = frontmatter(WORKER)
    assert fm.get("name") == "worker"
    tools = {t.strip() for t in fm.get("disallowedTools", "").split(",")}
    assert {"Skill", "Agent"} <= tools
    # 許可の一覧で書くと Grep / Glob が子に現れなかった（設計の表の B）
    assert "tools" not in fm


def test_the_worker_definition_is_distributed():
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert "./agents/worker.md" in manifest["agents"]
