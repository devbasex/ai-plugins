"""ラウンドの開始時に、レビュー用の作業ツリーを Pull Request の head へ揃える検査。

同期は作られるときと再開するときにしか行われていなかった。修正をレビュー用の作業ツリーの
外で行って push すると、次のラウンドは 1 つ前の内容をレビューする。指摘した側から見ると
修正が反映されていないため、対応済みの指摘が再び投稿される。

| 状態 | 扱い |
| --- | --- |
| 作業ツリーが head より古い | `git fetch` と `git reset --hard <headRefOid>` で揃える |
| head と一致していて変更が無い | 何もしない |
| 追跡対象のファイルに変更がある | 終了コード 8 で止める |
| 基準に含まれないローカルのコミットがある | 終了コード 8 で止める |
| `worktree_path` が無い | 同期せず続ける |
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess

import pytest

PR = 6170
REPO = "o/r"
BRANCH = "fix/current"
OID = "a" * 40
OLD_OID = "b" * 40


def _rest(head_branch: str = BRANCH, oid: str = OID, fork: bool = False) -> str:
    """`gh api -i repos/<repo>/pulls/<PR>` の応答（実測の形）。

    状態行だけが `\r` を持たず、以降のヘッダは `\r\n` で終わる。
    """
    body = {
        "number": PR,
        "user": {"login": "someone"},
        "head": {
            "ref": head_branch, "sha": oid,
            "repo": {"full_name": ("fork/r" if fork else REPO)},
        },
        "base": {"ref": "develop"},
    }
    return (
        "HTTP/2.0 200 OK\n"
        "Content-Type: application/json; charset=utf-8\r\n"
        "X-Ratelimit-Remaining: 4972\r\n"
        "\r\n"
        + json.dumps(body)
    )


class _Recorder:
    """subprocess.run を差し替えて、渡されたコマンドを記録する。

    既定は「作業ツリーが head より古く、変更は無い」状態を返す。
    """

    def __init__(
        self,
        *,
        view_rc: int = 0,
        view_stdout: str | None = None,
        fetch_rc: int = 0,
        base_present: bool = True,
        head_oid: str = OLD_OID,
        status: str = "",
        ahead: str = "0",
        reset_rc: int = 0,
        clean_rc: int = 0,
    ):
        self.calls: list[tuple[list[str], str | None]] = []
        self.view_rc = view_rc
        # head の取得は REST の 1 回になった（#271）。GraphQL へは投げない。
        self.view_stdout = view_stdout if view_stdout is not None else _rest()
        self.fetch_rc = fetch_rc
        self.base_present = base_present
        self.head_oid = head_oid
        self.status = status
        self.ahead = ahead
        self.reset_rc = reset_rc
        self.clean_rc = clean_rc

    def __call__(self, cmd, capture_output=False, text=False, cwd=None, **kwargs):
        self.calls.append((list(cmd), cwd))
        rc = 0
        stdout = ""
        if cmd[:3] == ["gh", "api", "-i"]:
            rc, stdout = self.view_rc, self.view_stdout
        elif cmd[:2] == ["git", "fetch"]:
            rc = self.fetch_rc
        elif cmd[:3] == ["git", "cat-file", "-e"]:
            rc = 0 if self.base_present else 1
        elif cmd[:2] == ["git", "status"]:
            stdout = self.status
        elif cmd[:2] == ["git", "rev-list"]:
            stdout = self.ahead + "\n"
        elif cmd == ["git", "rev-parse", "HEAD"]:
            stdout = self.head_oid + "\n"
        elif cmd[:2] == ["git", "rev-parse"]:
            stdout = self.head_oid[:7] + "\n"
        elif cmd[:3] == ["git", "reset", "--hard"]:
            rc = self.reset_rc
        elif cmd[:2] == ["git", "clean"]:
            rc = self.clean_rc
        return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr="boom")

    def commands(self) -> list[list[str]]:
        return [c for c, _ in self.calls]

    def issued(self, prefix: list[str]) -> bool:
        return any(c[:len(prefix)] == prefix for c in self.commands())


def _state(**over) -> dict:
    state = {
        "current_pr": PR,
        "repo": REPO,
        "max_rounds": 12,
        "rotate_after": 8,
        "only": None,
        "rounds": [],
        "deferred_nits": [],
        "final": None,
        "worktree_path": "/wt",
        "head_branch": "fix/stale",
    }
    state.update(over)
    return state


def _write(tmp_dir: pathlib.Path, state: dict) -> None:
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def _read(tmp_dir: pathlib.Path) -> dict:
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod, real_github):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(state_mod, "_is_registered_worktree", lambda path: True)
    return tmp_path


@pytest.fixture()
def run(monkeypatch, state_mod):
    def _set(rec: _Recorder) -> _Recorder:
        monkeypatch.setattr(state_mod.subprocess, "run", rec)
        return rec
    return _set


# ---------------- 揃える ----------------

def test_syncs_before_opening_a_round(tmp_dir, state_mod, run):
    """作業ツリーが古いとき、取り込みと基準への巻き戻しを発行してからラウンドを開く。"""
    rec = run(_Recorder())
    _write(tmp_dir, _state())

    state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert ["git", "fetch", "origin", BRANCH] in rec.commands()
    assert ["git", "reset", "--hard", OID] in rec.commands()
    assert len(_read(tmp_dir)["rounds"]) == 1
    # 同期はラウンドのエントリを開く前に済ませる。
    for cmd, cwd in rec.calls:
        if cmd[:3] == ["git", "reset", "--hard"]:
            assert cwd == "/wt"


def test_does_nothing_when_already_at_head(tmp_dir, state_mod, run, capsys):
    """head と一致していて変更が無ければ、巻き戻しも掃除も発行しない。"""
    rec = run(_Recorder(head_oid=OID))
    _write(tmp_dir, _state())

    state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert not rec.issued(["git", "reset", "--hard"])
    assert not rec.issued(["git", "clean"])
    assert "同期済み" in capsys.readouterr().err
    assert len(_read(tmp_dir)["rounds"]) == 1


# ---------------- 失われるものがあるときは止める ----------------

def test_stops_when_tracked_files_are_modified(tmp_dir, state_mod, run, capsys):
    """追跡対象の変更は、修正の工程が push を終えていない証拠である。捨てずに止める。"""
    rec = run(_Recorder(status=" M src/foo.py\n"))
    _write(tmp_dir, _state())

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert e.value.code == 8
    assert not rec.issued(["git", "reset", "--hard"])
    # 先頭が空白の状態でもパスが 1 文字欠けない。
    assert "src/foo.py" in capsys.readouterr().err


def test_stops_when_local_commits_are_not_pushed(tmp_dir, state_mod, run):
    """基準に含まれないローカルのコミットがあるときも止める。"""
    rec = run(_Recorder(ahead="2"))
    _write(tmp_dir, _state())

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert e.value.code == 8
    assert not rec.issued(["git", "reset", "--hard"])


def test_no_round_is_opened_when_sync_fails(tmp_dir, state_mod, run):
    """止まったとき、ラウンドのエントリは開かれない。原因を取り除けば同じ番号から再開できる。"""
    run(_Recorder(status="M  src/foo.py\n"))
    _write(tmp_dir, _state(rounds=[]))

    with pytest.raises(SystemExit):
        state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert len(_read(tmp_dir)["rounds"]) == 0


def test_a_sync_failure_does_not_end_the_loop(tmp_dir, state_mod, run):
    """終了コード 1 はループを抜ける値である。同期の失敗で返さない。"""
    run(_Recorder(reset_rc=1))
    _write(tmp_dir, _state())

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert e.value.code != 1
    assert e.value.code == 8


# ---------------- 同期先の取り方 ----------------

def test_head_ref_comes_from_github(tmp_dir, state_mod, run):
    """状態ファイルの `head_branch` ではなく、その時点の head を GitHub から取る。

    `squash` の巻き直しは新しいブランチを作るが、状態ファイルの `head_branch` は
    更新されない。そのまま使うと巻き直し前のブランチへ戻すことになる。
    """
    rec = run(_Recorder())
    _write(tmp_dir, _state(head_branch="fix/stale"))

    state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert ["gh", "api", "-i", f"repos/{REPO}/pulls/{PR}"] in rec.commands()
    # GraphQL 側の枠は使わない。
    assert not rec.issued(["gh", "pr", "view"])
    assert not any("fix/stale" in " ".join(c) for c in rec.commands())


def test_resolved_head_branch_is_written_back(tmp_dir, state_mod, run):
    """取れたブランチ名を状態ファイルへ書き戻す。再開の経路も現在の head を見る。"""
    run(_Recorder())
    _write(tmp_dir, _state(head_branch="fix/stale"))

    state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert _read(tmp_dir)["head_branch"] == BRANCH


def test_stops_when_head_ref_cannot_be_resolved(tmp_dir, state_mod, run):
    """head を解決できないとき、状態ファイルの古い値へ落とさずに止める。"""
    rec = run(_Recorder(view_rc=1))
    _write(tmp_dir, _state())

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert e.value.code == 8
    assert not rec.issued(["git", "reset", "--hard"])


def test_fetches_the_pull_ref_for_a_fork(tmp_dir, state_mod, run):
    """フォークの Pull Request では `refs/pull/<PR>/head` を取り込む。

    head branch は base のリポジトリに無いため、`origin/<head branch>` は解決できない。
    """
    rec = run(_Recorder(view_stdout=_rest(fork=True)))
    _write(tmp_dir, _state())

    state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert ["git", "fetch", "origin", f"refs/pull/{PR}/head"] in rec.commands()
    assert not any(f"origin/{BRANCH}" in c for cmd in rec.commands() for c in cmd)
    assert ["git", "reset", "--hard", OID] in rec.commands()


def test_strict_stops_when_the_base_commit_is_missing(tmp_dir, state_mod, run):
    """基準を手元に持てないとき、`gh pr checkout --detach` を発行せずに止める。

    この操作は HEAD を動かすが、動かす前に何が失われるかを数える材料が無い。
    """
    rec = run(_Recorder(base_present=False))
    _write(tmp_dir, _state())

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert e.value.code == 8
    assert not rec.issued(["gh", "pr", "checkout"])


# ---------------- 同期の対象が無い ----------------

def test_continues_without_a_worktree_path(tmp_dir, state_mod, run):
    """同期の対象が無いことと、同期できないことは分けて扱う。前者は続ける。"""
    rec = run(_Recorder())
    state = _state()
    del state["worktree_path"]
    _write(tmp_dir, state)

    state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert len(_read(tmp_dir)["rounds"]) == 1
    assert not rec.issued(["gh", "pr", "view"])
