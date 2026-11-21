"""cross-review のテストに共通する fixture。"""

from __future__ import annotations

from pathlib import Path

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


class FakePort:
    """PR のスレッドの偽物。作った・返信した・resolve したものを残す。`fail` ならスレッドを作るのを断る。
    読む口（#3941）は `existing`（既存のコメント）と `left`（スレッド）を返し、読んだ回数を数える（`reads` は PR コメント、
    `review_reads` は相手役へ渡すだけのレビューコメントとレビュー本文。PR 3944 の r2-10）。"""

    def __init__(self, fail: bool = False):
        self.opened: list[tuple[str, object, str, str]] = []
        self.replies: list[tuple[int, str]] = []
        self.resolved: list[int] = []
        self.fail = fail
        self.existing: list = []
        self.left: list = []
        self.reads = 0
        self.review_reads = 0

    def comments(self):
        self.reads += 1
        return [r for r in self.existing if r.kind == "comment"]

    def reviews(self):
        self.review_reads += 1
        return [r for r in self.existing if r.kind != "comment"]

    def open_threads(self):
        return list(self.left)

    def open_thread(self, path, line, commit, body):
        if self.fail:
            raise RuntimeError("書けない")
        self.opened.append((path, line, commit, body))
        return 1000 + len(self.opened)

    def reply(self, comment_id, body):
        self.replies.append((comment_id, body))

    def resolve(self, comment_id):
        self.resolved.append(comment_id)


class FakeHub:
    """PR へ書く口の偽物。記録のコメントとスレッドを残す（#3939）。"""

    def __init__(self):
        self.comments: list[tuple[str, int, str]] = []
        self.port = FakePort()
        self.slug = ""
        self.made = 0

    def make(self, repo_arg, state_repo, cwd):
        from ndf.domains.crossreview import cli

        self.made += 1

        slug = self.slug = repo_arg or state_repo or "o/r"

        def comment(pr, body, previous):
            self.comments.append((slug, pr, body, previous))
            return previous or f"https://github.com/{slug}/pull/{pr}#issuecomment-{len(self.comments)}"

        return cli.Hub(comment, lambda pr: self.port, lambda pr: self.port)


@pytest.fixture(autouse=True)
def hub(monkeypatch: pytest.MonkeyPatch) -> FakeHub:
    """PR へ書く口を偽物にする（run の終わりの記録は GitHub へ書く。#3939）。"""
    from ndf.domains.crossreview import cli

    fake = FakeHub()
    monkeypatch.setattr(cli, "HUB", fake.make)
    return fake
