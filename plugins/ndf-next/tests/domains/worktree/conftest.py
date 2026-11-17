"""worktree のテストに共通する補助: origin（bare）と、それを clone したメインディレクトリを作る。

ブランチ戦略の宣言（`.ndf/project.json` の `branch_strategy`）は、代表のリポジトリ 5 種の fixture（`strategy_fixture`）から
選んで置く。既定は `web-develop`（ベースブランチ develop・本番ブランチ main）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

import pytest
import strategy_fixture

# コミットの書き手（テストの機械の git の設定に依らない）
IDENT = ["-c", "user.name=ndf-test", "-c", "user.email=ndf-test@example.invalid", "-c", "init.defaultBranch=develop"]
DECLARATION = {"version": 1}
# 既定のブランチ戦略の宣言の種類
KIND = "web-develop"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *IDENT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def run_git() -> Callable[..., str]:
    return git


def _make(tmp_path: Path, decl: dict | None, kind: str | None = KIND) -> Path:
    origin, main = tmp_path / "origin.git", tmp_path / "main"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    git(tmp_path, "init", "-q", str(main))
    (main / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    if kind is not None:
        strategy_fixture.write(main, kind)
    if decl is not None:
        (main / ".ndf").mkdir(exist_ok=True)
        (main / ".ndf" / "worktree.json").write_text(json.dumps(decl), encoding="utf-8")
    git(main, "add", "-A")
    git(main, "commit", "-q", "-m", "init")
    git(main, "branch", "main")
    git(main, "remote", "add", "origin", str(origin))
    git(main, "push", "-q", "origin", "develop", "main")
    git(main, "remote", "set-head", "origin", "develop")
    return main


@pytest.fixture
def main_repo(tmp_path: Path) -> Path:
    """宣言（ブランチ戦略は web-develop。ベースブランチは develop）を持つメインディレクトリ。origin に develop と main がある。"""
    return _make(tmp_path, DECLARATION)


@pytest.fixture
def make_repo(tmp_path: Path) -> Callable[..., Path]:
    """宣言を選んでメインディレクトリを作る関数（`decl` の `None` は worktree の宣言なし、`kind` の `None` はブランチ戦略の宣言なし）。"""
    return lambda decl, kind=KIND: _make(tmp_path, decl, kind)
