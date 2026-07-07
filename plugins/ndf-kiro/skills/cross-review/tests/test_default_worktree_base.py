"""state.py `_default_worktree_base()` / `_repo_slug()` の解決テスト。

優先順位:
  1. 環境変数 NDF_WORKTREE_BASE
  2. <システム tmpdir>/ndf-worktrees (非永続領域)

worktree path は `<base>/<owner>--<name>/pr<N>` 形式で、他リポジトリの
同一 PR 番号と衝突しない。
"""
from __future__ import annotations

import pathlib


def test_env_override_takes_precedence(monkeypatch, tmp_path, state_mod):
    explicit = tmp_path / "custom-base"
    monkeypatch.setenv("NDF_WORKTREE_BASE", str(explicit))
    assert state_mod._default_worktree_base() == explicit


def test_default_is_tmpdir_ndf_worktrees(monkeypatch, tmp_path, state_mod):
    """既定では永続 volume ではなくシステム tmpdir 配下を使う。"""
    monkeypatch.delenv("NDF_WORKTREE_BASE", raising=False)
    monkeypatch.setattr(state_mod.tempfile, "gettempdir", lambda: str(tmp_path))
    assert state_mod._default_worktree_base() == tmp_path / "ndf-worktrees"


def test_repo_slug_is_path_safe(state_mod):
    assert state_mod._repo_slug("devbasex/ai-plugins") == "devbasex--ai-plugins"


def test_is_registered_worktree_rejects_foreign_dir(monkeypatch, tmp_path, state_mod):
    """`git worktree list` に載っていないパスは流用しない (別リポジトリの残骸対策)。"""
    registered = tmp_path / "registered"
    foreign = tmp_path / "foreign"
    monkeypatch.setattr(
        state_mod, "_sh",
        lambda cmd, check=True: f"worktree {registered}\nHEAD abc\n",
    )
    assert state_mod._is_registered_worktree(str(registered)) is True
    assert state_mod._is_registered_worktree(str(foreign)) is False
