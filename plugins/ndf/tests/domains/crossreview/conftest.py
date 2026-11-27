"""cross-review のテストに共通する fixture。"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pytest

from ._repos import BRANCH, Repos, commit, make_repos, sh


@pytest.fixture
def repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repos:
    return make_repos(tmp_path, monkeypatch)


@pytest.fixture
def wt(repos: Repos, tmp_path: Path) -> Path:
    """セクション B を足した PR のブランチの worktree（run と記録のテストが使う）。"""
    commit(repos.other, "doc.md", "# 設計\n\n## セクション A\n\n本文\n\n## セクション B\n\n足した\n", "セクション B")
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    sh(repos.main, "fetch", "-q", "origin")
    return repos.add_worktree(tmp_path / "wt")


@pytest.fixture(autouse=True)
def _runtimes_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """担当すべてを起動できる前提にする（学習の設定のその場の確かめ `training.ensure` は HTTP を打つ。#3907）。

    始める前の確かめそのものを見るテストは `training.ensure` を上書きする。
    """
    from ndf.adapters import training

    monkeypatch.setattr(training, "ensure", lambda runtime, env=None, now=None, read=None: None)


class _Everything(frozenset):
    """どのパスも含む変更ファイルの集まり（差分で絞らない偽物）。"""

    def __contains__(self, item: object) -> bool:
        return True


class FakePort:
    """PR のスレッドの偽物。作った・返信した・resolve したものを残す。`fail` ならレビューとスレッドを作るのを断り、
    `refuse_position` ならレビュー（`batches` に残す）を行の位置で断る。作ったスレッドは PR ごとに持ち、`open_threads` が自分の書いた
    スレッドとして返す（返信と resolve も反映する）。`opened` はレビューと 1 件ずつの作成の両方で作ったスレッドを並べる。
    読む口（#3941）は `existing`（既存のコメント）と `left`（スレッド）を返し、読んだ回数を数える（`reads` は PR コメント、
    `review_reads` は相手役へ渡すだけのレビューコメントとレビュー本文。PR 3944 の r2-10）。"""

    def __init__(self, fail: bool = False, files=None):
        self.opened: list[tuple[str, object, str, str]] = []
        self.batches: list[tuple[str, list]] = []
        self.singles: list[tuple[str, object, str, str]] = []
        self.replies: list[tuple[int, str]] = []
        self.resolved: list[int] = []
        self.fail = fail
        self.refuse_position = False
        self.existing: list = []
        self.left: list = []
        self.files = files
        self.made: dict[int, list] = {}
        self.pr = 0
        self.reads = 0
        self.review_reads = 0
        self.thread_reads = 0
        self.changed_reads = 0
        # PR のマージ先のブランチ（None なら読み取りに失敗する）と、読んだ回数
        self.base_ref: str | None = "main"
        self.base_reads = 0

    def at(self, pr: int) -> "FakePort":
        """PR を切り替える（作ったスレッドは PR ごとに読む）。"""
        self.pr = pr
        return self

    def comments(self):
        self.reads += 1
        return [r for r in self.existing if r.kind == "comment"]

    def reviews(self):
        self.review_reads += 1
        return [r for r in self.existing if r.kind != "comment"]

    def changed(self):
        self.changed_reads += 1
        return _Everything() if self.files is None else frozenset(self.files)

    def base(self):
        self.base_reads += 1
        if self.base_ref is None:
            raise RuntimeError("PR を読めない")
        return self.base_ref

    def read_pr(self):
        return None

    def open_threads(self):
        self.thread_reads += 1
        out = []
        for t in [*self.left, *self.made.get(self.pr, [])]:
            replies = tuple(body for cid, body in self.replies if cid == t.comment)
            out.append(t._replace(resolved=t.resolved or t.comment in self.resolved, replies=t.replies + replies))
        return out

    def _make(self, path, line, commit, body) -> int:
        from ndf.domains.crossreview import prior_decide

        self.opened.append((path, line, commit, body))
        cid = 1000 + len(self.opened)
        self.made.setdefault(self.pr, []).append(prior_decide.Thread(cid, body, False, path, line, True))
        return cid

    def open_review(self, commit, comments):
        from ndf.domains.crossreview import threads

        if self.fail:
            raise RuntimeError("書けない")
        if self.refuse_position:
            raise threads.PositionRefused("pull_request_review_thread.line must be part of the diff")
        self.batches.append((commit, list(comments)))
        for c in comments:
            self._make(c.path, c.line, commit, c.body)
        return len(self.batches)

    def open_thread(self, path, line, commit, body):
        if self.fail:
            raise RuntimeError("書けない")
        self.singles.append((path, line, commit, body))
        return self._make(path, line, commit, body)

    def reply(self, comment_id, body):
        self.replies.append((comment_id, body))

    def resolve(self, comment_id):
        self.resolved.append(comment_id)


class FakeHub:
    """PR へ書く口の偽物。記録のコメントとスレッドを残す（#3939）。同じ PR の公開のロックは何もしない（本物のホームの下へ
    ロックのファイルを書かない）。"""

    def __init__(self):
        from ndf.domains.crossreview import cli

        self.comments: list[tuple[str, int, str]] = []
        self.port = FakePort()
        self.slug = ""
        self.made = 0
        self.clean: Any = cli._redacted

    def make(self, repo_arg, state_repo, cwd):
        from ndf.domains.crossreview import cli

        self.made += 1

        slug = self.slug = repo_arg or state_repo or "o/r"

        def comment(pr, body, previous):
            self.comments.append((slug, pr, body, previous))
            return previous or f"https://github.com/{slug}/pull/{pr}#issuecomment-{len(self.comments)}"

        return cli.Hub(comment, self.port.at, self.port.at, lambda texts: self.clean(texts), lambda pr: contextlib.nullcontext())


@pytest.fixture(autouse=True)
def hub(monkeypatch: pytest.MonkeyPatch) -> FakeHub:
    """PR へ書く口を偽物にする（run の終わりの記録は GitHub へ書く。#3939）。"""
    from ndf.domains.crossreview import cli

    fake = FakeHub()
    monkeypatch.setattr(cli, "HUB", fake.make)
    return fake
