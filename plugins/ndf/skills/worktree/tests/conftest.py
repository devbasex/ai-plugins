"""worktree Skill のテストのフィクスチャ。

判定は `plugins/ndf/scripts/lib/worktree-common.sh` の関数に集約されている
（詳細設計 06 の決定 8）。テストはこの層に対して書く。

隔離した作業領域で bash を子プロセスとして実行し、標準出力と終了コードを観測する。
直接 import する補助は `worktree_helpers.py` にある。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from worktree_helpers import git, init_repo, missing_command

# bash / jq / git が無い環境ではテストを収集しない。補助モジュールの読み込み時に
# skip を投げるとテストモジュールの外での skip になるため、収集の側で止める。
_missing = missing_command()
collect_ignore_glob = ["test_*.py"] if _missing else []


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
