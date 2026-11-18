"""範囲の起点と終点（#3615 の決定 6・I4）。"""

from __future__ import annotations

import pytest

from ndf.core.branch_strategy import Strategy
from ndf.domains.release import since_decide
from ndf.domains.release.since_decide import SpanKind

from ._fakes import section


def _mobile_with_tags() -> dict:
    got = section("mobile")
    got["release"].update(version_store={"kind": "tag"}, tag="v{version}", github_release=False, cd={"workflow": "ndf-release.yml"})
    return got


@pytest.mark.parametrize(
    ("kind", "want"),
    [("web-develop", SpanKind.BASE), ("plugin", SpanKind.TRUNK), ("web-trunk", SpanKind.PR), ("docs", SpanKind.PR)],
)
def test_kind_of_follows_the_route(kind: str, want: SpanKind) -> None:
    assert since_decide.kind_of(Strategy(section(kind))) == want


def test_release_branch_collects_units() -> None:
    assert since_decide.kind_of(Strategy(_mobile_with_tags())) == SpanKind.UNITS


@pytest.mark.parametrize(
    ("tagged", "introduced", "want"),
    [("t", "i", "t"), (None, "i", "i"), (None, None, "root")],
)
def test_prev_release_prefers_tag_then_introduced_then_root(tagged: str | None, introduced: str | None, want: str) -> None:
    assert since_decide.prev_release(tagged, introduced, "root") == want


def test_since_and_until_per_kind() -> None:
    """I4: base は本番の HEAD から、trunk・units は前のリリースの起点から、pr は分岐点から。"""
    assert since_decide.since_of(SpanKind.BASE, "prod", None, None) == "prod"
    assert since_decide.since_of(SpanKind.TRUNK, "prod", "prev", None) == "prev"
    assert since_decide.since_of(SpanKind.UNITS, "prod", "prev", None) == "prev"
    assert since_decide.since_of(SpanKind.PR, "prod", None, "fork") == "fork"
    # trunk で前のリリースの起点が本番の HEAD と同じでもそのまま返す（範囲が空なら呼び手が NothingToRelease にする）
    assert since_decide.since_of(SpanKind.TRUNK, "prod", "prod", None) == "prod"
    assert since_decide.until_of(SpanKind.BASE, "base", "prod", None, None) == "base"
    assert since_decide.until_of(SpanKind.TRUNK, "base", "prod", None, None) == "prod"
    assert since_decide.until_of(SpanKind.UNITS, "base", "prod", "units", None) == "units"
    assert since_decide.until_of(SpanKind.PR, "base", "prod", None, "pr") == "pr"
    with pytest.raises(ValueError):
        since_decide.since_of(SpanKind.PR, "prod", None, None)
