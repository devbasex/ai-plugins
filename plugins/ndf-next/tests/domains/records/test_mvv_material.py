"""MVV 判定の材料: 分岐の終了コード（#1502）・要求文書の検査と PR の本文（#2853）・レッドライン（#3017）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from ndf.domains.records import mvv
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


# 現状固定（I-004 の前）: base の事実を全項目そのまま写し、changed・body・claimed だけを PR の値で置き換える
_FULL = MvvFacts(
    status="mismatch",
    current="c",
    approved="a",
    referenced="r",
    mode="m",
    changed=(Changed("old.py"),),
    boundary_paths=("x/", "y/"),
    body="古い本文",
    claimed=("古い節",),
)


def test_facts_from_pr_keeps_every_base_field_and_replaces_only_the_pr_fields() -> None:
    got = mm.facts_from_pr(_FULL, None, iter([Changed("n.py", "o.py", True)]))
    assert got == MvvFacts(
        status="mismatch",
        current="c",
        approved="a",
        referenced="r",
        mode="m",
        changed=(Changed("n.py", "o.py", True),),
        boundary_paths=("x/", "y/"),
        body=None,
        claimed=(),
    )


def test_facts_from_pr_takes_claimed_from_the_new_body_in_first_seen_order() -> None:
    body = "## 出し直しの指摘への対応\n\n| a | 「設計」の節に足した |\n| b | 「テスト」の節に記載した |\n| c | 「設計」の節に追記した |\n"
    got = mm.facts_from_pr(_FULL, body, [])
    assert got == MvvFacts(
        status="mismatch",
        current="c",
        approved="a",
        referenced="r",
        mode="m",
        changed=(),
        boundary_paths=("x/", "y/"),
        body=body,
        claimed=("設計", "テスト"),
    )


def test_prompt_lists_every_redline_before_the_materials() -> None:
    text = mm.prompt({"C4": "戻せない操作", "P1": "取得元の規則"}, {"設計": "既定で強制 push する"})
    assert text.index("- C4: 戻せない操作") < text.index("- P1: 取得元の規則") < text.index("## 設計")


class _Hub:
    repo = "o/r"

    def __init__(self, body: str) -> None:
        self.body = body

    def pull(self, number: int) -> SimpleNamespace:
        return SimpleNamespace(body=self.body)

    def rest(self, method: str, path: str, payload: Any = None, params: Any = None) -> list[dict]:
        assert path == "/repos/o/r/pulls/3/files"
        return [{"filename": "b.py", "previous_filename": "a.py", "status": "renamed"}, {"filename": "c.md", "status": "added"}]


def test_pr_facts_reads_the_body_and_changed_files_of_the_pr() -> None:
    body = "## テスト\n\n通った\n\n## 出し直しの指摘への対応\n\n| a | 「テスト」の節に記載した |\n"
    facts = mvv.pr_facts(_Hub(body), 3, BASE)  # type: ignore[arg-type]
    assert facts.claimed == ("テスト",) and check_claimed_sections(facts) is None
    assert facts.changed == (Changed("b.py", "a.py", True), Changed("c.md", None, False))
