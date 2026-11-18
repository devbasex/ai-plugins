"""development-workflow の工程表と、工程の定義（`contracts/workflow/modes/`）の工程の並びを照らす（#3092 の条件 22）。

工程表は「モードごとに起動する Skill」の表で、行が工程、列がモードである。`—` の欄はそのモードに無い工程である。
モードごとに、`—` でない行を上から並べた工程の名前が、そのモードの工程の定義の `do` の並びと一致することを確かめる。
"""

from __future__ import annotations

import json
import re

import pytest

from ndf import identity

SKILL = identity.ROOT / "skills" / "development-workflow" / "SKILL.md"
MODES = identity.ROOT / "contracts" / "workflow" / "modes"
HEADING = "## モードごとに起動する Skill"
ABSENT = "—"


def _cells(line: str) -> list[str]:
    return [c.strip().strip("`") for c in line.strip().strip("|").split("|")]


def _table() -> tuple[list[str], list[list[str]]]:
    """工程表の見出し（モードの名前）と行（工程の名前と各モードの欄）。"""
    lines = SKILL.read_text(encoding="utf-8").splitlines()
    start = lines.index(HEADING)
    rows = []
    for line in lines[start + 1 :]:
        if rows and not line.startswith("|"):
            break
        if line.startswith("|"):
            rows.append(_cells(line))
    return rows[0][1:], rows[2:]


def _stages_in_table(mode: str) -> list[str]:
    modes, body = _table()
    col = modes.index(mode) + 1
    return [row[0] for row in body if row[col] != ABSENT]


def _stages_in_contract(mode: str) -> list[str]:
    data = json.loads((MODES / f"{mode}.json").read_text(encoding="utf-8"))
    return [next(iter(step)) for step in data["do"]]


def test_table_lists_every_mode_of_the_contract():
    modes, _ = _table()
    assert sorted(modes) == sorted(p.stem for p in MODES.glob("*.json"))


@pytest.mark.parametrize("mode", sorted(p.stem for p in MODES.glob("*.json")))
def test_stage_order_matches_the_mode_definition(mode):
    assert _stages_in_table(mode) == _stages_in_contract(mode)


def test_a_changed_row_is_caught(monkeypatch, tmp_path):
    """表の行を入れ替えると並びが食い違い、検証が落ちる（検証が空振りしていないことの確かめ）。"""
    text = SKILL.read_text(encoding="utf-8")
    swapped = re.sub(r"^(\| 完了判定 .*)\n(\| Pull Request .*)$", r"\2\n\1", text, flags=re.M)
    assert swapped != text
    fake = tmp_path / "SKILL.md"
    fake.write_text(swapped, encoding="utf-8")
    monkeypatch.setitem(globals(), "SKILL", fake)
    assert _stages_in_table("standard") != _stages_in_contract("standard")
