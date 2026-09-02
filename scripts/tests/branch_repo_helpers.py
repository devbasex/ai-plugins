"""起点ブランチの検査が共有する補助。

conftest.py へ置くと、複数のテストを同時に実行したときに `conftest` というモジュール名が
衝突する。直接 import する補助はこの固有名のモジュールへ置く。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

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


def push_branch(repo: Path, name: str) -> None:
    """origin にだけ <name> のブランチを置く。"""
    git(repo, "branch", name)
    git(repo, "push", "-q", "origin", name)
    git(repo, "branch", "-D", name)


def push_lookalike_branch(repo: Path, name: str) -> None:
    """`refs/heads/<名前>` の問い合わせに応えてしまう別のブランチだけを origin に置く。

    `git ls-remote` のパターンは参照名の末尾に一致するため、完全な参照名で問い合わせても
    `refs/heads/x/refs/heads/develop` が `refs/heads/develop` の結果として返る
    （git 2.53.0 で実測）。問い合わせの成功だけを見ると、起点が未作成なのに「ある」と読む。
    """
    git(repo, "push", "-q", "origin", f"HEAD:refs/heads/x/refs/heads/{name}")


def drop_remote_tracking(repo: Path, name: str) -> None:
    """origin のブランチを取得していない状態を作る。

    `git push` は送った先の参照 (`refs/remotes/origin/<名前>`) も更新する。origin には
    あるがまだ取得していない状態は、その参照を消すことで再現する。
    """
    git(repo, "update-ref", "-d", f"refs/remotes/origin/{name}")
