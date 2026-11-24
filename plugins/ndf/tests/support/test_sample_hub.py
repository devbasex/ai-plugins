"""GitHub のアダプターが、録った見本の応答を読める（テストダブル `sample_hub`。#3687 の決定 8）。"""

from __future__ import annotations

import pytest
from sample_hub import SampleHub

from ndf.adapters import github


def test_github_reads_the_recorded_issue() -> None:
    hub = SampleHub()
    api = github.GitHub(hub.repo, github.client(auth="t", transport=hub.transport))
    title, authored = api.issue_text(hub.issue["number"])
    assert (title, authored.body, authored.author) == (hub.issue["title"], hub.issue["body"], hub.issue["user"]["login"])
    assert authored.association == hub.issue["author_association"]
    with pytest.raises(github.GitHubError):
        api.issue_text(hub.issue["number"] + 1)
    assert [method for method, _ in hub.sent] == ["GET", "GET"]
