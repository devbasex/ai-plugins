"""state.py `_sync_worktree()` の同期テスト。

既存 worktree をそのまま流用すると、レビュー担当は前回の実行が残した古い差分を
読む。指摘は現在の Pull Request に無い行へ出るか、直したはずの箇所へ再び出る。
どちらも投稿されるため、読む側からは見分けが付かない。
"""
from __future__ import annotations

import subprocess


class _Recorder:
    """subprocess.run を差し替えて、渡されたコマンドを記録する。"""

    def __init__(self, fetch_rc: int = 0, reset_rc: int = 0, checkout_rc: int = 0,
                 clean_rc: int = 0, status: str = ""):
        self.calls: list[tuple[list[str], str | None]] = []
        self.fetch_rc = fetch_rc
        self.reset_rc = reset_rc
        self.checkout_rc = checkout_rc
        self.clean_rc = clean_rc
        self.status = status

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
        elif cmd[:2] == ["git", "clean"]:
            rc = self.clean_rc
        elif cmd[:3] == ["git", "cat-file", "-e"]:
            rc = self.fetch_rc
        elif cmd[:2] == ["git", "status"]:
            stdout = self.status
        elif cmd[:2] == ["git", "rev-list"]:
            stdout = "0\n"
        elif cmd == ["git", "rev-parse", "HEAD"]:
            stdout = "b" * 40 + "\n"
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
    tmp ディレクトリを消さないためで、除外は `-e` でも重ねて指定する。
    """
    rec = _Recorder()
    monkeypatch.setattr(state_mod.subprocess, "run", rec)
    state_mod._sync_worktree("/wt", 42, "feature/x")

    clean = [c for c in rec.commands() if c[:2] == ["git", "clean"]]
    assert clean and clean[0][:3] == ["git", "clean", "-fd"]
    assert "-e" in clean[0]
    assert "-x" not in clean[0]


def test_excludes_the_tmp_dir_from_clean(monkeypatch, state_mod):
    """tmp ディレクトリを掃除の対象から外す。

    状態ファイルと結果ファイルはそこにある。`.cross_review/` が `.gitignore` に
    載っているのはこのリポジトリの都合で、レビュー対象のリポジトリで載っている
    保証は無い。載っていなければ、ラウンドごとの掃除がそれらを消す。
    """
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", "/wt/.review-tmp")
    rec = _Recorder()
    monkeypatch.setattr(state_mod.subprocess, "run", rec)
    state_mod._sync_worktree("/wt", 42, "feature/x")

    clean = [c for c in rec.commands() if c[:2] == ["git", "clean"]][0]
    assert "-e" in clean
    assert ".cross_review" in clean
    assert ".review-tmp" in clean


def test_strict_mode_keeps_tracked_changes(monkeypatch, state_mod):
    """`strict=True` で追跡対象の変更があるとき、巻き戻さずに終了コード 8 で止める。

    ラウンドの開始時に見つかる変更は、同じループの修正の工程が今まさに残したものである。
    """
    rec = _Recorder(status=" M src/foo.py\n")
    monkeypatch.setattr(state_mod.subprocess, "run", rec)
    head = state_mod.HeadRef(branch="feature/x", oid="a" * 40, is_fork=False)

    with __import__("pytest").raises(SystemExit) as e:
        state_mod._sync_worktree("/wt", 42, head, strict=True)

    assert e.value.code == 8
    assert not any(c[:3] == ["git", "reset", "--hard"] for c in rec.commands())


def test_falls_back_to_gh_pr_checkout(monkeypatch, state_mod):
    """`strict=False` で基準を手元に持てないときは、フォールバックの後に掃除まで進む。"""
    rec = _Recorder(fetch_rc=1)
    monkeypatch.setattr(state_mod.subprocess, "run", rec)
    state_mod._sync_worktree("/wt", 42, "feature/x")

    assert ["gh", "pr", "checkout", "42", "--detach"] in rec.commands()
    assert any(c[:2] == ["git", "clean"] for c in rec.commands())


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


def test_dies_when_clean_fails(monkeypatch, state_mod):
    """追跡対象外のファイルを消せないときも止める。

    差分そのものは reset で合っているが、残骸を抱えたまま進むと fix 担当の
    `git add -A` で Pull Request へ混ざる。
    """
    rec = _Recorder(clean_rc=1)
    monkeypatch.setattr(state_mod.subprocess, "run", rec)
    with __import__("pytest").raises(SystemExit):
        state_mod._sync_worktree("/wt", 42, "feature/x")
