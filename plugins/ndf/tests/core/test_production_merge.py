"""本番チャネルの判定の部品（`ndf.core.production_merge`）を、設計の表「判定の部品が返す値」の行ごとに確かめる（#4409 の決定 2）。"""

from __future__ import annotations

import pytest

from ndf.core import production_merge as pm
from ndf.core.production_merge import Verdict


def _section(release, production="main"):
    return {"branches": {"base": "main", "production": production}, "release": release}


@pytest.mark.parametrize(
    ("release", "want"),
    [
        ({"deliveries": [{"channel": "production", "kind": "auto"}], "cd": None}, Verdict.AUTOMATIC),
        ({"deliveries": [{"channel": "production", "kind": "manual"}], "cd": None}, Verdict.MANUAL),
        ({"deliveries": [], "cd": None}, Verdict.MANUAL),
        ({"deliveries": [{"channel": "staging", "kind": "auto"}], "cd": None}, Verdict.MANUAL),
        ({"cd": {"workflow": "release.yml"}}, Verdict.AUTOMATIC),
        ({"cd": None}, Verdict.UNSET),
        ({}, Verdict.UNKNOWN),
        ({"cd": "release.yml"}, Verdict.UNKNOWN),
        ({"deliveries": "auto", "cd": None}, Verdict.UNKNOWN),
    ],
)
def test_automatic_rows_follows_the_release_section(release, want):
    assert pm.automatic_rows(_section(release)) is want


@pytest.mark.parametrize("section", [None, [], "x", {"release": []}, {"release": None}, {}])
def test_automatic_rows_is_unknown_for_broken_shapes(section):
    assert pm.automatic_rows(section) is Verdict.UNKNOWN


def test_judge_is_not_production_for_another_base():
    section = _section({"deliveries": [{"channel": "production", "kind": "auto"}]})
    assert pm.judge(section, "develop") is Verdict.NOT_PRODUCTION
    assert pm.judge(section, "main") is Verdict.AUTOMATIC


@pytest.mark.parametrize("production", ["", None, 3])
def test_judge_is_unknown_without_a_production_branch(production):
    assert pm.judge(_section({"cd": None}, production=production), "main") is Verdict.UNKNOWN


def test_judge_is_unknown_for_no_section():
    assert pm.judge(None, "main") is Verdict.UNKNOWN
    assert pm.judge({"release": {"cd": None}}, "main") is Verdict.UNKNOWN


@pytest.mark.parametrize(
    ("verdict", "want"),
    [
        (Verdict.NOT_PRODUCTION, False),
        (Verdict.MANUAL, False),
        (Verdict.AUTOMATIC, True),
        (Verdict.UNSET, True),
        (Verdict.UNKNOWN, True),
    ],
)
def test_needs_approval_and_reaches_production_match_the_table(verdict, want):
    assert pm.needs_approval(verdict) is want
    assert pm.reaches_production(verdict) is want
