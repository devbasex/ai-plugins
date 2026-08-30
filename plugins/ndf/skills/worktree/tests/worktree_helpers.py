"""worktree のテストが共有する補助。

conftest.py へ置くと、複数の Skill のテストを同時に実行したときに `conftest` という
モジュール名が衝突し、別の Skill の conftest が解決されてしまう。直接 import する
補助はこの固有名のモジュールへ置く。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REQUIRED_COMMANDS = ("bash", "jq", "git")


def missing_command() -> str | None:
    """テストの実行に要るコマンドのうち、無いものを 1 つ返す。"""
    for name in REQUIRED_COMMANDS:
        if shutil.which(name) is None:
            return name
    return None


SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
LIB = SCRIPTS_DIR / "lib" / "worktree-common.sh"
GUARD = SCRIPTS_DIR / "worktree-guard.sh"
SESSION = SCRIPTS_DIR / "worktree-session.sh"


def run_lib(snippet: str, cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    """共通ライブラリを読み込んだ上で `snippet` を bash で実行する。"""
    script = f'set -uo pipefail\n. "{LIB}"\n{snippet}\n'
    run_env = os.environ.copy()
    run_env.setdefault("LC_ALL", "C")
    if env:
        run_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(cwd) if cwd else None,
        env=run_env,
        capture_output=True,
        text=True,
    )


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )


def init_repo(path: Path) -> Path:
    """コミットを 1 つ持つリポジトリを作る。"""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "test")
    git(path, "config", "commit.gpgsign", "false")
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-q", "-m", "init")
    return path


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )


def init_repo(path: Path) -> Path:
    """コミットを 1 つ持つリポジトリを作る。"""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "test")
    git(path, "config", "commit.gpgsign", "false")
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-q", "-m", "init")
    return path


def write_declaration(main_repo: Path, body: str) -> Path:
    """`.ndf/localenv.json` を書く。"""
    ndf = main_repo / ".ndf"
    ndf.mkdir(parents=True, exist_ok=True)
    path = ndf / "localenv.json"
    path.write_text(body, encoding="utf-8")
    return path
