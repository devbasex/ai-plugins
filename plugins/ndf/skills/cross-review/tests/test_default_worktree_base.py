"""state.py `_default_worktree_base()` の解決順テスト。

優先順位:
  1. 環境変数 NDF_WORKTREE_BASE
  2. /work/worktrees (書込可能)
  3. $HOME/work/worktrees (フォールバック)
"""
from __future__ import annotations

import pathlib


def test_env_override_takes_precedence(monkeypatch, tmp_path, state_mod):
    explicit = tmp_path / "custom-base"
    monkeypatch.setenv("NDF_WORKTREE_BASE", str(explicit))
    assert state_mod._default_worktree_base() == explicit


def test_fallback_to_home_when_legacy_unwritable(monkeypatch, tmp_path, state_mod):
    """`/work/worktrees` への mkdir が失敗する環境では $HOME/work/worktrees を返す。

    `Path.mkdir` を patch して `/work/worktrees` を書込不可な状態をエミュレートする。
    """
    monkeypatch.delenv("NDF_WORKTREE_BASE", raising=False)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # Path.home() は HOME env を再評価しないため明示的に差し替える
    monkeypatch.setattr(
        state_mod.pathlib.Path, "home",
        classmethod(lambda cls: cls(str(fake_home))),
    )

    orig_mkdir = state_mod.pathlib.Path.mkdir

    def fake_mkdir(self, *args, **kwargs):
        if str(self) == "/work/worktrees":
            raise OSError("read-only file system")
        return orig_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(state_mod.pathlib.Path, "mkdir", fake_mkdir)

    result = state_mod._default_worktree_base()
    assert result == pathlib.Path(str(fake_home)) / "work" / "worktrees"
