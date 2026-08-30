"""worktree Skill のテスト共通フィクスチャ。

判定は `plugins/ndf/scripts/lib/worktree-common.sh` の関数に集約されている
（詳細設計 06 の決定 8）。テストはこの層に対して書く。

既存の `statusline` / `cross-review` のテストと同じく、隔離した作業領域で
bash を子プロセスとして実行し、標準出力と終了コードを観測する形を採る。
`bash` と `jq` が無い環境ではモジュールごと読み飛ばす。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

for _cmd in ("bash", "jq", "git"):
    if shutil.which(_cmd) is None:
        pytest.skip(f"{_cmd} not available", allow_module_level=True)

# plugins/ndf/skills/worktree/tests/ -> plugins/ndf/scripts/
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


@pytest.fixture()
def main_repo(tmp_path: Path) -> Path:
    """主ディレクトリにあたるリポジトリ。"""
    return init_repo(tmp_path / "main")


@pytest.fixture()
def worktree(main_repo: Path) -> Path:
    """`.worktrees/feature/x` に置いた開発用の作業ツリー。"""
    target = main_repo / ".worktrees" / "feature" / "x"
    git(main_repo, "worktree", "add", "-q", "-b", "feature/x", str(target))
    return target


def write_declaration(main_repo: Path, body: str) -> Path:
    """`.ndf/localenv.json` を書く。"""
    ndf = main_repo / ".ndf"
    ndf.mkdir(parents=True, exist_ok=True)
    path = ndf / "localenv.json"
    path.write_text(body, encoding="utf-8")
    return path
