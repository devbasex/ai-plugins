"""worktree のテストが共有する補助。

conftest.py へ置くと、複数の Skill のテストを同時に実行したときに `conftest` という
モジュール名が衝突し、別の Skill の conftest が解決されてしまう。直接 import する
補助はこの固有名のモジュールへ置く。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

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


def write_declaration(main_repo: Path, body: str) -> Path:
    """`.ndf/worktree.json` を書く。"""
    ndf = main_repo / ".ndf"
    ndf.mkdir(parents=True, exist_ok=True)
    path = ndf / "worktree.json"
    path.write_text(body, encoding="utf-8")
    return path


def add_origin(main_repo: Path, name: str = "main") -> Path:
    """主ディレクトリへ origin を足し、既定ブランチを送る。"""
    remote = main_repo.parent / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    git(main_repo, "remote", "add", "origin", str(remote))
    git(main_repo, "push", "-q", "origin", name)
    git(main_repo, "remote", "set-head", "origin", name)
    return remote


def push_branch(main_repo: Path, name: str, *, keep_local: bool = False) -> None:
    """origin にブランチを送る。`keep_local` が偽ならローカル側は残さない。"""
    git(main_repo, "branch", name)
    git(main_repo, "push", "-q", "origin", name)
    if not keep_local:
        git(main_repo, "branch", "-D", name)


def drop_remote_tracking(main_repo: Path, name: str) -> None:
    """origin のブランチをまだ取得していない状態を作る。

    `git push` は送った先の追跡参照 (`refs/remotes/origin/<名前>`) も更新する。origin には
    あるが取得していない状態は、その参照を消すことで再現する。
    """
    git(main_repo, "update-ref", "-d", f"refs/remotes/origin/{name}")
