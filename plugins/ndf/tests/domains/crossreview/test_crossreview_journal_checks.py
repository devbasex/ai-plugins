"""ジャーナルの、判定役の確認で下げる・解除する規則（#4363）。"""

from __future__ import annotations

import pytest

from ndf.domains.crossreview import criteria_decide, drive, journal as journal_mod, verify_decide
from ndf.domains.crossreview import finish_decide as fin

from .test_crossreview_journal import ITEMS, V11, _row, _told

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
def test_issue_4363_lowered_finding_raised_in_another_file_is_unlowered_and_not_asked(order: int) -> None:
    """下げた指摘と同じ本文の重い報告が別のファイルで出たら、下げた扱いを解除して今回の位置の指摘にし、否定した位置は
    主にせず、判定役へ渡し直さない（交互に出ても確かめ直さない）。"""
    journal, row = _lowered(path="a.py", line=1)
    journal.enter(2, [_told(path="a.py", line=9)])
    assert (row["severity"], row["line"], journal.fresh) == ("minor", 1, [])
    other = _told(path="b.py", line=2, impact="別の経路で保存が止まる")
    journal.enter(3, [_told(path="a.py", line=1), other][::order])
    assert (row["severity"], row["path"], row["line"], row["impact"]) == ("major", "b.py", 2, "別の経路で保存が止まる")
    assert (row["check"]["lowered"], journal.fresh, journal.revived) == (False, [], ["r1-1"])
    journal.enter(4, [_told(path="a.py", line=1)])
    assert (row["severity"], journal.fresh) == ("major", [])


@pytest.mark.parametrize("order", [1, -1])
@pytest.mark.parametrize("severity, protect", [("critical", []), ("major", ["secret"])])
def test_issue_4363_report_without_a_place_does_not_lead_the_unlowered_finding(severity: str, protect: list, order: int) -> None:
    """下げた扱いを解除した指摘の代表は、解除の条件を満たした報告にする。位置の無い重い報告は、代表が保護の根拠を持つ場合も
    本文に残す。"""
    journal, row = _lowered(path="a.py", line=1)
    placeless = _told(severity=severity, impact="位置を示せない不具合")
    journal.enter(2, [placeless, _told(path="b.py", line=2, protect=protect)][::order])
    assert (row["severity"], row["path"], row["line"]) == (severity, "b.py", 2)
    assert "位置を示せない不具合" in criteria_decide.fixer_impact(row) and "位置を示せない不具合" not in row["text"]


@pytest.mark.parametrize("order", [1, -1])
def test_issue_4363_placeless_reports_all_reach_the_unlowered_finding_led_by_a_placeless_one(order: int) -> None:
    """代表が位置の無い保護対象の報告でも、発生条件の違う位置の無い重い報告を本文に残す。"""
    journal, row = _lowered(path="a.py", line=1)
    protected = _told(impact="秘密が記録に残る", protect=["secret"])
    plain = _told(impact="保存が止まる")
    journal.enter(2, [plain, protected, _told(path="b.py", line=2)][::order])
    assert (row.get("path"), row["impact"]) == (None, "秘密が記録に残る")
    assert "保存が止まる" in criteria_decide.fixer_impact(row) and "保存が止まる" not in row["text"]


@pytest.mark.parametrize("order", [1, -1])
def test_issue_4363_denied_report_does_not_weigh_on_the_unlowered_finding(order: int) -> None:
    """確認で否定したファイルの根拠の無い報告は、解除するラウンドの重要度に重ねない。"""
    journal, row = _lowered(path="a.py", line=1)
    journal.enter(2, [_told(severity="critical", path="a.py", line=1), _told(path="b.py", line=2)][::order])
    assert (row["severity"], row["raised_severity"], row["path"]) == ("major", "major", "b.py")


def test_issue_4363_protected_report_in_another_file_gets_the_first_severity_back() -> None:
    journal, row = _lowered(severity="critical", path="a.py")
    journal.enter(2, [_told(path="b.py", protect=["secret"])])
    assert (row["severity"], row["path"], row["protect"]) == ("critical", "b.py", ["secret"])


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
def test_issue_4363_lowered_finding_raised_for_another_condition_in_the_same_file_is_unlowered(other: dict) -> None:
    journal, row = _lowered(path="a.py", line=1, section="doc.md#保存")
    journal.enter(2, [_told(path="a.py", line=9, section="doc.md#保存")])
    assert (row["severity"], row["line"]) == ("minor", 1)
    journal.enter(3, [_told(**{"path": "a.py", "line": 20, "section": "doc.md#保存", **other})])
    assert (row["severity"], row["line"], row["check"]["lowered"]) == ("major", 20, False)
    assert journal.handable() == [row] and journal.fresh == []
