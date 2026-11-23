"""指摘のジャーナル（drive.FindingJournal）の規則。外部の起動・git を使わずに確かめる。"""

from __future__ import annotations

from ndf.domains.crossreview import criteria_decide, drive, journal as journal_mod, prior_decide
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
    assert list(journal.rows.values()) == [
        {**_row("r1-1", "一つ目", 1, section="a.md#セクション"), **raised_rows},
        {**_row("r1-2", "二つ目", 1, by="kiro"), **raised_rows},
    ]


def test_enter_skips_deferred_findings_and_reopens_fixed_ones() -> None:
    journal = drive.FindingJournal([_row("r1-1", "直した", 1, fin.FIXED), _row("r1-2", "見送った", 1, fin.SKIPPED, "別で直す")])
    assert journal.enter(2, [{"text": "直した", "by": "kiro"}, {"text": "見送った", "by": "kiro"}]) == frozenset({"直した", "見送った"})
    assert list(journal.rows.values()) == [_row("r1-1", "直した", 2), _row("r1-2", "見送った", 1, fin.SKIPPED, "別で直す")]
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
        {"id": "rebuild-1", "outcome": fin.SKIPPED, "reason": "後で"},
        "x",
    ]
    reported = journal.apply_fix(items, [fin.Handed("rebuild-1", "足す")], 2)
    assert reported == [items[0], items[2], items[3]]
    assert list(journal.rows.values()) == [
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
