"""工程の並びが 4 か所で一致していることを固定する（#231）。

`development-workflow` の工程表が判定の基準を持つ唯一の場所である。対応表も盤面の値の
一覧も、その表から作られる。片方だけが更新されると、工程を盤面へ記録できなくなる。
実際に「ドキュメントレビュー」が値の一覧から抜けており、その工程にいることを記録できなかった。

突き合わせる先は 4 つある。

| 突き合わせる先 | 何を読むか |
| --- | --- |
| 工程表 | `SKILL.md` の「モードごとに起動する Skill」の表の 1 列目 |
| 対応表の工程の列 | `references/projects-tracking.md` の「工程と値の対応」の表の 1 列目 |
| 対応表の値の列 | 同じ表の 3 列目 |
| 値の一覧 | `plugins/ndf/scripts/lib/projects-common.sh` の並び |

**並びまで見る。** 盤面の単一選択は並びを持ち、その並びが工程の順序を表す。順序が
入れ替わっても集合は一致するため、集合だけでは食い違いを拾えない。

読み取れないこと自体も失敗として扱う。素通りさせると、表の書き方を変えるだけでこの検査を
無効にできる。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL = SKILL_DIR / "SKILL.md"
TRACKING = SKILL_DIR / "references/projects-tracking.md"
COMMON = SKILL_DIR.parents[1] / "scripts/lib/projects-common.sh"

WORKFLOW_TABLE_HEADING = "## モードごとに起動する Skill"
TRACKING_TABLE_HEADING = "## 工程と値の対応"


def _table_rows(body: str, heading: str) -> list[list[str]]:
    """見出しの直後に来る表の本文の行を、セルの並びとして返す。"""
    lines = body.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        raise AssertionError(f"見出しが見つからない: {heading}")
    rows: list[list[str]] = []
    seen_header = False
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if not stripped.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not seen_header:
            seen_header = True
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    assert rows, f"表を読み取れない: {heading}"
    return rows


def workflow_stages() -> list[str]:
    return [row[0] for row in _table_rows(SKILL.read_text(encoding="utf-8"), WORKFLOW_TABLE_HEADING)]


def tracking_rows() -> list[list[str]]:
    return _table_rows(TRACKING.read_text(encoding="utf-8"), TRACKING_TABLE_HEADING)


def declared_stages() -> list[str]:
    body = COMMON.read_text(encoding="utf-8")
    found = re.search(r"PJ_STAGES=\$'([^']*)'", body)
    assert found, f"値の一覧を読み取れない: {COMMON}"
    return found.group(1).split("\\n")


def test_the_workflow_table_is_readable() -> None:
    assert len(workflow_stages()) >= 10


def test_the_tracking_table_lists_the_same_stages_in_the_same_order() -> None:
    assert [row[0] for row in tracking_rows()] == workflow_stages()


def test_the_tracking_values_match_the_stage_names() -> None:
    """対応表の値の列は、工程表の行名をそのまま書く。"""
    assert [row[2].strip("`") for row in tracking_rows()] == workflow_stages()


def test_the_declared_values_match_the_workflow_table() -> None:
    assert declared_stages() == workflow_stages()


def test_the_design_review_stage_is_declared() -> None:
    """ドキュメントレビューの工程を盤面へ記録できること（#231 そのもの）。"""
    assert "ドキュメントレビュー" in declared_stages()


@pytest.mark.parametrize("heading", [WORKFLOW_TABLE_HEADING, TRACKING_TABLE_HEADING])
def test_an_unreadable_table_fails(heading: str) -> None:
    """表を見つけられないことを、素通りさせない。"""
    with pytest.raises(AssertionError):
        _table_rows("# 見出しのない文書\n", heading)


def test_a_missing_value_is_detected() -> None:
    stages = declared_stages()
    assert stages[:-1] != workflow_stages()


def test_a_reordered_list_is_detected() -> None:
    stages = declared_stages()
    swapped = [stages[1], stages[0], *stages[2:]]
    assert swapped != workflow_stages()
