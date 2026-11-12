"""Pull Request の衝突の状態の読み方（lib/gh_pr_info.py の fetch_mergeable・#3076）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gh_fake import fake, rest_out  # noqa: E402,F401
import gh_call  # noqa: E402
import gh_pr_info  # noqa: E402


@pytest.mark.parametrize(
    "mergeable, expected",
    [(True, "MERGEABLE"), (False, "CONFLICTING"), (None, "UNKNOWN")],
)
def test_fetch_mergeable_translates_the_rest_value(fake, mergeable, expected):
    fake.on("api", "-i", "repos/o/r/pulls/7", out=rest_out({"number": 7, "mergeable": mergeable}))
    assert gh_pr_info.fetch_mergeable("o/r", 7) == expected
    assert fake.argvs() == ["api -i repos/o/r/pulls/7"]


def test_fetch_mergeable_is_none_when_the_read_fails(fake):
    fake.on("api", rc=1, out="", err="HTTP 502: Bad Gateway")
    assert gh_pr_info.fetch_mergeable("o/r", 7) is None


def test_fetch_mergeable_is_none_when_the_body_lacks_the_field(fake):
    fake.on("api", "-i", "repos/o/r/pulls/7", out=rest_out({"number": 7}))
    assert gh_pr_info.fetch_mergeable("o/r", 7) is None


def test_fetch_mergeable_does_not_raise(monkeypatch):
    def boom(path):
        raise OSError("gh が無い")

    monkeypatch.setattr(gh_call, "rest_cached", boom)
    assert gh_pr_info.fetch_mergeable("o/r", 7) is None
