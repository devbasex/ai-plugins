"""develop を起点にした一時の git リポジトリの土台（test_pr_steps.py・test_supervise.py が共通に使う）。

origin は bare（ベースブランチは develop）。土台は git_fixture.init_with_origin が作り、ここでは渡したファイルで最初の
コミットを作って origin の develop へ push する。feature ブランチ・追加のコミット・偽の gh は呼び出し側が用意する。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from git_fixture import init_with_origin


def seed_develop_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """`tmp_path/origin.git`（bare）と、`files` を最初のコミットにして push した `tmp_path/repo` を作り、repo を返す。"""
    root = init_with_origin(tmp_path)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for args in (
        ("add", "-A"),
        ("commit", "-q", "-m", "init"),
        ("push", "-q", "-u", "origin", "develop"),
    ):
        subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return root
