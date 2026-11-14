"""MVV 判定の材料: 分岐の終了コード（#1502）・要求文書の検査と PR の本文（#2853）・レッドライン（#3017）。"""

from __future__ import annotations

from ndf.domains.records import mvv_material_decide as mm
from ndf.progress.gate.checks import Changed, MvvFacts, check_claimed_sections

BASE = MvvFacts(status="approved", current="h", approved="h", referenced="h", mode="mvv", boundary_paths=(".ndf/",))


def test_branch_exit_is_not_a_failure_and_a_recheck_replaces_it() -> None:
    steps = [
        {"name": "requirements-check", "exit": 2, "on_fail": "requirements-fix", "output": "不足 1 件"},
        {"name": "requirements-fix", "exit": 0},
        {"name": "requirements-recheck", "exit": 0, "rechecks": "requirements-check", "output": "不足 0 件"},
        {"name": "unit", "exit": 1},
    ]
    rows = mm.test_rows(steps)
    assert [(r.name, r.exit, r.status) for r in rows] == [
        ("requirements-recheck", 0, mm.PASSED),
        ("requirements-fix", 0, mm.PASSED),
        ("unit", 1, mm.FAILED),
    ]
    assert mm.test_rows(steps[:1])[0].status == mm.BRANCHED


def test_test_section_carries_exit_codes_and_the_tail_of_outputs() -> None:
    out = "\n".join(f"行 {i}" for i in range(30))
    text = mm.test_section(mm.test_rows([{"name": "requirements-check", "exit": 2, "output": out}]))
    assert "| requirements-check | 2 | 落ちた |" in text
    assert "行 29" in text and "行 9\n" not in text


def test_facts_from_pr_fills_body_claimed_and_changed() -> None:
    body = "## 概要\n\nx\n\n## 出し直しの指摘への対応\n\n| 指摘 | 対応 |\n| --- | --- |\n| a | 「テスト」の節に記載した |\n| b | 「テスト」の節に追記した |\n"
    facts = mm.facts_from_pr(BASE, body, [Changed("a.py")])
    assert facts.claimed == ("テスト",) and facts.body == body and facts.changed == (Changed("a.py"),)
    assert facts.boundary_paths == (".ndf/",)
    assert check_claimed_sections(facts) is not None
    assert mm.claimed_sections(None) == ()


def test_prompt_lists_every_redline_before_the_materials() -> None:
    text = mm.prompt({"C4": "戻せない操作", "P1": "取得元の規則"}, {"設計": "既定で強制 push する"})
    assert text.index("- C4: 戻せない操作") < text.index("- P1: 取得元の規則") < text.index("## 設計")
