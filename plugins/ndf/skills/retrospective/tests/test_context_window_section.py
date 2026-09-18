"""振り返りに context window の集計が載ること（#550 の AC33・AC34）。

固定するのは、観点の表に `context window` の行があることと、記録の雛形が確定仕様
（`docs/specifications/ndf-context-window-metrics.md`「振り返りの記録へ貼る表」）の 3 つの表を
持つことである。**雛形へ載せてよい値は AC30 が許す列だけで、パス・本文・`agent_id` は
載せない**（投稿されるため）。
"""
from __future__ import annotations

import re

from retrospective_helpers import SKILL, read

SECTION_HEADING = "## context window の大きさ"

SUMMARY_HEADER = (
    "| 層 | 持ち場 | モデル | 件数 | 固定費の中央値 | 実作業の中央値 "
    "| 実作業 < 固定費 | 最大充填の最大 | 印 |"
)
LAYER_TOTALS_HEADER = "| 層 | 件数 | 固定費の合計 | 実作業の合計 | 総消費 |"
ROLE_USAGE_HEADER = (
    "| 持ち場 | supervisor | supervisor の実作業 | worker の件数 "
    "| supervisor と worker の固定費の合計 | 印 |"
)


def template() -> str:
    """記録の雛形（`## 振り返り` で始まる Markdown のコードブロック）を返す。"""
    text = read(SKILL)
    blocks = re.findall(r"```markdown\n(.*?)```", text, re.DOTALL)
    found = [b for b in blocks if SECTION_HEADING in b]
    assert found, "記録の雛形に context window の節が無い"
    return found[0]


# ---------- AC33: 観点と雛形 ----------

def test_the_second_step_lists_the_context_window_as_a_viewpoint() -> None:
    text = read(SKILL)
    start = text.index("### 2. 観点ごとに事実を集める")
    end = text.index("### 3. 次に変えることを決める")
    rows = [ln for ln in text[start:end].splitlines() if ln.startswith("| ")]
    assert any("context window" in ln for ln in rows), rows


def test_the_template_holds_the_three_tables() -> None:
    body = template()
    for header in (SUMMARY_HEADER, LAYER_TOTALS_HEADER, ROLE_USAGE_HEADER):
        assert header in body, header


def test_the_template_asks_for_the_three_marks() -> None:
    body = template()
    for mark in ("束ねる候補", "割る候補", "worker を使いすぎ"):
        assert mark in body, mark


def test_the_values_come_from_the_measuring_command() -> None:
    text = read(SKILL)
    assert "--agents" in text and "--session" in text
    assert "/ndf:skill-stats" in text or "skill-stats" in text


# ---------- AC34: 投稿してよい値だけを持つ ----------

def test_the_template_carries_no_identifier_no_path_and_no_body() -> None:
    body = template()
    for forbidden in ("agent_id", "/tmp/", "~/.claude", "プロンプト"):
        assert forbidden not in body, forbidden
