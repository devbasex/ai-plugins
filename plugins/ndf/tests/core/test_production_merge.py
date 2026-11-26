"""本番チャネルの判定の部品（`ndf.core.production_merge`）を、設計の表「判定の部品が返す値」の行ごとに確かめる（#4409 の決定 2）。"""

from __future__ import annotations

import pytest

from ndf.core import production_merge as pm
from ndf.core.production_merge import Verdict


def _section(release, production="main"):
    return {"branches": {"base": "main", "production": production}, "release": release}


_AUTO = {"deliveries": [{"channel": "production", "kind": "auto"}]}

# （呼ぶ部品・引数・期待）。設計の表「判定の部品が返す値」の行ごとに 1 行
CASES = [
    pytest.param(pm.automatic_rows, (_section({**_AUTO, "cd": None}),), Verdict.AUTOMATIC, id="rows-auto-delivery"),
    pytest.param(
        pm.automatic_rows,
        (_section({"deliveries": [{"channel": "production", "kind": "manual"}], "cd": None}),),
        Verdict.MANUAL,
        id="rows-manual-delivery",
    ),
    pytest.param(pm.automatic_rows, (_section({"deliveries": [], "cd": None}),), Verdict.MANUAL, id="rows-no-delivery"),
    pytest.param(
        pm.automatic_rows, (_section({"deliveries": [{"kind": "auto"}], "cd": None}),), Verdict.AUTOMATIC, id="rows-channel-omitted"
    ),
    pytest.param(
        pm.automatic_rows,
        (_section({"deliveries": [{"channel": None, "kind": "auto"}], "cd": None}),),
        Verdict.AUTOMATIC,
        id="rows-channel-null",
    ),
    pytest.param(
        pm.automatic_rows, (_section({"deliveries": [{"kind": "manual"}], "cd": None}),), Verdict.MANUAL, id="rows-channel-omitted-manual"
    ),
    pytest.param(
        pm.automatic_rows,
        (_section({"deliveries": [{"channel": "staging", "kind": "auto"}], "cd": None}),),
        Verdict.MANUAL,
        id="rows-staging-only",
    ),
    pytest.param(pm.automatic_rows, (_section({"cd": {"workflow": "release.yml"}}),), Verdict.AUTOMATIC, id="rows-cd-workflow"),
    pytest.param(pm.automatic_rows, (_section({"cd": None}),), Verdict.UNSET, id="rows-cd-null"),
    pytest.param(pm.automatic_rows, (_section({}),), Verdict.UNKNOWN, id="rows-empty-release"),
    pytest.param(pm.automatic_rows, (_section({"cd": "release.yml"}),), Verdict.UNKNOWN, id="rows-cd-string"),
    pytest.param(pm.automatic_rows, (_section({"deliveries": "auto", "cd": None}),), Verdict.UNKNOWN, id="rows-deliveries-string"),
    *[
        pytest.param(pm.automatic_rows, (section,), Verdict.UNKNOWN, id=f"rows-broken-{name}")
        for name, section in [
            ("none", None),
            ("list", []),
            ("str", "x"),
            ("release-list", {"release": []}),
            ("release-none", {"release": None}),
            ("empty", {}),
        ]
    ],
    pytest.param(pm.judge, (_section(_AUTO), "develop"), Verdict.NOT_PRODUCTION, id="judge-another-base"),
    pytest.param(pm.judge, (_section(_AUTO), "main"), Verdict.AUTOMATIC, id="judge-production-base"),
    *[
        pytest.param(pm.judge, (_section({"cd": None}, production=p), "main"), Verdict.UNKNOWN, id=f"judge-production-{p!r}")
        for p in ["", None, 3]
    ],
    pytest.param(pm.judge, (None, "main"), Verdict.UNKNOWN, id="judge-no-section"),
    pytest.param(pm.judge, ({"release": {"cd": None}}, "main"), Verdict.UNKNOWN, id="judge-no-branches"),
    *[
        pytest.param(fn, (verdict,), want, id=f"{fn.__name__}-{verdict.value}")
        for verdict, want in [
            (Verdict.NOT_PRODUCTION, False),
            (Verdict.MANUAL, False),
            (Verdict.AUTOMATIC, True),
            (Verdict.UNSET, True),
            (Verdict.UNKNOWN, True),
        ]
        for fn in (pm.needs_approval, pm.reaches_production)
    ],
]


@pytest.mark.parametrize(("call", "args", "want"), CASES)
def test_parts_follow_the_table(call, args, want):
    assert call(*args) is want
