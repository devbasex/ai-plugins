"""プランの実行のテストが共有する手: 一時のリポジトリ・プランを書く・ステップを作る。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import git_sandbox


def make_repo(path: Path) -> Path:
    path.mkdir()
    git_sandbox.sh(path, "init", "-q", identity=True)
    git_sandbox.sh(path, "commit", "-q", "--allow-empty", "-m", "init", identity=True)
    return path


def py_step(sid: str, code: str, **kw) -> dict:
    """Python の 1 行を打つステップ。"""
    return {"id": sid, "run": [sys.executable, "-c", code], **kw}


def write_plan(repo: Path, name: str, steps: list[dict], **extra) -> str:
    path = repo / "plans" / f"{name}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"name": name, "repo": str(repo), "steps": steps, **extra}), encoding="utf-8")
    return f"plans/{name}.json"
