"""cross-refactoring のテストの実行は、コンテナで走る suite が worktree を見ているときだけ走る（#1337）。"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture()
def container_reach(gitfacts):
    """共通層の到達の確認。取り込みの道は refactor_lib が sys.path へ足す。"""
    import container_reach as mod

    return mod


def test_run_with_timeout_adds_the_reached_env(gitfacts, container_reach, tmp_path, monkeypatch):
    monkeypatch.setattr(container_reach, "env_for", lambda cwd: {"NDF_WORKTREE": str(tmp_path)})
    code, timed_out = gitfacts.run_with_timeout(f'test "$NDF_WORKTREE" = "{tmp_path}"', str(tmp_path), 30)
    assert (code, timed_out) == (0, False)


def test_run_with_timeout_does_not_run_when_unreachable(gitfacts, container_reach, tmp_path, monkeypatch):
    def unreachable(cwd):
        raise container_reach.Unreachable("サービス app の作業ディレクトリはこの worktree ではない")

    monkeypatch.setattr(container_reach, "env_for", unreachable)
    marker = tmp_path / "ran"
    with pytest.raises(container_reach.Unreachable):
        gitfacts.run_with_timeout(f"touch {marker}", str(tmp_path), 30)
    assert not marker.exists()


def test_refactor_stops_when_unreachable(refactor, container_reach, monkeypatch, capsys):
    def unreachable(args):
        raise container_reach.Unreachable("サービス app のコンテナが動いていない")

    monkeypatch.setattr(refactor, "cmd_report", unreachable)
    monkeypatch.setattr(sys, "argv", ["refactor.py", "report", "1"])
    with pytest.raises(SystemExit) as e:
        refactor.main()
    assert e.value.code not in (0, None)
    assert "コンテナが動いていない" in capsys.readouterr().err
