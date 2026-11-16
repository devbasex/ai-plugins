"""配る Skill の表（`plugins/ndf-next/manifests/skills.json`）と、機能の対応表の Skill の行の一致。"""

from __future__ import annotations

from ndf.domains.manifests import table

from parity import table as parity
from parity.trial import probe


def test_the_table_lists_the_skills_s1a_moves():
    # 配る Skill の表は、S1a で移す Skill の名前の一覧（機能の対応表の Skill の行）から作った
    moved = {r["id"].split(":", 1)[1] for r in parity.load("features")["rows"] if r["kind"] == "skill" and r["to"] == "S1a"}
    assert {s.name for s in table.load(probe.PLUGIN_ROOT).skills} == moved
