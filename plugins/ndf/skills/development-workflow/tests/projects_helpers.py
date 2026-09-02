"""進行の記録のテストが共有する補助。

conftest.py へ置くと、複数の Skill のテストを同時に実行したときに `conftest` という
モジュール名が衝突する。直接 import する補助はこの固有名のモジュールへ置く。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
LIB = SCRIPTS_DIR / "lib" / "projects-common.sh"
SYNC = SCRIPTS_DIR / "projects-sync.sh"


def run_lib(snippet: str, cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    """共通ライブラリを読み込んだ上で `snippet` を bash で実行する。"""
    script = f'set -uo pipefail\n. "{LIB}"\n{snippet}\n'
    run_env = os.environ.copy()
    run_env.setdefault("LC_ALL", "C")
    if env:
        run_env.update(env)
    return subprocess.run(
        ["bash", "-c", script], cwd=str(cwd) if cwd else None,
        env=run_env, capture_output=True, text=True,
    )


def run_sync(*args: str, cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    """入口のスクリプトを実行する。外部への通信は PATH の差し替えで止める。"""
    run_env = os.environ.copy()
    run_env.setdefault("LC_ALL", "C")
    if env:
        run_env.update(env)
    return subprocess.run(
        ["bash", str(SYNC), *args], cwd=str(cwd),
        env=run_env, capture_output=True, text=True,
    )


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "t")
    (path / "README.md").write_text("x\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "init")
    return path


def write_declaration(repo: Path, body: str) -> Path:
    d = repo / ".ndf"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "projects.json"
    f.write_text(body, encoding="utf-8")
    return f
