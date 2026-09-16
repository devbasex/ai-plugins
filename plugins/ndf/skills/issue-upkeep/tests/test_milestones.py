"""`issue-upkeep` の参照 `milestones.md`（着手の時期の決め方）の契約。

重要度 3 区分の言い換えは `no-work.md` が正本で、milestones.md は写さず参照する。その
配線もここで確かめる。
"""
from __future__ import annotations

import pytest

from _helpers import MILESTONES, NO_WORK, flat, section, table


def test_milestones_reflect_only_the_earlier_direction() -> None:
    """早める方向だけを自動で反映する。"""
    body = MILESTONES.read_text(encoding="utf-8")
    assert "**早める方向だけを自動で反映する。**" in body
    assert "**要判断**" in body


def test_milestones_are_not_created_for_a_single_issue() -> None:
    """1 件しか残らないときは作らない。"""
    assert "**1 件しか残らないときは作らない。**" in MILESTONES.read_text(encoding="utf-8")


def test_priority_paraphrase_lives_only_in_no_work() -> None:
    """重要度 3 区分の言い換えは no-work.md が正本で、milestones.md は写さず参照する。

    高い＝実害・安全機構の欠落 / 中くらい＝保守性・設計一貫性 / 低い＝余力があれば の
    言い換えを 2 つの表へ逐語で持つと、片方だけ直すと食い違う。
    """
    no_work = NO_WORK.read_text(encoding="utf-8")
    milestones = MILESTONES.read_text(encoding="utf-8")
    # 正本の no-work.md は 3 区分の言い換えを持つ
    for gloss in ("実害・安全機構の欠落", "保守性・設計一貫性", "余力があれば"):
        assert gloss in no_work, gloss
    # milestones.md は言い換えを写さず、no-work.md を指す
    for gloss in ("実害・安全機構の欠落", "保守性・設計一貫性", "余力があれば"):
        assert gloss not in milestones, gloss
    assert "[no-work.md](no-work.md)" in milestones
    assert "重要度が、抱える費用の目安になる" in milestones


def test_priority_tiers_share_the_same_order() -> None:
    """no-work.md と milestones.md が高い→中くらい→低いの同じ順で並ぶ。"""
    def tiers(text: str, header: str) -> list[str]:
        rows = table(text, header)
        # 太字の印と括弧内の言い換えを外して区分名だけにする
        return [row[0].replace("*", "").split("（", 1)[0].strip() for row in rows]

    no_work_order = tiers(NO_WORK.read_text(encoding="utf-8"),
                          "| 重要度 | 抱える費用 | 候補になるか |")
    milestones_order = tiers(
        section(MILESTONES.read_text(encoding="utf-8"),
                "## 設定されていない課題を割り当てる"),
        "| 重要度 | 入れる先 | 主題の合うものが無いとき |")
    assert no_work_order[:3] == ["高い", "中くらい", "低い"], no_work_order
    assert milestones_order == ["高い", "中くらい", "低い"], milestones_order


@pytest.mark.parametrize(("priority", "when_no_matching_subject"), [
    ("高い", "直近のマイルストーンへ、主題によらず入れる"),
    ("中くらい", "末尾に新しく作る（下記）"),
    ("低い", "未設定のまま残す"),
])
def test_unassigned_issues_branch_by_priority_when_no_subject_matches(
        priority: str, when_no_matching_subject: str) -> None:
    """主題の合うものが無いときの割り当て先を、重要度の 3 区分ごとに固定する。"""
    part = section(MILESTONES.read_text(encoding="utf-8"),
                   "## 設定されていない課題を割り当てる")
    rows = table(part, "| 重要度 | 入れる先 | 主題の合うものが無いとき |")
    normalized = [[cell.replace("**", "") for cell in row] for row in rows]
    row = next(row for row in normalized if row[0] == priority)
    assert row[2] == when_no_matching_subject


def test_unassigned_issue_with_multiple_matching_subjects_needs_judgement() -> None:
    """主題が複数に当てはまるときは、自動で割り当てず要判断へ倒す。"""
    part = flat(section(MILESTONES.read_text(encoding="utf-8"),
                        "## 設定されていない課題を割り当てる"))
    assert "主題が複数に当てはまるときは**要判断**へ倒す" in part


def test_milestone_reorder_branches_by_dependency_and_sequence() -> None:
    """順序修正の判断を、依存の記述の有無と連番順序との食い違いで固定する。

    依存の記述が無いものは先に返し（早く出したい判断が入る）、記述があるものだけを
    連番の順序と照らして、食い違えば直し、食い違わなければ何もしない。
    """
    part = section(MILESTONES.read_text(encoding="utf-8"), "## 順序を直すとき")
    assert "依存の記述が無いものの順序は、まず対象から外す" in flat(part)
    rows = table(part, "| 依存の記述 | 連番の順序との関係 | 対応 |")
    outcomes = {(row[0], row[1]): row[2] for row in rows}
    assert "返す" in outcomes[("無い", "（見ない）")]
    ある_食い違う = next(v for (dep, seq), v in outcomes.items()
                       if dep == "ある" and "食い違う" in seq)
    assert "直す" in ある_食い違う
    ある_一致 = next(v for (dep, seq), v in outcomes.items()
                   if dep == "ある" and seq == "食い違わない")
    assert "何もしない" in ある_一致


def test_milestones_use_one_word_for_the_timing() -> None:
    """着手の時期は「マイルストーン」で呼び、「まとまり」を使わない。"""
    assert "まとまり" not in MILESTONES.read_text(encoding="utf-8")


def test_milestones_pick_the_nearest_by_sequence() -> None:
    """直近と順序は名前の先頭の連番で決める。open のマイルストーンは版数を持たない。"""
    text = flat(MILESTONES.read_text(encoding="utf-8"))
    assert "版数が最も小さい" not in text
    assert "版数の順序" not in text
    assert "連番が最も小さい" in text
    # 順序修正の判断は「順序を直すとき」の対応表で連番の順序と照らす（R3-005 で平坦化）
    assert "連番の順序と照らして判断する" in text
    assert "後の連番" in text
    assert "`<2 桁の連番> <主題>`" in text
    assert "説明が連番と別の着手の順序を書いているときは、説明を採る" in text
