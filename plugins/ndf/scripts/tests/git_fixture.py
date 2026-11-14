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
