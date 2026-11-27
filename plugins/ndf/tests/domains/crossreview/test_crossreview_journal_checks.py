"""ジャーナルの、判定役の確認で下げる・解除する規則（#4363）。"""

from __future__ import annotations

import pytest

from ndf.domains.crossreview import criteria_decide, drive, journal as journal_mod, verify_decide
from ndf.domains.crossreview import finish_decide as fin

from .test_crossreview_journal import IMPACT, ITEMS, V11, _row, _told

# --- 判定役の確認（#4363） ---------------------------------------------------------------------


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


@pytest.mark.parametrize("order", [1, -1])
@pytest.mark.parametrize(
    "other", [{"text": "a の別の経路"}, {"impact": "既存の設定が消える"}, {"path": "b.py"}, {"section": "doc.md#移行"}]
)
def test_issue_4363_lowered_finding_raised_with_other_content_is_unlowered_and_not_asked(other: dict, order: int) -> None:
    """下げた指摘は、行の番号だけが違う同じ内容の出し直しでは下げたまま、内容の違う重い出し直しでは元の重要度へ戻し、
    本文と位置は行のまま、判定役へ渡し直さない。"""
    journal, row = _lowered(severity="critical", path="a.py", line=1)
    journal.enter(2, [_told(severity="critical", path="a.py", line=9)])
    assert (row["severity"], row["check"]["lowered"]) == ("minor", True)
    # 本文の違いは、指摘のキー（本文の先頭）より後ろに置く
    row.update(text="A" * 500, key=journal_mod.oscillation.finding_key("A" * 500))
    same = _told("A" * 500, path="a.py", line=1)
    changed = {**same, **other}
    if "text" in other:
        changed["text"] = "A" * 500 + other["text"]
    journal.enter(3, [same, changed][::order])
    assert (row["severity"], row["check"]["lowered"], journal.fresh) == ("critical", False, [])
    assert (row["text"], row["impact"], row["path"], row["line"]) == ("A" * 500, IMPACT, "a.py", 1)


@pytest.mark.parametrize("order", [1, -1])
@pytest.mark.parametrize("other", [{"impact": "既存の設定が消える"}, {"line": 2}, {"protect": ["secret"]}])
def test_issue_4363_finding_with_a_different_or_protected_report_is_not_asked(other: dict, order: int) -> None:
    """同じ指摘にまとまる重い報告の内容が違うか、保護の種類を挙げた報告があれば、判定役へ渡さず重いままにする。"""
    journal = drive.FindingJournal()
    journal.enter(1, [_told(path="a.py", line=1), _told(**{"path": "a.py", "line": 1, **other})][::order])
    row = journal.rows["r1-1"]
    assert (row["severity"], journal.fresh) == ("major", [])
    assert row.get("protect") == other.get("protect")


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


@pytest.mark.parametrize("order", [1, -1])
def test_issue_4363_heavy_reports_of_different_conditions_at_one_place_are_not_asked(order: int) -> None:
    """同じ位置でも発生条件の違う重い報告は、1 つの否定で下がらないように判定役へ渡さず、どの条件も残す。"""
    journal = drive.FindingJournal()
    told = [_told(path="a.py", line=1, impact=impact) for impact in ("空の入力で失敗する", "既存データの更新で失敗する")]
    journal.enter(1, told[::order])
    row = journal.rows["r1-1"]
    assert (row["severity"], journal.fresh) == ("major", [])
    shown = criteria_decide.fixer_impact(row)
    assert "空の入力で失敗する" in shown and "既存データの更新で失敗する" in shown


@pytest.mark.parametrize("other", [{"impact": "既存データの更新で失敗する"}, {"section": "doc.md#移行"}])
def test_issue_4363_lowered_finding_raised_differently_in_the_same_file_is_checked_again(other: dict) -> None:
    """同じファイルで 1 文か見出しだけが違う出し直しは、判定役に確かめ直し、否定されればまた下げて修正担当へ渡さない。
    確かめ直しの後は、否定した内容の出し直しは下げたままにし、どちらとも違う重い報告は判定役に渡さず修正候補に戻す。"""
    journal, row = _lowered(path="a.py", line=1, section="doc.md#保存")
    occurrence = row[journal_mod.OCCURRENCE]
    journal.enter(2, [_told(path="a.py", line=9, section="doc.md#保存")])
    assert (row["severity"], row["line"], journal.fresh) == ("minor", 1, [])
    journal.enter(3, [_told(**{"path": "a.py", "line": 20, "section": "doc.md#保存", **other})])
    assert (row["severity"], row["line"], row["round"], "check" in row) == ("major", 20, 3, False)
    assert journal.fresh == ["r1-1"] and journal.revived == [] and row[journal_mod.OCCURRENCE] == occurrence
    journal.apply_checks(journal.fresh, _answered())
    assert (row["severity"], journal.handable()) == ("minor", [])
    # 先に否定した内容と、確かめ直して否定した内容は、下げたままにする
    for told in ({}, other):
        journal.enter(4, [_told(**{"path": "a.py", "line": 20, "section": "doc.md#保存", **told})])
        assert (row["severity"], row["check"]["lowered"], journal.fresh) == ("minor", True, [])
    journal.enter(5, [_told(path="a.py", line=30, section="doc.md#保存", impact="別の発生条件で止まる")])
    assert (row["severity"], row["line"], journal.fresh) == ("major", 30, [])
    assert journal.handable() == [row]
