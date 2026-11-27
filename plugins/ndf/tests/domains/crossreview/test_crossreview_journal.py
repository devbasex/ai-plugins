"""指摘のジャーナル（drive.FindingJournal）の規則。外部の起動・git を使わずに確かめる。"""

from __future__ import annotations

import pytest

from ndf.domains.crossreview import criteria_decide, drive, journal as journal_mod, prior_decide, verify_decide
from ndf.domains.records.mvv_decide import REVIEW_FIXED_AT
from ndf.domains.crossreview import finish_decide as fin

IMPACT = "保存が止まり、利用者の入力が消える"


def _row(fid: str, text: str, round_no: int, outcome: str = fin.UNRESOLVED, reason=None, by="codex", section=None) -> dict:
    return {
        "id": fid,
        "key": text.lower(),
        "text": text,
        "section": section,
        "round": round_no,
        "outcome": outcome,
        "reason": reason,
        "by": by,
    }


def _plain(rows) -> list[dict]:
    """行から発生の値（乱数）と直した時刻を外す。新しい行と再発の行は 12 字の発生の値を持ち、修正担当が直したと返した行は
    直した時刻を持つ。"""
    out = []
    for row in rows:
        row = dict(row)
        occurrence = row.pop(journal_mod.OCCURRENCE, None)
        assert occurrence is None or len(occurrence) == 12
        fixed_at = row.pop(REVIEW_FIXED_AT, None)
        assert fixed_at is None or isinstance(fixed_at, str)
        out.append(row)
    return out


def test_enter_numbers_new_findings_per_round_and_ignores_duplicates_and_blanks() -> None:
    journal = drive.FindingJournal()
    raised = [
        {"text": " 一つ目 ", "by": "codex", "section": "a.md#セクション", "impact": IMPACT},
        {"text": "一つ目", "by": "kiro"},
        {"text": "  ", "by": "kiro"},
        {"text": 3, "by": "kiro"},
        {"text": "二つ目", "by": "kiro", "section": " ", "impact": IMPACT},
    ]
    assert journal.enter(1, raised) == frozenset({"一つ目", "二つ目"})
    raised_rows = {"severity": "major", "raised_severity": None, "impact": IMPACT}
    assert all(len(r[journal_mod.OCCURRENCE]) == 12 for r in journal.rows.values())
    assert _plain(journal.rows.values()) == [
        {**_row("r1-1", "一つ目", 1, section="a.md#セクション"), **raised_rows},
        {**_row("r1-2", "二つ目", 1, by="kiro"), **raised_rows},
    ]


def test_enter_skips_deferred_findings_and_reopens_fixed_ones() -> None:
    journal = drive.FindingJournal([_row("r1-1", "直した", 1, fin.FIXED), _row("r1-2", "見送った", 1, fin.SKIPPED, "別で直す")])
    assert journal.enter(2, [{"text": "直した", "by": "kiro"}, {"text": "見送った", "by": "kiro"}]) == frozenset({"直した", "見送った"})
    assert _plain(journal.rows.values()) == [_row("r1-1", "直した", 2), _row("r1-2", "見送った", 1, fin.SKIPPED, "別で直す")]
    # 再発した指摘は新しい発生の値を持ち、見送り済みの行は書き換えない（#4016）
    assert journal_mod.OCCURRENCE in journal.rows["r1-1"] and journal_mod.OCCURRENCE not in journal.rows["r1-2"]
    assert journal.skipped_texts() == ["見送った"]


def test_rows_without_an_id_are_not_taken_in() -> None:
    assert list(drive.FindingJournal([_row("r1-1", "a", 1), {"text": "id が無い"}, "x"]).rows) == ["r1-1"]


def test_pending_lists_open_findings_and_undecided_handed_items() -> None:
    journal = drive.FindingJournal([_row("r1-1", "a", 1), _row("rebuild-1", "b", 1, fin.FIXED), _row("rebuild-2", "c", 1)])
    handed = [fin.Handed("rebuild-1", "b"), fin.Handed("rebuild-2", "c"), fin.Handed("rebuild-3", "d")]
    open_, undecided = journal.pending(handed)
    assert [f["id"] for f in open_] == ["r1-1", "rebuild-2"]
    assert undecided == [fin.Handed("rebuild-2", "c"), fin.Handed("rebuild-3", "d")]


def test_apply_fix_takes_decided_outcomes_and_ignores_unknown_ids() -> None:
    journal = drive.FindingJournal([_row("r1-1", "a", 1), _row("r1-2", "b", 1)])
    items = [
        {"id": "r1-1", "outcome": fin.FIXED, "reason": "  "},
        {"id": "r1-2", "outcome": fin.UNRESOLVED},
        {"id": "nope", "outcome": fin.FIXED},
        {"id": "rebuild-1", "outcome": fin.SKIPPED, "reason": "後で", "defer": "filed"},
        "x",
    ]
    reported = journal.apply_fix(items, [fin.Handed("rebuild-1", "足す")], 2)
    assert reported == [items[0], items[2], items[3]]
    assert len(journal.rows["rebuild-1"][journal_mod.OCCURRENCE]) == 12
    assert _plain(journal.rows.values()) == [
        _row("r1-1", "a", 1, fin.FIXED),
        _row("r1-2", "b", 1),
        {
            "id": "rebuild-1",
            "key": None,
            "text": "足す",
            "section": None,
            "round": 2,
            "outcome": fin.SKIPPED,
            "reason": "後で",
            "by": "rebuild",
            "defer": "filed",
        },
    ]


def test_reopen_and_findings() -> None:
    journal = drive.FindingJournal(
        [_row("r1-1", "a", 1, fin.FIXED), _row("r1-2", "b", 1, fin.SKIPPED, "理由"), {"id": "x", "outcome": "?"}]
    )
    journal.reopen(["r1-1", "nope"])
    assert journal.rows["r1-1"]["outcome"] == fin.UNRESOLVED
    assert journal.findings() == (fin.Finding("r1-1", "a", fin.UNRESOLVED, None), fin.Finding("r1-2", "b", fin.SKIPPED, "理由"))


def test_enter_keeps_the_heaviest_severity_within_one_round() -> None:
    """PR 3944 の r1-5: 同じラウンドで同じ指摘が minor の後に major で返れば major を残し、収束の妨げに数える。"""
    journal = drive.FindingJournal()
    keys = journal.enter(
        1,
        [
            {"text": "A が止まる", "severity": "minor", "by": "codex"},
            {"text": "A が止まる", "severity": "major", "impact": IMPACT, "by": "kiro"},
        ],
    )
    [row] = journal.rows.values()
    assert row["severity"] == "major" and len(keys) == 1
    open_, _ = journal.pending(())
    assert [f["id"] for f in open_] == ["r1-1"]


def test_issue_4014_raised_keeps_the_original_severity_and_the_impact() -> None:
    """#4014: ジャーナルへ入れる 1 件は位置・下げた後の重要度・元の重要度・1 文（あるときだけ、前後の空白を除く）を持つ。"""
    assert journal_mod._raised({"text": "a", "severity": "major", "impact": f" {IMPACT} ", "path": "x.py", "line": 3}) == {
        "path": "x.py",
        "line": 3,
        "severity": "major",
        "raised_severity": "major",
        "impact": IMPACT,
    }
    # severity のキーを持たない指摘も例外にしない
    assert journal_mod._raised({"text": "a", "impact": IMPACT}) == {"severity": "major", "raised_severity": None, "impact": IMPACT}
    assert journal_mod._raised({"text": "a"}) == {"severity": "minor", "raised_severity": None}
    # 知らない重要度・文字列でない 1 文
    assert journal_mod._raised({"text": "a", "severity": "重い", "impact": 3}) == {"severity": "minor", "raised_severity": None}


def test_issue_4014_enter_downgrades_heavy_findings_without_impact() -> None:
    """#4014: 1 文の無い major と critical は minor の行になり、収束の妨げのキーに入らず、修正担当へ渡らない。"""
    journal = drive.FindingJournal()
    keys = journal.enter(
        1, [{"text": "A", "severity": "major", "by": "codex"}, {"text": "B", "severity": "critical", "impact": " ", "by": "kiro"}]
    )
    assert keys == frozenset()
    assert [(r["severity"], r["raised_severity"], "impact" in r) for r in journal.rows.values()] == [
        ("minor", "major", False),
        ("minor", "critical", False),
    ]
    assert all(criteria_decide.downgraded(r) for r in journal.rows.values())
    assert journal.pending(()) == ([], [])


def test_issue_4014_reraised_within_a_round_keeps_major_and_the_impact_in_either_order() -> None:
    """#4014: 同じラウンドで 1 文の無い major と 1 文つきの major が返ると、どちらの順でも major で 1 文を持ち、妨げに数える。"""
    bare = {"text": "A が止まる", "severity": "major", "by": "codex"}
    told = {"text": "A が止まる", "severity": "major", "impact": IMPACT, "by": "kiro"}
    for raised in ([bare, told], [told, bare]):
        journal = drive.FindingJournal()
        keys = journal.enter(1, raised)
        [row] = journal.rows.values()
        assert (row["severity"], row["impact"], row["raised_severity"]) == ("major", IMPACT, "major")
        assert len(keys) == 1 and not criteria_decide.downgraded(row)


def test_issue_4014_unrated_and_major_without_impact_keep_the_raised_major_in_either_order() -> None:
    """#4014（PR 4023 の r1-4）: 1 文の無い重要度なしと major が同じラウンドで返ると、どちらの順でも minor へ下げ、
    元の重要度は明示された major を残す。"""
    unrated = {"text": "A が止まる", "by": "codex"}
    major = {"text": "A が止まる", "severity": "major", "by": "kiro"}
    for raised in ([unrated, major], [major, unrated]):
        journal = drive.FindingJournal()
        assert journal.enter(1, raised) == frozenset()
        [row] = journal.rows.values()
        assert (row["severity"], row["raised_severity"]) == ("minor", "major")


def test_issue_4014_downgraded_row_reraised_with_impact_goes_back_to_major() -> None:
    """#4014: 下げた行が次のラウンドで 1 文つきで出し直されると major に戻り、下げた行ではなくなる。"""
    journal = drive.FindingJournal()
    journal.enter(1, [{"text": "A", "severity": "major", "by": "codex"}])
    assert journal.enter(2, [{"text": "A", "severity": "critical", "impact": IMPACT, "by": "kiro"}]) != frozenset()
    [row] = journal.rows.values()
    assert (row["severity"], row["impact"], row["raised_severity"]) == ("critical", IMPACT, "critical")
    assert not criteria_decide.downgraded(row)


def test_issue_4014_fixed_finding_coming_back_without_impact_stays_major_with_the_old_impact() -> None:
    """#4014 の決定 4: 1 文つきの major で入って直した指摘が 1 文の無い major で戻ると、未解決・major のまま前の 1 文を持つ。"""
    journal = drive.FindingJournal()
    journal.enter(1, [{"text": "A", "severity": "major", "impact": IMPACT, "path": "x.py", "line": 3, "by": "codex"}])
    journal.rows["r1-1"]["outcome"] = fin.FIXED
    journal.enter(2, [{"text": "A", "severity": "major", "path": "x.py", "line": 9, "by": "kiro"}])
    row = journal.rows["r1-1"]
    assert (row["outcome"], row["severity"], row["impact"], row["raised_severity"], row["line"]) == (
        fin.UNRESOLVED,
        "major",
        IMPACT,
        "major",
        9,
    )
    open_, _ = journal.pending(())
    assert [f["id"] for f in open_] == ["r1-1"]


@pytest.mark.parametrize("order", [1, -1])
def test_issue_4363_fixed_finding_coming_back_takes_the_impact_and_the_place_of_this_time(order: int) -> None:
    """直した指摘が別の経路で戻ると、判定役が読む本文・1 文・位置は、出し直しの順によらず今回の同じ 1 件のものになる。"""
    journal = drive.FindingJournal()
    journal.enter(1, [{"text": "A", "severity": "major", "impact": IMPACT, "path": "x.py", "line": 3, "by": "codex"}])
    journal.rows["r1-1"]["outcome"] = fin.FIXED
    again = [
        {"text": "A", "severity": "minor", "path": "x.py", "line": 9, "by": "codex"},
        {"text": "a", "severity": "major", "impact": "別の経路で保存が止まる", "path": "y.py", "line": 5, "by": "kiro"},
    ]
    journal.enter(2, again[::order])
    row = journal.rows["r1-1"]
    assert (row["text"], row["impact"], row["path"], row["line"]) == ("a", "別の経路で保存が止まる", "y.py", 5)
    assert journal.fresh == ["r1-1"]


def test_issue_4363_finding_coming_back_without_impact_stays_major_and_is_not_asked() -> None:
    """今回の 1 文が無いまま戻った指摘は重さを保ち、前の経路の 1 文で下げられないように判定役へ渡さない。"""
    journal = drive.FindingJournal()
    journal.enter(1, [{"text": "A", "severity": "major", "impact": IMPACT, "path": "x.py", "line": 3, "by": "codex"}])
    journal.rows["r1-1"]["outcome"] = fin.FIXED
    journal.enter(2, [{"text": "A", "severity": "major", "path": "y.py", "line": 5, "by": "kiro"}])
    assert journal.rows["r1-1"]["severity"] == "major" and journal.fresh == []


def test_issue_4014_carried_rows_are_not_rated_again() -> None:
    """#4014: 引き継いだ行は付け直さず、raised_severity を持たない。重要度の無い行は今のまま収束の妨げになり、
    相手役から出し直されても raised_severity を足さない。"""
    journal = drive.FindingJournal()
    journal.carry([prior_decide.Carried("t1", None, "A", None, None, None, None)])
    row = journal.rows["t1"]
    assert "severity" not in row and "raised_severity" not in row and "impact" not in row
    assert [f["id"] for f in journal.pending(())[0]] == ["t1"]
    journal.enter(1, [{"text": "A", "severity": "major", "by": "codex"}])
    assert "raised_severity" not in row and "severity" not in row
    assert not criteria_decide.downgraded(row)


def test_issue_4363_enter_keeps_the_protect_kinds_of_the_reviewer() -> None:
    """#4363: 相手役の `protect` のうち保護の種類だけを行に残し、当たらない指摘と形の違う値はキーを持たない。"""
    journal = drive.FindingJournal()
    journal.enter(
        1,
        [
            {"text": "A", "protect": ["user_data", "速さ", "secret", "secret"], "by": "codex"},
            {"text": "B", "protect": [], "by": "codex"},
            {"text": "C", "protect": "secret", "by": "codex"},
            {"text": "D", "by": "codex"},
        ],
    )
    assert [r.get("protect") for r in journal.rows.values()] == [["secret", "user_data"], None, None, None]
    assert ["protect" in r for r in journal.rows.values()] == [True, False, False, False]


@pytest.mark.parametrize("rounds", [(1, 1), (1, 2)])
def test_issue_4363_reraised_finding_adds_protect_kinds_and_never_drops_them(rounds: tuple[int, int]) -> None:
    """#4363: 同じ指摘の出し直し（同じラウンドでも後のラウンドでも）は保護の種類を足し、`protect` の無い出し直しでは外さない。"""
    first, second = rounds
    journal = drive.FindingJournal()
    one = {"text": "A", "protect": ["user_data"], "by": "codex"}
    two = {"text": "A", "protect": ["authz"], "by": "kiro"}
    if first == second:
        journal.enter(first, [one, two])
    else:
        journal.enter(first, [one])
        journal.enter(second, [two])
    [row] = journal.rows.values()
    assert row["protect"] == ["authz", "user_data"]
    journal.enter(3, [{"text": "A", "by": "kiro"}])
    assert row["protect"] == ["authz", "user_data"]


def test_issue_4363_fixed_finding_coming_back_keeps_protect() -> None:
    """#4363: 直した指摘が `protect` を書かずに戻っても、行の保護の種類は残る。"""
    journal = drive.FindingJournal()
    journal.enter(1, [{"text": "A", "severity": "major", "impact": IMPACT, "protect": ["secret"], "by": "codex"}])
    journal.rows["r1-1"]["outcome"] = fin.FIXED
    journal.enter(2, [{"text": "A", "severity": "major", "impact": IMPACT, "by": "kiro"}])
    row = journal.rows["r1-1"]
    assert (row["outcome"], row["protect"]) == (fin.UNRESOLVED, ["secret"])


@pytest.mark.parametrize("protect", [[], ["secret"]])
def test_issue_4363_protect_does_not_change_how_a_finding_is_rated(protect: list[str]) -> None:
    """#4363: 今の下げ方の退行を防ぐ。`protect` の有無によらず、1 文のある major は重いままで修正担当へ渡り、1 文の無い major は
    minor になる。"""
    journal = drive.FindingJournal()
    told = {"text": "A", "severity": "major", "impact": IMPACT, "protect": protect, "by": "codex"}
    keys = journal.enter(1, [told, {"text": "B", "severity": "major", "protect": protect, "by": "codex"}])
    assert [r["severity"] for r in journal.rows.values()] == ["major", "minor"]
    assert keys == frozenset({"a"})
    assert [r["text"] for r in journal.handable()] == ["A"]


def test_occurrence_is_new_for_each_raise_and_written_for_older_rows() -> None:
    """#4016: 指摘を初めて記録するとき（`enter`・`carry`・`apply_fix`）と再発で発生の値を書き、前のバージョンの行には
    `with_occurrences` が書く。同じ指摘の出し直し（直していない）は値を変えない。"""
    journal = drive.FindingJournal()
    journal.enter(1, [{"text": "A", "by": "codex"}, {"text": "B", "by": "codex"}])
    first = journal.rows["r1-1"][journal_mod.OCCURRENCE]
    assert first != journal.rows["r1-2"][journal_mod.OCCURRENCE]
    journal.enter(2, [{"text": "A", "by": "kiro"}])
    assert journal.rows["r1-1"][journal_mod.OCCURRENCE] == first
    journal.rows["r1-1"]["outcome"] = fin.FIXED
    journal.enter(3, [{"text": "A", "by": "kiro"}])
    assert journal.rows["r1-1"][journal_mod.OCCURRENCE] != first
    journal.carry([prior_decide.Carried("t9", 9, "C", "codex", "major", "a.py", 1)])
    assert len(journal.rows["t9"][journal_mod.OCCURRENCE]) == 12
    data = {"findings": [_row("r1-1", "a", 1), {**_row("r1-2", "b", 1), journal_mod.OCCURRENCE: "kept00000000"}]}
    assert journal_mod.with_occurrences(data)
    assert len(data["findings"][0][journal_mod.OCCURRENCE]) == 12 and data["findings"][1][journal_mod.OCCURRENCE] == "kept00000000"
    assert not journal_mod.with_occurrences(data)


# --- プロジェクト MVV に反する指摘 -----------------------------------------------------------

ITEMS = ("Mission", "Value 1", "Value 5", "P1", "C7")
V11, V12, ABSENT = "approved:11:aaa:ccc", "approved:12:bbb:ccc", "absent:::"


def _named(text: str, mvv: object = None, severity: str | None = "major", **more: object) -> dict:
    """相手役の指摘。`mvv` があれば、反する MVV の項目として書く。"""
    named = {} if mvv is None else {"mvv": mvv}
    return {"text": text, "severity": severity, "impact": IMPACT, "by": "codex", **named, **more}


def _entered(*raised: dict, mvv: str = V11) -> journal_mod.FindingJournal:
    journal = journal_mod.FindingJournal()
    journal.enter(1, raised, ITEMS, mvv)
    return journal


def _decide(journal: journal_mod.FindingJournal, outcome: str, mvv: str | None = V11, round_no: int = 1) -> None:
    """指摘 `r1-1` の修正の結果をマージする。"""
    journal.apply_fix([{"id": "r1-1", "outcome": outcome, "reason": "反しない"}], (), round_no, mvv=mvv)


def test_mvv_finding_is_held_at_a_blocking_severity_with_the_one_before() -> None:
    """MVV の項目に反する指摘は、軽い重さでも修正担当へ渡す重さに固定し、固定する前の重さと判定した MVV の識別子を残す。"""
    journal = journal_mod.FindingJournal()
    assert len(journal.enter(1, [_named("A", "Value 5", "minor")], ITEMS, V11)) == 1
    [row] = journal.handable()
    assert (row["mvv"], row["mvv_pinned"], row["judged_mvv"]) == ("Value 5", True, V11)
    assert (row["severity"], row["unpinned_severity"]) == ("major", "minor")
    assert "redline" not in row and journal.warnings == []


@pytest.mark.parametrize("severity", ["major", "critical"])
def test_mvv_finding_without_impact_is_not_lowered_but_warned(severity: str) -> None:
    """1 文の無い重い指摘でも、MVV に反するなら重いまま修正担当へ渡り、下げた行に数えない（#4363 の受け入れ条件 5）。"""
    journal = _entered(_named("A", "Value 5", severity, impact=" "))
    [row] = journal.handable()
    assert (row["severity"], row["unpinned_severity"]) == (severity, "minor")
    assert not criteria_decide.downgraded(row)
    [warning] = journal.warnings
    assert "r1-1" in warning and "起きること" in warning


@pytest.mark.parametrize(("raised", "kept"), [("major", "major"), ("critical", "critical"), (None, "major")])
@pytest.mark.parametrize("item", ["P1", "C7"])
def test_redline_without_impact_is_not_lowered_but_warned(item: str, raised: str | None, kept: str) -> None:
    journal = _entered(_named("A", item, raised, impact=" "))
    [row] = journal.rows.values()
    assert (row["severity"], row["raised_severity"], row["redline"]) == (kept, raised, True)
    assert not criteria_decide.downgraded(row)
    [warning] = journal.warnings
    assert "r1-1" in warning and "起きること" in warning


@pytest.mark.parametrize("order", [("Value 5", "P1"), ("P1", "Value 5"), (None, "P1"), ("P1", None)])
def test_redline_without_impact_keeps_its_severity_in_any_order(order: tuple[str | None, ...]) -> None:
    journal = _entered(*(_named("A", m, impact=None) for m in order))
    [row] = journal.rows.values()
    assert (row["severity"], row["redline"]) == ("major", True)
    assert not {"mvv_pinned", "unpinned_severity"} & set(row)
    [warning] = journal.warnings
    assert warning.count("r1-1") == 1


@pytest.mark.parametrize("value", ["P9", "Value", 5, ["Value 5"]])
def test_unknown_mvv_item_is_an_ordinary_finding_with_a_warning(value: object) -> None:
    journal = _entered(_named("A", value, "minor"), _named("B", " "))
    [a, b] = journal.rows.values()
    assert (a["severity"], "mvv" in a, "judged_mvv" in a) == ("minor", False, False)
    assert [r["text"] for r in journal.handable()] == ["B"]
    [warning] = journal.warnings
    assert "r1-1" in warning and "r1-2" not in warning
    # 注意は次のラウンドへ持ち越さない。MVV が承認されていなければ、項目が書かれていても注意しない
    journal.enter(2, [_named("A", value, "minor")], (), ABSENT)
    assert journal.warnings == []


@pytest.mark.parametrize(
    "order",
    [("Value 1", "C7", "P1"), ("P1", "Value 1", "C7"), ("C7", "P1", "Value 1")],
)
@pytest.mark.parametrize("rounds", ["同じラウンド", "後のラウンド"])
def test_redline_wins_over_other_items_in_any_order(order: tuple[str, ...], rounds: str) -> None:
    """必ず人の承認が要る操作と言われた指摘は、言われた順によらず、その番号をすべて残し、修正担当へ渡さず収束も止めない。"""
    raised = [_named("A", m, "minor") for m in order]
    journal = journal_mod.FindingJournal()
    for no, batch in enumerate([raised] if rounds == "同じラウンド" else [[r] for r in raised], 1):
        keys = journal.enter(no, batch, ITEMS, V11)
    assert keys == frozenset() and journal.pending([]) == ([], [])
    [row] = journal.rows.values()
    got = {k: v for k, v in row.items() if "mvv" in k or "redline" in k or "severity" in k}
    assert got == {
        "mvv": "P1",
        "redline": True,
        "redline_items": ["P1", "C7"],
        "judged_mvv": V11,
        "severity": "minor",
        "raised_severity": "minor",
    }


def test_first_item_in_the_mvv_is_kept_for_a_finding_named_with_two_values() -> None:
    got = [_entered(*(_named("A", m) for m in order)).rows["r1-1"]["mvv"] for order in (("Value 5", "Value 1"), ("Value 1", "Value 5"))]
    assert got == ["Value 1", "Value 1"]


def test_handable_omits_decided_and_light_findings() -> None:
    journal = _entered(_named("A", "Value 5"), _named("B"), _named("C", severity="nit"), _named("D"))
    journal.apply_fix([{"id": "r1-2", "outcome": fin.FIXED}, {"id": "r1-4", "outcome": fin.SKIPPED, "reason": "別"}], (), 1, mvv=V11)
    assert [r["text"] for r in journal.handable()] == ["A"]


def test_finding_deferred_as_a_value_becomes_a_redline_when_it_is_raised_as_one() -> None:
    """修正担当が MVV に反しないと見送った指摘でも、同じ MVV で必ず人の承認が要る操作と言われたら、人の判断を待つ指摘に戻す。"""
    journal = _entered(_named("A", "Value 5"))
    _decide(journal, fin.SKIPPED)
    journal.enter(2, [_named("A", "P1")], ITEMS, V11)
    [row] = journal.rows.values()
    assert (row["redline"], row["redline_items"], row["outcome"]) == (True, ["P1"], fin.UNRESOLVED)
    assert journal.revived == ["r1-1"] and journal.handable() == [] and journal.skipped_texts(V11) == []


@pytest.mark.parametrize(
    ("skipped_at", "now", "listed"),
    [
        (V11, V11, True),
        (V11, V12, False),
        (V11, ABSENT, False),
        (ABSENT, V11, False),
        # 識別子の無い見送り（前のバージョンの NDF が書いた）は、MVV が無い間だけ同じとみなす
        (None, ABSENT, True),
        (None, V11, False),
        (None, "unapproved:::", False),
    ],
)
def test_only_findings_deferred_under_the_same_mvv_stay_deferred(skipped_at: str | None, now: str, listed: bool) -> None:
    """見送り済みの一覧に載せる（出し直さないよう求める）のは同じ MVV での見送りだけで、違う MVV での見送りは出し直されたら判定し直す。"""
    journal = _entered(_named("A"))
    _decide(journal, fin.SKIPPED, skipped_at)
    assert journal.skipped_texts(now) == (["A"] if listed else [])
    keys = journal.enter(2, [_named("A", path="b.py", line=3)], ITEMS, now)
    [row] = journal.rows.values()
    assert len(keys) == 1 and row["outcome"] == (fin.SKIPPED if listed else fin.UNRESOLVED)
    assert journal.revived == ([] if listed else ["r1-1"])
    assert ("path" in row, "skipped_mvv" in row) == (not listed, listed and skipped_at is not None)


def test_deferring_records_the_mvv_and_fixing_forgets_it() -> None:
    journal = _entered(_named("A"))
    for outcome, mvv, want in [(fin.SKIPPED, V11, V11), (fin.SKIPPED, V12, V12), (fin.SKIPPED, None, None), (fin.SKIPPED, V11, V11)]:
        _decide(journal, outcome, mvv)
        assert journal.rows["r1-1"].get("skipped_mvv") == want
    _decide(journal, fin.FIXED)
    assert "skipped_mvv" not in journal.rows["r1-1"]


def test_issue_4090_6_redline_of_an_earlier_mvv_is_judged_again() -> None:
    """前の MVV で必ず人の承認が要る操作だった指摘が、今の MVV で Value の項目に反すると出し直されたら、修正担当へ渡し収束を止める。"""
    journal = _entered(_named("A", "P1"))
    assert len(journal.enter(2, [_named("A", "Value 5", "minor")], ITEMS, V12)) == 1
    [row] = journal.handable()
    assert (row["mvv"], row["mvv_pinned"], row["severity"], row["judged_mvv"]) == ("Value 5", True, "major", V12)
    assert "redline" not in row and "redline_items" not in row
    assert row["mvv_history"] == [{"judged_mvv": V11, "mvv": "P1", "redline_items": ["P1"], "outcome": fin.UNRESOLVED}]
    # 同じ MVV で出し直されても履歴は増えない
    journal.enter(3, [_named("A", "Value 5")], ITEMS, V12)
    assert len(row["mvv_history"]) == 1
    # 次に MVV が変わったときは、前の履歴に足す
    journal.restate("approved:13:ddd:ccc")
    assert [h["mvv"] for h in row["mvv_history"]] == ["P1", "Value 5"]


@pytest.mark.parametrize(("before", "after"), [("minor", "minor"), ("major", "major")])
def test_issue_4090_6_held_severity_of_an_earlier_mvv_is_not_kept(before: str, after: str) -> None:
    """前の MVV で固定した重さは、今の MVV で項目なしに出し直されたら固定する前へ戻る（固定する前から重ければ重いまま）。"""
    journal = _entered(_named("A", "Value 5", before))
    _decide(journal, fin.FIXED)
    keys = journal.enter(2, [_named("A", severity="minor")], ITEMS, V12)
    [row] = journal.rows.values()
    assert (row["severity"], row["outcome"], len(keys)) == (after, fin.UNRESOLVED, int(after == "major"))
    assert not {"mvv", "mvv_pinned", "judged_mvv", "unpinned_severity"} & set(row)
    assert row["mvv_history"] == [{"judged_mvv": V11, "mvv": "Value 5", "outcome": fin.FIXED}]


def test_held_severity_remembers_the_heaviest_one_before_holding() -> None:
    journal = _entered(_named("A", "Value 5", "minor"), _named("A", severity="critical"))
    journal.restate(V12)
    assert journal.rows["r1-1"]["severity"] == "critical"


def test_issue_4090_6_restate_drops_classifications_of_an_earlier_mvv() -> None:
    """MVV が変わったら、出し直されない指摘の分類も外して履歴に残す。終わり方は変えず、同じ MVV の指摘はそのまま残す。"""
    journal = _entered(_named("A", "P1", "minor"), _named("B", "Value 5", "minor"), _named("C"), _named("D", "Value 1"))
    journal.apply_fix([{"id": "r1-2", "outcome": fin.SKIPPED, "reason": "反しない"}, {"id": "r1-4", "outcome": fin.FIXED}], (), 1, mvv=V11)
    assert journal.restate(V11) == []
    assert journal.restate(V12) == ["r1-1", "r1-2", "r1-4"]
    rows = journal.rows
    assert all(not {"mvv", "mvv_pinned", "redline", "redline_items", "judged_mvv", "unpinned_severity"} & set(r) for r in rows.values())
    assert [(r["outcome"], r["severity"]) for r in rows.values()] == [
        (fin.UNRESOLVED, "minor"),
        (fin.SKIPPED, "minor"),
        (fin.UNRESOLVED, "major"),
        (fin.FIXED, "major"),
    ]
    assert rows["r1-2"]["mvv_history"] == [{"judged_mvv": V11, "mvv": "Value 5", "outcome": fin.SKIPPED, "skipped_mvv": V11}]
    assert "mvv_history" not in rows["r1-3"]
    assert [r["text"] for r in journal.handable()] == ["C"]
    assert journal.restate(V12) == []


def test_findings_carry_the_mvv_item_and_whether_it_is_a_redline() -> None:
    journal = _entered(_named("A", "Value 5"), _named("B", "P1"), _named("C"))
    assert [(f.mvv, f.redline) for f in journal.findings()] == [("Value 5", False), ("P1", True), (None, False)]


def test_result_for_a_finding_made_by_ndf_is_not_taken_as_its_outcome() -> None:
    """NDF が機械で確かめて作った指摘は、修正担当の結果では閉じない。理由だけ残し、マージした件に数えない（#4109）。"""
    rows = [_row("n1", "根拠の行の誤り", 1, by="ndf"), _row("n2", "もう 1 つの誤り", 1, by="ndf"), _row("r1-1", "a", 1)]
    journal = journal_mod.FindingJournal(rows)
    items = [
        {"id": "n1", "outcome": fin.FIXED},
        {"id": "r1-1", "outcome": fin.FIXED},
        {"id": "n2", "outcome": fin.SKIPPED, "reason": "誤りでない"},
    ]
    assert journal.apply_fix(items, (), 1, mvv=V11) == [items[1]]
    assert [(r["outcome"], r["reason"]) for r in journal.rows.values()] == [
        (fin.UNRESOLVED, None),
        (fin.UNRESOLVED, None),
        (fin.FIXED, None),
    ]
    assert (journal.rows["n2"]["fixer_reason"], "skipped_mvv" in journal.rows["n2"]) == ("誤りでない", False)


@pytest.mark.parametrize("outcome", [fin.FIXED, fin.SKIPPED])
def test_result_for_a_redline_is_not_taken_as_its_outcome(outcome: str) -> None:
    """必ず人の承認が要る操作に当たる指摘は、修正担当の結果では閉じず、人の承認を待つまま残る。"""
    journal = _entered(_named("A", "P1"), _named("B"))
    items = [{"id": "r1-1", "outcome": outcome, "reason": "ついでに扱った"}, {"id": "r1-2", "outcome": fin.FIXED}]
    assert journal.apply_fix(items, (), 1, mvv=V11) == [items[1]]
    row = journal.rows["r1-1"]
    assert (row["outcome"], row["reason"], row["redline"]) == (fin.UNRESOLVED, None, True)
    assert (row["fixer_reason"], "skipped_mvv" in row) == ("ついでに扱った", False)


def test_resent_fix_does_not_restore_a_redline() -> None:
    """送り直した修正の記録に id があっても、必ず人の承認が要る操作に当たる指摘は直したへ戻さない。"""
    journal = _entered(_named("A", "P1"), _named("B"))
    journal.restore_fixed(["r1-1", "r1-2", "nope"])
    assert [r["outcome"] for r in journal.rows.values()] == [fin.UNRESOLVED, fin.FIXED]


def test_reviewer_finding_with_the_same_text_does_not_classify_a_finding_made_by_ndf() -> None:
    row = _row("n1", "根拠の行の誤り", 1, by="ndf")
    journal = journal_mod.FindingJournal([row])
    journal.enter(2, [_named("根拠の行の誤り", "P1")], ITEMS, V11)
    assert "mvv" not in journal.rows["n1"] and [r["id"] for r in journal.handable()] == ["n1"]


# --- MVV の根拠の記述の誤り --------------------------------------------------------------------


def _error(path: str = "doc.md", line: int = 3, cited: str = "Value 5", kind: str = "version", **other) -> dict:
    return {"path": path, "line": line, "cited": cited, "kind": kind, "version": 1, "unknown": [], "text": f"{path} の {cited}", **other}


def test_citation_key_is_made_of_the_place_the_item_and_the_kind() -> None:
    """行・バージョン・指摘の文が変わっても同じ誤りとみなし、文書・引いた項目・種類のどれかが違えば別の誤りとみなす。"""
    key = journal_mod.citation_key(_error())
    assert key == journal_mod.citation_key(_error(line=9, version=2, text="別の文"))
    others = [_error(path="other.md"), _error(cited="Value 6"), _error(kind="format"), _error(path='doc.md","Value 5', cited="")]
    assert len({key, *map(journal_mod.citation_key, others)}) == 5


def test_citation_errors_become_findings_for_the_fixer() -> None:
    """誤りは、P の番号を引いていても MVV の項目の分類を持たない重い指摘になり、修正担当へ渡す。同じ誤りが 2 行にあれば 1 件で、
    最初の行を指す。"""
    journal = drive.FindingJournal([_row("r2-1", "相手役の指摘", 2)])
    errors = [_error(cited="P1", unknown=["P1"]), _error(cited="P1", line=8), _error(cited="Value 5")]
    assert journal.ingest_citations(errors, V11, 2)
    [first, second] = [journal.rows["r2-n1"], journal.rows["r2-n2"]]
    assert (first["cited"], first["unknown"], first["line"], second["cited"]) == ("P1", ["P1"], 3, "Value 5")
    assert (first["severity"], first["judged_mvv"], first["by"]) == ("major", V11, "ndf")
    assert not {"mvv", "mvv_pinned", "redline", "redline_items"} & set(first)
    assert [f["id"] for f in journal.handable()] == ["r2-1", "r2-n1", "r2-n2"]
    assert [f.by_ndf for f in journal.findings()] == [False, True, True]
    assert journal.enter(2, [{"text": "次の指摘", "by": "kiro"}]) is not None and "r2-2" in journal.rows


def test_the_same_citation_errors_change_nothing() -> None:
    journal = drive.FindingJournal()
    assert journal.ingest_citations([_error()], V11, 1)
    assert not journal.ingest_citations([_error()], V11, 1)
    assert not journal.ingest_citations(None, V11, 1)
    assert not drive.FindingJournal([_row("r1-1", "a", 1)]).ingest_citations([], V11, 1)


def test_only_the_check_decides_whether_a_citation_error_is_fixed() -> None:
    """見つかった誤りは未解決のまま（位置は今のラウンドのもの）、見つからなければ直したとする。確かめられなければ変えない。
    MVV が変わっても、同じ誤りは元の指摘のまま今の MVV の指摘にする。"""
    journal = drive.FindingJournal()
    journal.ingest_citations([_error()], V11, 1)
    occurrence = journal.rows["r1-n1"][journal_mod.OCCURRENCE]
    assert journal.ingest_citations([_error(line=9)], V11, 2)
    row = journal.rows["r1-n1"]
    assert (row["outcome"], row["line"], row["round"], row[journal_mod.OCCURRENCE]) == (fin.UNRESOLVED, 9, 2, occurrence)
    assert not journal.ingest_citations(None, V11, 3) and row["outcome"] == fin.UNRESOLVED
    journal.restate(V12)
    assert journal.ingest_citations([_error(line=9)], V12, 2) and (list(journal.rows), row["judged_mvv"]) == (["r1-n1"], V12)
    assert journal.ingest_citations([], V11, 3)
    assert (row["outcome"], row["judged_mvv"]) == (fin.FIXED, V11)


def test_citation_error_that_comes_back_is_open_again_with_a_new_occurrence() -> None:
    """直ったとした誤りがまた見つかれば、新しい発生として未解決へ戻す（解決済みの古いスレッドに結び付けない）。"""
    journal = drive.FindingJournal()
    journal.ingest_citations([_error()], V11, 1)
    occurrence = journal.rows["r1-n1"][journal_mod.OCCURRENCE]
    journal.ingest_citations([], V11, 2)
    assert journal.ingest_citations([_error(line=9)], V11, 3)
    row = journal.rows["r1-n1"]
    assert (row["outcome"], row["line"], row["round"]) == (fin.UNRESOLVED, 9, 3)
    assert row[journal_mod.OCCURRENCE] != occurrence and journal.revived == ["r1-n1"]


def test_open_citation_error_keeps_its_place_when_the_head_has_no_round() -> None:
    """ラウンドの記録に無いコミットで見つけた行の番号は、未解決の指摘の位置にしない。"""
    journal = drive.FindingJournal()
    journal.ingest_citations([_error()], V11, 1)
    assert not journal.ingest_citations([_error(line=9)], V11, 2, recorded=False)
    assert (journal.rows["r1-n1"]["line"], journal.rows["r1-n1"]["round"]) == (3, 1)


def test_finding_without_a_key_is_replaced_by_a_new_one() -> None:
    """誤りを見分ける値を持たない NDF の指摘（PR の記録から戻したもの）は解決とし、同じ誤りは新しい指摘として 1 回だけ足す。"""
    journal = drive.FindingJournal([_row("t77", "doc.md の Value 5", 0, by="ndf")])
    assert journal.ingest_citations([_error()], V11, 1)
    assert not journal.ingest_citations([_error()], V11, 1)
    assert {fid: f["outcome"] for fid, f in journal.rows.items()} == {"t77": fin.FIXED, "r1-n1": fin.UNRESOLVED}


# --- 判定役の確認（#4363） ---------------------------------------------------------------------


def _told(text: str = "A", **other) -> dict:
    return {"text": text, "severity": "major", "impact": IMPACT, "by": "codex", **other}


def _answered(fid: str = "r1-1", holds=False, tracks=()) -> dict:
    return {fid: verify_decide.Verdict(fid, holds, reason="呼ぶ経路が無い", tracks=tuple(tracks))}


def _lowered(**other):
    """判定役が成り立たないとして下げた指摘を 1 件持つジャーナルと、その行。"""
    journal = drive.FindingJournal()
    journal.enter(1, [_told(**other)], ITEMS, V11)
    journal.apply_checks(["r1-1"], _answered())
    return journal, journal.rows["r1-1"]


def test_issue_4363_enter_remembers_the_new_and_revived_findings() -> None:
    journal = drive.FindingJournal()
    journal.enter(1, [_told("A"), _told("B"), _told("A")])
    assert journal.fresh == ["r1-1", "r1-2"]
    journal.apply_fix([{"id": "r1-1", "outcome": fin.FIXED}], [], 1)
    journal.enter(2, [_told("A"), _told("B"), _told("C")])
    # 戻った A はラウンド 2 の指摘に数えるので、新しい C の番号は r2-2 になる
    assert journal.fresh == ["r1-1", "r2-2"]
    assert [journal.rows[fid]["text"] for fid in journal.fresh] == ["A", "C"]
    journal.enter(3, [])
    assert journal.fresh == []


@pytest.mark.parametrize("severity", ["major", "critical"])
def test_issue_4363_3_finding_that_does_not_hold_is_lowered_and_not_handed(severity: str) -> None:
    journal = drive.FindingJournal()
    journal.enter(1, [_told(severity=severity)])
    checked = journal.apply_checks(["r1-1"], _answered(tracks=("delivered",)))
    row = journal.rows["r1-1"]
    assert (row["severity"], row["raised_severity"]) == ("minor", severity)
    assert row["check"] == {
        "holds": False,
        "condition": "",
        "path": "",
        "result": "",
        "reason": "呼ぶ経路が無い",
        "tracks": ["delivered"],
        "lowered": True,
    }
    assert journal.pending([]) == ([], []) and not criteria_decide.downgraded(row)
    assert checked == journal_mod.Checked(frozenset({row["key"]}), frozenset())


def test_issue_4363_4_finding_that_holds_keeps_its_severity() -> None:
    journal = drive.FindingJournal()
    journal.enter(1, [_told()])
    checked = journal.apply_checks(["r1-1"], _answered(holds=True))
    row = journal.rows["r1-1"]
    assert (row["severity"], row["check"]["holds"], row["check"]["lowered"]) == ("major", True, False)
    assert journal.handable() == [row] and checked == journal_mod.Checked(frozenset(), frozenset())


@pytest.mark.parametrize("verdicts", [{}, _answered(holds=None, tracks=("secret",))])
def test_issue_4363_unconfirmed_finding_keeps_its_severity(verdicts: dict) -> None:
    journal = drive.FindingJournal()
    journal.enter(1, [_told()])
    checked = journal.apply_checks(["r1-1"], verdicts)
    row = journal.rows["r1-1"]
    assert row["severity"] == "major" and "check" not in row
    assert row.get("protect") == (["secret"] if verdicts else None)
    assert checked == journal_mod.Checked(frozenset(), frozenset({"r1-1"}))


@pytest.mark.parametrize("answer", [{"holds": False, "reason": ""}, {"holds": True}, {}])
def test_issue_4363_invalid_answer_leaves_only_its_protect_kinds(answer: dict) -> None:
    """#4363: 不正な回答は確認の結果にせず重要度を保つが、示した保護の種類は行に残す。"""
    journal = drive.FindingJournal()
    journal.enter(1, [_told()])
    answers = verify_decide.read([{"id": "r1-1", "tracks": ["secret", "delivered"], **answer}], ["r1-1"])
    checked = journal.apply_checks(["r1-1"], answers.verdicts)
    row = journal.rows["r1-1"]
    assert (row["severity"], row["protect"]) == ("major", ["secret"]) and "check" not in row
    assert answers.verdicts["r1-1"].tracks == ("secret",) and len(answers.warnings) == 1
    assert checked.unconfirmed == frozenset({"r1-1"})


def test_issue_4363_tracks_of_the_judge_stay_as_protect_kinds() -> None:
    journal = drive.FindingJournal()
    journal.enter(1, [_told()])
    journal.apply_checks(["r1-1"], _answered(tracks=("authz", "user_data", "delivered")))
    row = journal.rows["r1-1"]
    assert (row["severity"], row["protect"], row["check"]["lowered"]) == ("major", ["authz", "user_data"], False)


@pytest.mark.parametrize("told", [{"mvv": "Value 5"}, {"mvv": "C7"}, {"protect": ["secret"]}])
def test_issue_4363_5_marked_finding_is_not_lowered_by_the_check(told: dict) -> None:
    journal = drive.FindingJournal()
    journal.enter(1, [_told(**told)], ITEMS, V11)
    journal.apply_checks(["r1-1"], _answered())
    row = journal.rows["r1-1"]
    assert (row["severity"], row["check"]["lowered"]) == ("major", False)


def test_issue_4363_lowering_records_the_severity_of_a_row_without_the_raised_one() -> None:
    """前の実行から引き継いだ指摘は元の重要度を持たないので、下げる前の重要度を残す。元の重要度が不明（null）の行は上書きしない。"""
    journal = drive.FindingJournal([{**_row("r0-1", "a", 0), "severity": "critical"}, {**_row("r0-2", "b", 0), "raised_severity": None}])
    journal.apply_checks(["r0-1", "r0-2"], {**_answered("r0-1"), **_answered("r0-2")})
    assert [(r["severity"], r["raised_severity"]) for r in journal.rows.values()] == [("minor", "critical"), ("minor", None)]


def test_issue_4363_lowered_finding_stays_lowered_when_raised_again() -> None:
    journal, row = _lowered()
    assert journal.enter(2, [_told(severity="critical", by="kiro")]) == frozenset()
    assert (row["severity"], row["raised_severity"], row["check"]["lowered"]) == ("minor", "critical", True)
    assert journal.fresh == []


@pytest.mark.parametrize(
    "before, again, want",
    [("major", "major", "major"), ("critical", "minor", "critical"), ("major", "critical", "critical"), ("major", "nit", "major")],
)
def test_issue_4363_lowered_finding_raised_with_protect_gets_its_severity_back(before: str, again: str, want: str) -> None:
    journal, row = _lowered(severity=before)
    journal.enter(2, [_told(severity=again, protect=["secret"])])
    assert (row["severity"], row["protect"], row["check"]["lowered"]) == (want, ["secret"], False)


@pytest.mark.parametrize(
    "mvv, severity, protect, want",
    [
        ("Value 5", "minor", [], ("major", False)),
        ("Value 5", "critical", [], ("critical", False)),
        ("C7", "major", [], ("major", True)),
        ("C7", "nit", [], ("minor", True)),
        ("C7", None, [], ("major", True)),
        ("Value 5", "minor", ["secret"], ("major", False)),
        ("C7", "minor", ["secret"], ("major", True)),
    ],
)
def test_issue_4363_5_lowered_finding_raised_with_mvv_leaves_the_lowering(mvv: str, severity, protect: list, want: tuple) -> None:
    journal, row = _lowered()
    journal.enter(2, [_told(severity=severity, mvv=mvv, protect=protect)], ITEMS, V11)
    assert (row["severity"], bool(row.get("redline")), row["check"]["lowered"]) == (*want, False)


@pytest.mark.parametrize("protect_first", [False, True])
@pytest.mark.parametrize("rounds", [(2, 2), (2, 3)])
@pytest.mark.parametrize("mvv", ["Value 5", "C7"])
def test_issue_4363_lowered_finding_gets_its_severity_back_in_any_order_of_mvv_and_protect(
    mvv: str, rounds: tuple[int, int], protect_first: bool
) -> None:
    """#4363: MVV の項目だけの出し直しと保護の種類だけの出し直しが別々に来ても、順によらず元の critical に戻る。"""
    journal, row = _lowered(severity="critical")
    told = [_told(severity="minor", mvv=mvv), _told(severity="minor", protect=["secret"])]
    for round_no, item in zip(rounds, reversed(told) if protect_first else told):
        journal.enter(round_no, [item], ITEMS, V11)
    assert row["severity"] == "critical"
    journal.restate("other")
    assert row["severity"] == "critical"


@pytest.mark.parametrize("protect_first", [False, True])
def test_issue_4363_restored_finding_follows_a_later_raised_severity_in_any_order(protect_first: bool) -> None:
    """#4363: 戻した後の出し直しで元の重要度が上がっても、順によらず固定を外した後は critical になる。"""
    journal, row = _lowered()
    told = [_told(severity="critical", impact=None, mvv="Value 5"), _told(severity="minor", protect=["secret"])]
    for item in reversed(told) if protect_first else told:
        journal.enter(2, [item], ITEMS, V11)
    assert row["severity"] == "critical"
    journal.restate("other")
    assert row["severity"] == "critical"


@pytest.mark.parametrize("mvv_first", [False, True])
def test_issue_4363_lowered_finding_has_the_same_severity_in_any_order_within_a_round(mvv_first: bool) -> None:
    """#4363: 同じラウンドの critical と MVV の項目付きの minor は、処理の順によらず critical になる。"""
    journal, row = _lowered()
    told = [_told(severity="critical"), _told(severity="minor", mvv="Value 5")]
    journal.enter(2, told[::-1] if mvv_first else told, ITEMS, V11)
    assert (row["severity"], row["check"]["lowered"]) == ("critical", False)


def test_issue_4363_revived_finding_is_checked_again_and_keeps_protect() -> None:
    journal = drive.FindingJournal()
    journal.enter(1, [_told()])
    journal.apply_checks(["r1-1"], _answered(holds=True, tracks=("secret",)))
    journal.apply_fix([{"id": "r1-1", "outcome": fin.FIXED}], [], 1)
    journal.enter(2, [_told()])
    row = journal.rows["r1-1"]
    assert "check" not in row and row["protect"] == ["secret"] and journal.fresh == ["r1-1"]
    journal.apply_checks(["r1-1"], _answered())
    assert (row["severity"], row["check"]["lowered"]) == ("major", False)
