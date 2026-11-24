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
        (gate.check_mvv_decl, {"changed": (gate.Changed(".ndf/mvv.json"),)}, "C7"),
        (gate.check_mvv_decl, {"changed": (gate.Changed("docs/mvv.json", previous=".ndf/mvv.json"),)}, "C7"),
        # 検査 7: 保留の文（理由・本文）は返さず、件数の固定の文だけを返す
        (gate.check_review_holds, {"review_holds": ("見送り X", "本文 Y")}, "レビューの保留が 2 件ある（内容は保留の欄）"),
    ],
)
def test_each_check_refuses_its_own_fact(check, changes, word):
    facts = replace(PASSING, **changes)
    why = check(facts)
    assert why is not None and word in why and "見送り X" not in why
    # すべての検査を通る事実には、省かない理由が無い
    assert check(PASSING) is None and gate.refusals(PASSING) == []
    assert why in gate.refusals(facts)


def test_the_gate_is_skipped_only_for_a_well_formed_follow_without_a_boundary():
    assert gate.may_skip(PASSING, FOLLOW) == (True, [])
    assert gate.may_skip(PASSING, {**FOLLOW, "verdict": "unknown"})[0] is False
    assert gate.may_skip(PASSING, {**FOLLOW, "boundary": ["C4 の削除"]}) == (False, ["レッドラインに当たる: C4 の削除"])
    for broken in ({"verdict": "follow", "reasons": ["r"]}, {**FOLLOW, "boundary": [""]}, {**FOLLOW, "boundary": {}}, "follow"):
        assert gate.may_skip(PASSING, broken) == (False, ["MVV 判定の応答がコントラクトの形でない"])
    # #3017: レッドラインに当たる変更を、boundary が空の「従う」の判定で通さない
    hit = replace(PASSING, changed=(gate.Changed("infra/prod.tf"),))
    skip, why = gate.may_skip(hit, FOLLOW)
    assert skip is False and why == ["レッドラインのパスに当たる: infra/prod.tf"]


def test_the_note_shows_the_boundary_and_why_the_gate_was_not_skipped():
    # #2950: 判定の記録にレッドラインの中身が載り、承認する人が PR から読める
    text = gate.note("design", {**FOLLOW, "boundary": ["C1 の秘密に触れる"]}, ["レッドラインに当たる: C1 の秘密に触れる"])
    assert "### レッドライン\n\n- C1 の秘密に触れる" in text and "### 承認ゲートを省かない理由" in text
    # I-015 の現状固定: 空の箇条書きは「- （無し）」の 1 行。省かない理由が無ければセクションごと載せない
    text = gate.note("design", {"verdict": "follow"}, [])
    assert text == (
        "## MVV 判定（設計 Pull Request のマージ）\n\n- 判定: follow\n\n### 理由\n\n- （無し）\n\n### レッドライン\n\n- （無し）\n"
    )
    assert gate.note("release", None, []).startswith("## MVV 判定（本番系へ届く操作）\n\n- 判定: 無し\n\n### 理由\n\n- （無し）\n")
    # レビューの保留は、あるときだけ載せる
    assert "### レビューの保留\n\n- PR #5: 直っていないレッドライン" in gate.checks.note(
        "design", FOLLOW, [], ["PR #5: 直っていないレッドライン"]
    )


# --- #3959 Task 4: 検査 1 だけが判定を止め、区分ごとの検査が省略だけを止める ---


def test_only_the_first_check_stops_the_verdict():
    assert gate.checks.judgeable(PASSING) is None
    assert "承認済みでない" in str(gate.checks.judgeable(replace(PASSING, status="mismatch", current=None, approved=None)))
    # 検査 2 が外れても判定は打てる（省くかだけが止まる）
    assert gate.checks.judgeable(replace(PASSING, referenced=None)) is None


@pytest.mark.parametrize("kind", ["design", "release"])
def test_each_gate_has_the_checks_in_order(kind):
    assert gate.checks.CHECKS_BY_GATE[kind] == gate.checks.CHECKS and gate.checks.CHECKS[0] is gate.checks.check_mvv_hash
    hit = replace(PASSING, referenced=None)
    assert gate.refusals(hit, kind) == ["スプリントの状態に承認済み MVV の参照が無い"]
    assert gate.may_skip(hit, FOLLOW, kind) == (False, ["スプリントの状態に承認済み MVV の参照が無い"])
