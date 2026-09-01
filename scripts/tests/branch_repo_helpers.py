"""起点ブランチの検査が共有する補助。

conftest.py へ置くと、複数のテストを同時に実行したときに `conftest` というモジュール名が
衝突する。直接 import する補助はこの固有名のモジュールへ置く。
"""
from __future__ import annotations

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


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    )


def init_origin_repo(root: Path) -> Path:
    """origin を持つリポジトリを作る。既定ブランチは main で、他のブランチは無い。"""
    main = root / "main"
    main.mkdir(parents=True, exist_ok=True)
    git(main, "init", "-q", "-b", "main")
    git(main, "config", "user.email", "test@example.com")
    git(main, "config", "user.name", "test")
    git(main, "config", "commit.gpgsign", "false")
    (main / "README.md").write_text("# test\n", encoding="utf-8")
    git(main, "add", "README.md")
    git(main, "commit", "-q", "-m", "init")

    remote = root / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    git(main, "remote", "add", "origin", str(remote))
    git(main, "push", "-q", "origin", "main")
    git(main, "remote", "set-head", "origin", "main")
    return main


def init_master_only_repo(root: Path) -> Path:
    """origin の HEAD を持たず、ローカルに `master` だけがあるリポジトリを作る。

    `git init` の既定が `master` のままの古いリポジトリを clone すると、この形になる
    （`remote set-head` を実行していなければ `refs/remotes/origin/HEAD` は無い）。
    起点の解決は、この経路で慣例の名前へ落ちる。
    """
    main = root / "master-only"
    main.mkdir(parents=True, exist_ok=True)
    git(main, "init", "-q", "-b", "master")
    git(main, "config", "user.email", "test@example.com")
    git(main, "config", "user.name", "test")
    git(main, "config", "commit.gpgsign", "false")
    (main / "README.md").write_text("# test\n", encoding="utf-8")
    git(main, "add", "README.md")
    git(main, "commit", "-q", "-m", "init")

    remote = root / "master-only-origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    git(main, "remote", "add", "origin", str(remote))
    git(main, "push", "-q", "origin", "master")
    return main


def push_develop(repo: Path) -> None:
    """origin にだけ `develop` を置く。"""
    git(repo, "branch", "develop")
    git(repo, "push", "-q", "origin", "develop")
    git(repo, "branch", "-D", "develop")
