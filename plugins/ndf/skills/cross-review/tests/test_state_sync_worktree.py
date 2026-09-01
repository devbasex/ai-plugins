"""state.py `_sync_worktree()` の同期テスト。

既存 worktree をそのまま流用すると、レビュー担当は前回の実行が残した古い差分を
読む。指摘は現在の Pull Request に無い行へ出るか、直したはずの箇所へ再び出る。
どちらも投稿されるため、読む側からは見分けが付かない。
"""
from __future__ import annotations

import subprocess


class _Recorder:
    """subprocess.run を差し替えて、渡されたコマンドを記録する。"""

    def __init__(self, fetch_rc: int = 0, reset_rc: int = 0, checkout_rc: int = 0):
        self.calls: list[tuple[list[str], str | None]] = []
        self.fetch_rc = fetch_rc
        self.reset_rc = reset_rc
        self.checkout_rc = checkout_rc

    def __call__(self, cmd, capture_output=False, text=False, cwd=None, **kwargs):
        self.calls.append((list(cmd), cwd))
        rc = 0
        stdout = ""
        if cmd[:2] == ["git", "fetch"]:
            rc = self.fetch_rc
        elif cmd[:3] == ["git", "reset", "--hard"]:
            rc = self.reset_rc
        elif cmd[:2] == ["gh", "pr"]:
            rc = self.checkout_rc
        elif cmd[:2] == ["git", "rev-parse"]:
            stdout = "abc1234\n"
        return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr="boom")

    def commands(self) -> list[list[str]]:
        return [c for c, _ in self.calls]


def test_resets_worktree_to_pr_head(monkeypatch, state_mod):
    """fetch が通れば origin/<head> へ hard reset する。"""
    rec = _Recorder()
    monkeypatch.setattr(state_mod.subprocess, "run", rec)
    state_mod._sync_worktree("/wt", 42, "feature/x")

    assert ["git", "fetch", "origin", "feature/x"] in rec.commands()
    assert ["git", "reset", "--hard", "origin/feature/x"] in rec.commands()
    # reset と clean は worktree の中で実行する。親リポジトリで走らせない。
    for cmd, cwd in rec.calls:
        if cmd[:2] in (["git", "reset"], ["git", "clean"]):
            assert cwd == "/wt"


def test_removes_untracked_leftovers(monkeypatch, state_mod):
    """前回の実行が残した追跡対象外のファイルを消す。

    残すと fix 担当の `git add -A` で Pull Request へ混ざる。`-x` を付けないのは
    `.cross_review/` が `.gitignore` に載っており、state.json を消さないため。
    """
    rec = _Recorder()
    monkeypatch.setattr(state_mod.subprocess, "run", rec)
    state_mod._sync_worktree("/wt", 42, "feature/x")

    assert ["git", "clean", "-fd"] in rec.commands()
    assert not any(c[:2] == ["git", "clean"] and "-x" in c for c in rec.commands())


def test_falls_back_to_gh_pr_checkout_for_fork(monkeypatch, state_mod):
    """origin に head branch が無いフォーク PR は gh pr checkout で合わせる。"""
    rec = _Recorder(fetch_rc=1)
    monkeypatch.setattr(state_mod.subprocess, "run", rec)
    state_mod._sync_worktree("/wt", 42, "feature/x")

    assert ["gh", "pr", "checkout", "42", "--detach"] in rec.commands()
    assert not any(c[:3] == ["git", "reset", "--hard"] for c in rec.commands())


def test_dies_when_reset_fails(monkeypatch, state_mod):
    """同期できないまま進めない。古い差分を読ませるより止める。"""
    rec = _Recorder(reset_rc=1)
    monkeypatch.setattr(state_mod.subprocess, "run", rec)
    with __import__("pytest").raises(SystemExit):
        state_mod._sync_worktree("/wt", 42, "feature/x")
