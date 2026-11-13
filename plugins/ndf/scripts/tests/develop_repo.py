"""develop を起点にした一時の git リポジトリの土台（test_pr_steps.py・test_supervise.py が共通に使う）。

origin は bare（ベースブランチは develop）。作業ツリーは git の利用者と署名の無効を設定し、渡したファイルで最初の
コミットを作って origin の develop へ push する。feature ブランチ・追加のコミット・偽の gh は呼び出し側が用意する。
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def seed_develop_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """`tmp_path/origin.git`（bare）と、`files` を最初のコミットにして push した `tmp_path/repo` を作り、repo を返す。"""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "develop", str(origin)], check=True)
    root = tmp_path / "repo"
    root.mkdir()
    for args in (
        ("init", "-q", "-b", "develop"),
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "t"),
        ("config", "commit.gpgsign", "false"),
    ):
        subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for args in (
        ("add", "-A"),
        ("commit", "-q", "-m", "init"),
        ("remote", "add", "origin", str(origin)),
        ("push", "-q", "-u", "origin", "develop"),
    ):
        subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return root
