"""プランの実行のテストが共有する手: 一時のリポジトリ・プランを書く・子プロセスで `ndf plan` を打つ・数える。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ndf import identity
from ndf.core import durable

SRC = Path(__file__).resolve().parents[3] / "src"
SECRET = "plan-s3cr3t-value-42"


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, check=True, capture_output=True)


def make_repo(path: Path) -> Path:
    path.mkdir()
    git(path, "init", "-q")
    git(path, "commit", "-q", "--allow-empty", "-m", "init")
    return path


def ndf(cwd: Path, *args: str, timeout: float = 180) -> tuple[int, dict]:
    """`ndf plan <args>` を子プロセスで打ち、終了コードと最後の行の結果 JSON を返す。"""
    env = {**os.environ, "PYTHONPATH": str(SRC), "NDF_TEST_TOKEN": SECRET}
    env.pop("NDF_PROGRESS_DB", None)
    done = subprocess.run(
        [sys.executable, "-m", "ndf", "plan", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False
    )
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    assert lines, done.stderr
    return done.returncode, json.loads(lines[-1])


def count_step(sid: str, counter: Path, **kw) -> dict:
    """打つたびに `counter` の数を 1 つ増やすステップ。"""
    code = f"import pathlib; p = pathlib.Path({str(counter)!r}); p.write_text(str(int(p.read_text()) + 1) if p.exists() else '1')"
    return {"id": sid, "run": [sys.executable, "-c", code], **kw}


def py_step(sid: str, code: str, **kw) -> dict:
    """Python の 1 行を打つステップ。"""
    return {"id": sid, "run": [sys.executable, "-c", code], **kw}


def write_plan(repo: Path, name: str, steps: list[dict], **extra) -> str:
    path = repo / "plans" / f"{name}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"name": name, "repo": str(repo), "steps": steps, **extra}), encoding="utf-8")
    return f"plans/{name}.json"


def dbos_dir(repo: Path) -> Path:
    return repo / ".git" / identity.load().name / "state" / durable.DBOS_DIR


def count(path: Path) -> int:
    return int(path.read_text()) if path.exists() else 0
