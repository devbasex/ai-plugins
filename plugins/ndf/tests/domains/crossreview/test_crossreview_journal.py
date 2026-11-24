"""指摘のジャーナル（drive.FindingJournal）の規則。外部の起動・git を使わずに確かめる。"""

from __future__ import annotations

import pytest

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


def _plain(rows) -> list[dict]:
    """行から発生の値（乱数）を外す。新しい行と再発の行は 12 字の発生の値を持つ。"""
    out = []
    for row in rows:
        row = dict(row)
        occurrence = row.pop(journal_mod.OCCURRENCE, None)
        assert occurrence is None or len(occurrence) == 12
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
        {"id": "rebuild-1", "outcome": fin.SKIPPED, "reason": "後で"},
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


def _named(text: str, mvv: object = None, severity: str = "major", **more: object) -> dict:
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
    journal = _entered(_named("A", "Value 5", severity, impact=" "))
    [row] = journal.handable()
    assert (row["severity"], row["unpinned_severity"]) == (severity, "minor")
    [warning] = journal.warnings
    assert "r1-1" in warning and "起きること" in warning


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


def test_reviewer_finding_with_the_same_text_does_not_classify_a_finding_made_by_ndf() -> None:
    row = _row("n1", "根拠の行の誤り", 1, by="ndf")
    journal = journal_mod.FindingJournal([row])
    journal.enter(2, [_named("根拠の行の誤り", "P1")], ITEMS, V11)
    assert "mvv" not in journal.rows["n1"] and [r["id"] for r in journal.handable()] == ["n1"]
