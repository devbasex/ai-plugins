"""manifests のテストが共有する置き場と、一時のプラグインへ表と Skill を書く補助。"""

from __future__ import annotations

import json
from pathlib import Path

from ndf import identity
from ndf.domains.manifests import table


ROOT = identity.ROOT


def _skill(root: Path, name: str, **fields) -> Path:
    path = root / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in {"name": name, **fields}.items())
    path.write_text(f"---\n{head}\n---\n\n本文\n", encoding="utf-8")
    return path


# 一時の表のランタイム（claude は skills/ をすべて読み、codex は定義と policy を書く）
RUNTIMES = {
    "claude": {"definition": None, "policy": None},
    "codex": {"definition": ".codex-plugin/plugin.json", "policy": "agents/openai.yaml"},
}


def _write_table(root: Path, skills: list, runtimes: dict = RUNTIMES) -> None:
    (root / table.TABLE_REL).write_text(json.dumps({"runtimes": runtimes, "skills": skills}), encoding="utf-8")
