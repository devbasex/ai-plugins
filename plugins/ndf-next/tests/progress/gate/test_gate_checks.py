"""MVV 判定の前の機械の検査 5 つと、承認ゲートを省けるかの判定（#3295。3364-3・#3017・#2950）。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from ndf.progress import gate

SHA = "a" * 64
PASSING = gate.MvvFacts(
    status="approved",
    current=SHA,
    approved=SHA,
    referenced=SHA,
    mode="standard",
    changed=(gate.Changed("src/app.py"),),
    boundary_paths=("infra/**",),
)
FOLLOW = {"verdict": "follow", "reasons": ["Value 1 に従う"], "boundary": []}


def test_facts_that_pass_every_check_have_no_refusals():
    assert gate.refusals(PASSING) == []


@pytest.mark.parametrize(
    ("check", "changes", "word"),
    [
        (gate.check_mvv_hash, {"status": "unapproved"}, "承認済みでない"),
        (gate.check_mvv_hash, {"current": "b" * 64}, "一致しない"),
        (gate.check_approval_record, {"referenced": None}, "参照が無い"),
        (gate.check_approval_record, {"referenced": "c" * 64}, "一致しない"),
        (gate.check_mode, {"mode": "operation"}, "省かない"),
        (gate.check_mode, {"mode": "documentation"}, "省かない"),
        (gate.check_boundary, {"changed": (gate.Changed("infra/main.tf"),)}, "infra/main.tf"),
        (gate.check_boundary, {"changed": (gate.Changed("src/x.tf", previous="infra/x.tf", renamed=True),)}, "infra/x.tf → src/x.tf"),
        (gate.check_boundary, {"changed": (gate.Changed("src/y.tf", renamed=True),)}, "前のパスが分からない"),
        (gate.check_mvv_decl, {"changed": (gate.Changed(".ndf/mvv.md"),)}, "C7"),
        (gate.check_mvv_decl, {"changed": (gate.Changed("docs/mvv.json", previous=".ndf/mvv.json"),)}, "C7"),
    ],
)
def test_each_check_refuses_its_own_fact(check, changes, word):
    facts = replace(PASSING, **changes)
    why = check(facts)
    assert why is not None and word in why
    assert check(PASSING) is None
    assert why in gate.refusals(facts)


def test_the_gate_is_skipped_only_for_a_well_formed_follow_without_a_boundary():
    assert gate.may_skip(PASSING, FOLLOW) == (True, [])
    assert gate.may_skip(PASSING, {**FOLLOW, "verdict": "unknown"})[0] is False
    assert gate.may_skip(PASSING, {**FOLLOW, "boundary": ["C4 の削除"]}) == (False, ["レッドラインに当たる: C4 の削除"])
    for broken in ({"verdict": "follow", "reasons": ["r"]}, {**FOLLOW, "boundary": [""]}, {**FOLLOW, "boundary": {}}, "follow"):
        assert gate.may_skip(PASSING, broken) == (False, ["MVV 判定の応答が契約の形でない"])


def test_a_follow_with_an_empty_boundary_does_not_skip_a_change_on_the_boundary_paths():
    # #3017: レッドラインに当たる変更を、boundary が空の「従う」の判定で通さない
    hit = replace(PASSING, changed=(gate.Changed("infra/prod.tf"),))
    skip, why = gate.may_skip(hit, FOLLOW)
    assert skip is False and why == ["レッドラインのパスに当たる: infra/prod.tf"]


def test_the_note_shows_the_boundary_and_why_the_gate_was_not_skipped():
    # #2950: 判定の記録にレッドラインの中身が載り、承認する人が PR から読める
    text = gate.note("design", {**FOLLOW, "boundary": ["C1 の秘密に触れる"]}, ["レッドラインに当たる: C1 の秘密に触れる"])
    assert "### レッドライン\n\n- C1 の秘密に触れる" in text and "### 承認ゲートを省かない理由" in text
