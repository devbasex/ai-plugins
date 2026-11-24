"""組み直す文書の選び方（`ndf.domains.docrebuild.select_decide`）のテスト（#4337 の条件 6〜9・I1）。"""

from __future__ import annotations

from typing import Mapping, Optional

import pytest

from ndf.domains.docrebuild import select_decide as sd
from ndf.domains.docrebuild.select_decide import Inspection, Target

DESIGN = "issues/issue-4337-design.md"
REQ = "issues/issue-4337-requirements.md"
PLAN = "issues/issue-4337-plan.md"
OMITTED_PLAN = "# 計画\n\n省いた文書: 設計書（課題 1・公開インタフェース 変わらない）\n\n## 修正対象\n"


@pytest.mark.parametrize(
    ("path", "issues", "kinds", "kind"),
    [
        (DESIGN, {4337}, sd.KINDS, "design"),
        ("issues/issue-7-8-design.md", {8}, sd.KINDS, "design"),
        ("docs/guide.md", {4337}, sd.KINDS, "manuscript"),
        ("README.md", {4337}, ("manuscript",), "manuscript"),
        (DESIGN, {433}, sd.KINDS, None),
        ("issues/issue-1-design.md", {4337}, sd.KINDS, None),
        (REQ, {4337}, sd.KINDS, None),
        (PLAN, {4337}, sd.KINDS, None),
        ("docs/guide.md", {4337}, ("design", "plan"), None),
        (DESIGN, {4337}, ("plan",), None),
        ("src/a.py", {4337}, sd.KINDS, None),
    ],
)
def test_kind_of(path: str, issues: set[int], kinds: tuple[str, ...], kind: Optional[str]) -> None:
    assert sd.kind_of(path, issues, kinds) == kind


@pytest.mark.parametrize(
    ("text", "omits"),
    [
        (OMITTED_PLAN, True),
        ("# 計画\n省いた文書: 設計書\n## 修正対象\n", True),
        ("省いた文書: 設計書\n", True),
        ("# 計画\n省いた文書: 設計書（a）\n##\n", True),
        ("# 計画\n## 修正対象\n省いた文書: 設計書（課題 1）\n", False),
        ("# 計画\n省いた文書: 実行計画（課題 1・PR 1）\n", False),
        ("# 計画\n省いた文書: 詳細設計書（別ファイル）（a）\n", False),
        ("# 計画\n省いた文書: 設計書類\n", False),
        ("# 計画\n 省いた文書: 設計書\n", False),
    ],
)
def test_omits_design(text: str, omits: bool) -> None:
    assert sd.omits_design(text) is omits


def test_plan_paths_follow_issue_order_without_duplicates() -> None:
    assert sd.plan_paths([2, 1, 2]) == ("issues/issue-2-plan.md", "issues/issue-1-plan.md")


def test_candidates_for_design_stage_are_the_design_only() -> None:
    """条件 6: 工程「設計」の差分に設計書・要求仕様のコピー・実装計画があっても、候補は設計書だけ。"""
    got = sd.candidates([REQ, DESIGN, PLAN, "docs/a.md"], [4337], ("design",))
    assert got == (Target(DESIGN, "design"),)


def test_candidates_for_documentation_include_manuscripts() -> None:
    """条件 7: `documentation` の差分が `issues/` の外の原稿だけなら、候補はその原稿（と番号から決まる実装計画）。"""
    got = sd.candidates(["docs/a.md", "src/b.py"], [4337], ("design", "plan", "manuscript"))
    assert got == (Target("docs/a.md", "manuscript"), Target(PLAN, "plan"))


def test_candidates_for_standard_rebuild_leave_out_docs() -> None:
    """条件 8: `standard` では `docs/` の Markdown を選ばない。"""
    got = sd.candidates([DESIGN, REQ, PLAN, "docs/a.md"], [4337], ("design", "plan"))
    assert got == (Target(DESIGN, "design"), Target(PLAN, "plan"))


def test_candidates_add_plans_even_with_an_empty_diff() -> None:
    """決定 9: 実装計画は差分によらず課題の番号から入る。"""
    assert sd.candidates([], [4337], ("plan",)) == (Target(PLAN, "plan"),)


def test_candidates_keep_one_entry_per_path() -> None:
    got = sd.candidates([DESIGN, DESIGN], [4337], ("design",))
    assert got == (Target(DESIGN, "design"),)


def _seen(
    regular: set[str],
    present: Optional[set[str]] = None,
    excluded: Optional[Mapping[str, str]] = None,
    plans: Optional[Mapping[str, str]] = None,
    unreadable: Optional[Mapping[str, str]] = None,
) -> Inspection:
    return Inspection(
        frozenset(regular),
        frozenset(present if present is not None else regular),
        dict(excluded or {}),
        dict(plans or {}),
        dict(unreadable or {}),
    )


def test_select_keeps_a_plan_that_omits_the_design() -> None:
    """条件 9: 設計書を省いた行を持つ実装計画が対象に残る。"""
    found = (Target(PLAN, "plan"),)
    got = sd.select(found, _seen({PLAN}, plans={PLAN: OMITTED_PLAN}))
    assert got == sd.Selection((Target(PLAN, "plan"),), ())


def test_select_drops_a_plan_without_the_line_silently() -> None:
    found = (Target(DESIGN, "design"), Target(PLAN, "plan"))
    got = sd.select(found, _seen({DESIGN, PLAN}, plans={PLAN: "# 計画\n## 修正対象\n"}))
    assert got == sd.Selection((Target(DESIGN, "design"),), ())


def test_select_drops_an_absent_plan_silently() -> None:
    found = (Target(PLAN, "plan"),)
    got = sd.select(found, _seen(set(), excluded={PLAN: "作業場所に無い"}))
    assert got == sd.Selection((), ())


def test_select_reports_an_unreadable_plan() -> None:
    found = (Target(PLAN, "plan"),)
    got = sd.select(found, _seen({PLAN}, unreadable={PLAN: "UTF-8 で読めない"}))
    assert got == sd.Selection((), (f"{PLAN}: UTF-8 で読めない",))


def test_select_reports_links_and_missing_diff_paths() -> None:
    """I1: 差分の `.md` のリンクと作業場所に無いもの、リンクの実装計画は外して `skipped` に出す。"""
    found = (Target(DESIGN, "design"), Target("docs/gone.md", "manuscript"), Target(PLAN, "plan"))
    seen = _seen(
        set(),
        present={DESIGN, PLAN},
        excluded={DESIGN: "通常ファイルでない", "docs/gone.md": "作業場所に無い", PLAN: "通常ファイルでない"},
    )
    got = sd.select(found, seen)
    assert got.targets == ()
    assert got.skipped == (f"{DESIGN}: 通常ファイルでない", "docs/gone.md: 作業場所に無い", f"{PLAN}: 通常ファイルでない")
