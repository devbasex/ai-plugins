"""状態ファイルの探索と読み込みのつなぎ目を、実ファイルで固定するテスト。

再開時にどの状態を読むかは `paths.load_state` → `paths._find_state` が決める。
`CROSS_REFACTORING_TMP_DIR`（指定先）と現在地の両方に同じ ID の状態があるときの
優先順位、指定先に対象 ID が無いときの現在地へのフォールバック、環境変数が
未設定のときの経路を、公開入口 `paths.load_state(id)` を実際に呼んで固定する。
"""
from __future__ import annotations

import json
import pathlib
import tempfile

import pytest


STATE_ID = 130


def _make_state(dir_path: pathlib.Path, phase: str) -> pathlib.Path:
    """指定ディレクトリへ、既存の make_state と同じ形の最小状態を置く。

    どちらの経路が選ばれたかを返却内容から読めるよう `phase` だけ変える。
    """
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / f"cross-refactoring-rf{STATE_ID}-state.json"
    path.write_text(
        json.dumps({"id": STATE_ID, "phase": phase}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_the_env_dir_wins_when_both_have_the_target(paths, tmp_path, monkeypatch):
    """現状固定: 指定先と現在地の両方に対象 ID があると指定先が選ばれる。"""
    env_dir = tmp_path / "env"
    env_path = _make_state(env_dir, "from-env")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    _make_state(cwd / ".cross_refactoring", "from-cwd")

    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(env_dir))
    monkeypatch.chdir(cwd)

    path, state = paths.load_state(STATE_ID)

    assert path == env_path
    assert state["phase"] == "from-env"


def test_it_falls_back_to_the_cwd_when_the_env_dir_lacks_the_target(
    paths, tmp_path, monkeypatch
):
    """現状固定: 指定先に対象 ID が無ければ現在地の .cross_refactoring を読む。"""
    env_dir = tmp_path / "env"
    env_dir.mkdir(parents=True, exist_ok=True)
    # 指定先には別 ID の状態だけを置く（対象 ID は無い）
    (env_dir / "cross-refactoring-rf999-state.json").write_text(
        json.dumps({"id": 999, "phase": "different"}), encoding="utf-8"
    )

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    cwd_path = _make_state(cwd / ".cross_refactoring", "from-cwd")

    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(env_dir))
    monkeypatch.chdir(cwd)

    path, state = paths.load_state(STATE_ID)

    assert path == cwd_path
    assert state["phase"] == "from-cwd"


def test_it_uses_the_cwd_when_the_env_var_is_unset(paths, tmp_path, monkeypatch):
    """現状固定: 環境変数が未設定なら現在地の .cross_refactoring を読む。"""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    cwd_path = _make_state(cwd / ".cross_refactoring", "from-cwd")

    monkeypatch.delenv("CROSS_REFACTORING_TMP_DIR", raising=False)
    monkeypatch.chdir(cwd)

    path, state = paths.load_state(STATE_ID)

    assert path == cwd_path
    assert state["phase"] == "from-cwd"


def test_load_state_exits_4_when_the_target_is_missing(
    paths, tmp_path, monkeypatch, capsys
):
    """現状固定: 指定先と現在地のどちらにも対象 ID が無ければ停止する。"""
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(env_dir))
    monkeypatch.chdir(cwd)

    with pytest.raises(SystemExit) as exc:
        paths.load_state(STATE_ID)

    assert exc.value.code == 4
    assert str(STATE_ID) in capsys.readouterr().err


def test_the_explicit_worktree_base_is_resolved(paths, tmp_path, monkeypatch):
    """現状固定: 明示した作業ディレクトリの親を絶対パスへ解決する。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NDF_WORKTREE_BASE", "relative-worktrees")

    assert paths.default_worktree_base() == (tmp_path / "relative-worktrees").resolve()


def test_the_worktree_base_falls_back_to_the_system_tmpdir(paths, monkeypatch):
    """現状固定: 明示指定が無ければシステム tmpdir 配下を使う。"""
    monkeypatch.delenv("NDF_WORKTREE_BASE", raising=False)

    assert paths.default_worktree_base() == pathlib.Path(tempfile.gettempdir()) / "ndf-worktrees"
