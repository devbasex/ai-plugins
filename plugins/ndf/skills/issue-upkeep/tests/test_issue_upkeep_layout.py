"""`issue-upkeep` の構成と配線（#331）。

**判定の値は 3 箇所（手順の表・自動で反映してよい変更の表・報告）で同じ並びを持つ。**
片方だけが増えると、反映の側が知らない判定を受け取る。
"""
from __future__ import annotations

import pathlib
import re

import pytest

SKILL_DIR = pathlib.Path(__file__).resolve().parents[1]
SKILLS = SKILL_DIR.parent
ROOT = SKILLS.parents[2]

SKILL = SKILL_DIR / "SKILL.md"
NO_WORK = SKILL_DIR / "references" / "no-work.md"
MILESTONES = SKILL_DIR / "references" / "milestones.md"

# 手順の表が持つ 7 つの判定。**この並びが基準である。**
VERDICTS = [
    "そのまま", "追記が要る", "書き直しが要る", "閉じてよい",
    "やらない", "重複", "要判断",
]


def test_the_references_exist() -> None:
    """「やらない」とマイルストーンは参照へ分ける。

    段 1 と段 2A では、どちらの判断も起きない。手順の本体から外すと、対象を選んで調べる
    段では読み込まれない。
    """
    assert NO_WORK.is_file()
    assert MILESTONES.is_file()


def test_the_verdict_table_lists_seven_in_order() -> None:
    """判定は 7 つに限られ、手順の表がその並びを持つ。"""
    body = SKILL.read_text(encoding="utf-8")
    table = body[body.index("| 判定 | 選ぶ条件 | 段 3 での対応 |"):]
    table = table[:table.index("\n\n")]
    found = [re.match(r"\| \*?\*?([^|*]+?)\*?\*?(?: \|| ).*", line).group(1).strip()
             for line in table.split("\n")[2:] if line.startswith("|")]
    assert found == VERDICTS, found


def test_no_work_is_reachable_from_the_verdict_table() -> None:
    """「やらない」の行から参照を指す。判断の基準はそちらにある。"""
    body = SKILL.read_text(encoding="utf-8")
    row = next(line for line in body.split("\n")
               if line.startswith("| **やらない**"))
    assert "references/no-work.md" in row


def test_no_work_has_two_necessary_conditions() -> None:
    """2 つの必要条件と、欠けたときの行き先が表で示されている。"""
    body = NO_WORK.read_text(encoding="utf-8")
    assert "## 2 つの必要条件" in body
    assert "欠けたときの行き先" in body
    assert "抱える費用が、直す費用を下回る" in body
    assert "同じ原因の他の課題へ寄せられない" in body


def test_no_work_shows_both_kinds_of_reactivation_condition() -> None:
    """再燃の条件は観測できる形で書く。観測できない書き方の例も示す。"""
    body = NO_WORK.read_text(encoding="utf-8")
    assert "**観測できる**" in body
    assert "観測できない" in body


def test_closing_and_not_planned_are_separate_verdicts() -> None:
    """「やらない」と「閉じてよい」を別の判定として区別する。"""
    body = SKILL.read_text(encoding="utf-8")
    assert "**「閉じてよい」とは\n別である。**" in NO_WORK.read_text(encoding="utf-8")
    assert "| 閉じてよい |" in body
    assert "| **やらない** |" in body


def test_milestones_reflect_only_the_earlier_direction() -> None:
    """早める方向だけを自動で反映する。"""
    body = MILESTONES.read_text(encoding="utf-8")
    assert "**早める方向だけを自動で反映する。**" in body
    assert "**要判断**" in body


def test_milestones_are_not_created_for_a_single_issue() -> None:
    """1 件しか残らないときは作らない。"""
    assert "**1 件しか残らないときは作らない。**" in MILESTONES.read_text(encoding="utf-8")


@pytest.mark.parametrize("caller,marker", [
    ("retrospective", "issue-upkeep"),
    ("release", "issue-upkeep"),
    ("out-of-scope", "issue-upkeep"),
])
def test_the_callers_point_here(caller: str, marker: str) -> None:
    """振り返り・配布・起票の 3 つがこの Skill を指す。"""
    body = (SKILLS / caller / "SKILL.md").read_text(encoding="utf-8")
    assert marker in body, f"{caller} が {marker} を指していない"


@pytest.mark.parametrize("runtime", ["claude", "codex", "kiro", "agy"])
def test_the_skill_is_distributed(runtime: str) -> None:
    """4 つの manifest すべてに載る。"""
    manifest = ROOT / "plugins" / "ndf" / "manifests" / f"{runtime}-skills.txt"
    names = [line.split("#", 1)[0].strip()
             for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert "issue-upkeep" in names


# ---------- 対象の範囲 ----------

def test_the_target_is_not_narrowed_by_who_filed_it() -> None:
    """段 1 の 3 つ目の経路が、起票した主体で絞らないことを明記している。

    絞ると、**未設定のまま溜まる課題を誰も見ないことになる**。起票した側は一覧の全体を
    見る立場に無く、棚卸をする側が自分の分だけを見ると、残りは次の棚卸でも同じ理由で外れる。
    """
    body = SKILL.read_text(encoding="utf-8")
    assert "起票した主体を問わない" in body
    assert "**起票した主体で絞らない。**" in body


def test_the_target_query_does_not_filter_by_author() -> None:
    """未設定の課題を拾う例が、投稿者で絞っていないこと。"""
    body = SKILL.read_text(encoding="utf-8")
    block = body[body.index("**起票した主体で絞らない。**"):]
    start = block.index("```bash")
    block = block[start:block.index("```", start + len("```bash")) + 3]
    assert "--author" not in block, "投稿者で絞る例になっている"
    assert "select(.milestone == null)" in block
