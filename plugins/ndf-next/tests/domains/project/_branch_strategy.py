"""`ndf project branch-strategy` のテスト（`test_branch_strategy_*.py`）が共有する補助。

一時の git リポジトリ（`make_repo`・`history_repo`）、GitHub の API の偽物（`fake_api`）、サブコマンドの打ち方（`run_cli`）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ndf import contracts
from ndf.domains.project import cli

RESULT_SCHEMA = "schemas/io/branch-strategy.result.schema.json"
C = ["-c", "user.email=t@example.com", "-c", "user.name=t"]


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def make_repo(tmp_path: Path, ndf: dict[str, dict]) -> Path:
    root = tmp_path / "repo"
    (root / ".ndf").mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    for name, doc in ndf.items():
        (root / ".ndf" / name).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    git(root, *C, "add", "-A")
    git(root, *C, "commit", "-qm", "init")
    return root


def fake_api(prs, tags=(), releases=(), rules=None, rulesets=None, default="main", protection=None):
    data = {
        "repos/{owner}/{repo}": {"default_branch": default},
        "pulls": [{"number": n, "merged_at": "x", "base": {"ref": b}, "head": {"ref": h}} for n, b, h in prs],
        "tags": [{"name": t} for t in tags],
        "releases": [{"tag_name": t} for t in releases],
    }

    def api(path):
        if path == "repos/{owner}/{repo}":
            return data[path], ""
        for key in ("pulls", "tags", "releases"):
            if f"/{key}?" in path:
                return data[key], ""
        if "/rules/branches/" in path:
            return (rules, "") if rules is not None else (None, "missing")
        if path.endswith("/protection"):
            return (protection, "") if protection is not None else (None, "missing")
        if path.endswith(f"/branches/{default}"):
            return {"protection": {"enabled": protection is not None}}, ""
        if path.endswith("/rulesets"):
            return rulesets or [], ""
        return None, "unknown"

    return api


def history_repo(tmp_path: Path, merges: int, extra: dict | None = None) -> Path:
    root = make_repo(tmp_path, {"project.json": {"version": 1}})
    for name, text in (extra or {}).items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    git(root, *C, "add", "-A")
    git(root, *C, "commit", "-q", "--allow-empty", "-m", "files")
    for i in range(merges):
        git(root, "checkout", "-qb", f"sprint/{i}")
        git(root, *C, "commit", "-q", "--allow-empty", "-m", f"work {i}")
        git(root, "checkout", "-q", "main")
        git(root, *C, "merge", "-q", "--no-ff", f"sprint/{i}", "-m", f"Merge pull request #{i + 1}")
    return root


def run_cli(capsys, root: Path, *args: str) -> tuple[int, dict]:
    code = cli.main(["branch-strategy", *args, "--root", str(root)])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert contracts.schema_errors(out, RESULT_SCHEMA) == []
    return code, out
