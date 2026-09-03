"""必須の工程の一覧が工程表から導けることを固定する（#221-5）。

判定は `scripts/lib/workflow-common.sh` の分類表が持つ。**その表は工程表から機械で
導ける。** 工程表の側だけを変えると、必須と判定する工程が古いまま残る。この検査は
工程表を読み直して分類し、ライブラリの表と突き合わせる。

分類の規則は 3 つである。

| 工程表のセル | 分類 |
| --- | --- |
| `—` | 対象外 |
| `任意`、または丸括弧で条件を添えたもの | 条件付き |
| それ以外 | 必須 |
"""
from __future__ import annotations

import re

import pytest

from workflow_helpers import LIB, SKILL_DIR

SKILL = SKILL_DIR / "SKILL.md"
WORKFLOW_TABLE_HEADING = "## モードごとに起動する Skill"
CLASS_OF = {"必須": "R", "条件付き": "C", "対象外": "-"}


def _table(heading: str) -> tuple[list[str], list[list[str]]]:
    lines = SKILL.read_text(encoding="utf-8").splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        raise AssertionError(f"見出しが見つからない: {heading}")
    header: list[str] = []
    rows: list[list[str]] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if not stripped.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not header:
            header = cells
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    assert header and rows, f"表を読み取れない: {heading}"
    return header, rows


def classify(cell: str) -> str:
    """工程表のセル 1 つを分類する。"""
    body = cell.strip()
    if body in ("—", "-", ""):
        return "-"
    if body == "任意" or "（" in body:
        return "C"
    return "R"


def derived() -> tuple[list[str], list[list[str]]]:
    header, rows = _table(WORKFLOW_TABLE_HEADING)
    modes = [c.strip("`") for c in header[1:]]
    matrix = [[row[0], *[classify(c) for c in row[1:]]] for row in rows]
    return modes, matrix


def declared() -> tuple[list[str], list[list[str]]]:
    body = LIB.read_text(encoding="utf-8")
    found_modes = re.search(r"WF_MODES=\$'([^']*)'", body)
    assert found_modes, f"モードの一覧を読み取れない: {LIB}"
    modes = found_modes.group(1).split("\\t")
    found = re.search(r"WF_STAGE_MATRIX=\$'(.*?)'\n", body, re.DOTALL)
    assert found, f"分類表を読み取れない: {LIB}"
    matrix = [line.split("\\t") for line in found.group(1).split("\n") if line]
    return modes, matrix


def test_the_modes_match_the_workflow_table() -> None:
    assert declared()[0] == derived()[0]


def test_the_matrix_matches_the_workflow_table() -> None:
    assert declared()[1] == derived()[1]


def test_the_matrix_covers_every_stage_in_order() -> None:
    """並びまで見る。工程の順序が報告の並びになる。"""
    assert [row[0] for row in declared()[1]] == [row[0] for row in derived()[1]]


def test_an_unreadable_table_fails() -> None:
    """表を見つけられないことを、素通りさせない。"""
    with pytest.raises(AssertionError):
        _table("## 存在しない見出し")


def test_the_classification_rule() -> None:
    assert classify("—") == "-"
    assert classify("任意") == "C"
    assert classify("`plan-to-spec`（仕様が変わった場合）") == "C"
    assert classify("`quality-gates`") == "R"
    assert classify("直接編集") == "R"
