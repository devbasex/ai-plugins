"""worktree Skill のテストのフィクスチャ。

判定は `plugins/ndf/scripts/lib/worktree-common.sh` の関数に集約されている
（詳細設計 06 の決定 8）。テストはこの層に対して書く。

隔離した作業領域で bash を子プロセスとして実行し、標準出力と終了コードを観測する。
直接 import する補助は `worktree_helpers.py` にある。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from worktree_helpers import git, init_repo



@pytest.fixture(autouse=True)
def _own_tmpdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ガードの状態ファイル（`$TMPDIR/ndf-worktree-<セッション>.json`）をテストごとに分ける。

    固定のセッション ID のまま共有の /tmp に置くと、同時に走る別のテストの実行が「案内済み」を書き、
    案内が出なくなる（全体テストを 6 本同時に走らせたときに 9 件落ちた）。
    """
    d = tmp_path / "tmpdir"
    d.mkdir()
    monkeypatch.setenv("TMPDIR", str(d))


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
