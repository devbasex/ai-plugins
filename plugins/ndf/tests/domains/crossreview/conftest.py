"""cross-review のテストに共通する fixture。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ._repos import Repos, make_repos


@pytest.fixture
def repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repos:
    return make_repos(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def _runtimes_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """担当すべてを起動できる前提にする（学習の設定のその場の確かめ `training.ensure` は HTTP を打つ。#3907）。

    始める前の確かめそのものを見るテストは `training.ensure` を上書きする。
    """
    from ndf.adapters import training

    monkeypatch.setattr(training, "ensure", lambda runtime, env=None, now=None, read=None: None)


class FakePort:
    """PR のスレッドの偽物。作った・返信した・resolve したものを残す。`fail` ならスレッドを作るのを断る。"""

    def __init__(self, fail: bool = False):
        self.opened: list[tuple[str, object, str, str]] = []
        self.replies: list[tuple[int, str]] = []
        self.resolved: list[int] = []
        self.fail = fail

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

    def make(self, repo_arg, state_repo, cwd):
        from ndf.domains.crossreview import cli

        slug = self.slug = repo_arg or state_repo or "o/r"

        def comment(pr, body, previous):
            self.comments.append((slug, pr, body, previous))
            return previous or f"https://github.com/{slug}/pull/{pr}#issuecomment-{len(self.comments)}"

        return cli.Hub(comment, lambda pr: self.port)


@pytest.fixture(autouse=True)
def hub(monkeypatch: pytest.MonkeyPatch) -> FakeHub:
    """PR へ書く口を偽物にする（run の終わりの記録は GitHub へ書く。#3939）。"""
    from ndf.domains.crossreview import cli

    fake = FakeHub()
    monkeypatch.setattr(cli, "HUB", fake.make)
    return fake
