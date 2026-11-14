"""テストに共通する一時の git リポジトリの土台。

`init_with_origin` は bare の origin と、それを origin に登録した作業リポジトリを作る（コミットはしない）。
最初のファイル・コミット・push・作業ブランチは、呼ぶテストが自分で用意する。
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)


def init_with_origin(tmp_path: Path, branch: str = "develop") -> Path:
    """bare の `tmp_path/origin.git` と、`branch` で始めてコミット用の設定と origin の登録を済ませた `tmp_path/repo` を作り、後者を返す。"""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", branch, str(origin)], check=True)
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", branch)
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "remote", "add", "origin", str(origin))
    return root


def git(cwd: Path, *args: str) -> str:
    """`git -C <cwd> <args>` を作者 t で打ち、標準出力の前後の空白を除いて返す。失敗すれば AssertionError。"""
    p = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args], capture_output=True, text=True)
    assert p.returncode == 0, (args, p.stderr)
    return p.stdout.strip()


def main_and_develop(repo: Path, write_main, write_develop=None, main_msg: str = "main") -> None:
    """`.ndf/` を持つ `repo` を `main` で始め、`write_main(repo)` の内容を `main` にコミットし、`develop` を切って
    `write_develop(repo)`（省けば何も書かない）の内容で 1 コミット進める。`develop` を checkout したまま返す。origin は作らない。"""
    (repo / ".ndf").mkdir(parents=True)
    git(repo.parent, "init", "-q", "-b", "main", str(repo))
    write_main(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", main_msg)
    git(repo, "checkout", "-q", "-b", "develop")
    if write_develop is not None:
        write_develop(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", "develop")  # 両方の内容が同じでもブランチを分ける
