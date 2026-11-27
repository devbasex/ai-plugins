"""2 ラウンド目以降の線引き（#3780）。修正へ進む指摘と直さずに残す指摘の分け方、ジャーナルの `parked`、変えた行の読み取り。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.adapters import git
from ndf.domains.crossreview import changes, criteria_decide, journal as journal_mod, leftover_decide, threads
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview.leftover_decide import PARKED, Touched

from .test_crossreview_journal import ITEMS, V11, _told
from .test_crossreview_scope import _head, _lines, _write

# a.py の 10 行目だけを変えたラウンド
TOUCHED = Touched(frozenset({"a.py"}), {"a.py": frozenset({10})})
UNCHANGED = {"path": "a.py", "line": 5}


def _parked(**other) -> tuple[journal_mod.FindingJournal, dict]:
    """変えていない行の major を直さずに残したジャーナルと、その行。"""
    journal = journal_mod.FindingJournal()
    journal.enter(2, [_told(**(UNCHANGED | other))], ITEMS, V11)
    journal.sort(TOUCHED)
    row = journal.rows["r2-1"]
    assert row[PARKED] is True
    return journal, row


def _waits_for_a_human(journal: journal_mod.FindingJournal, row: dict) -> None:
    """直さずに残さず、未解決のまま人の判断を待つ（修正担当へも渡さない）。"""
    assert PARKED not in row
    assert (row["redline"], row["outcome"]) == (True, fin.UNRESOLVED)
    assert journal.handable() == []


# --- 必ず人の承認が要る操作の指摘は直さずに残さない -------------------------------------------


def test_issue_3780_5_redline_with_a_filed_kind_on_an_unchanged_line_is_never_parked() -> None:
    journal = journal_mod.FindingJournal()
    journal.enter(2, [_told(mvv="P1", **UNCHANGED)], ITEMS, V11)
    journal.rows["r2-1"]["check"] = {"tracks": ["delivered"]}
    assert journal.sort(TOUCHED) == frozenset()
    _waits_for_a_human(journal, journal.rows["r2-1"])


def test_issue_3780_5_redline_raised_on_a_checked_finding_is_not_parked() -> None:
    journal = journal_mod.FindingJournal()
    journal.enter(2, [_told(**UNCHANGED)], ITEMS, V11)
    journal.rows["r2-1"]["check"] = {"tracks": ["delivered"]}
    journal.enter(3, [_told(mvv="P1", **UNCHANGED)], ITEMS, V11)
    assert journal.sort(TOUCHED) == frozenset()
    _waits_for_a_human(journal, journal.rows["r2-1"])


def test_issue_3780_5_redline_raised_on_a_parked_finding_unparks_it() -> None:
    journal, row = _parked()
    journal.enter(3, [_told(mvv="P1", **UNCHANGED)], ITEMS, V11)
    # 線引きをしないラウンドでも外れる
    _waits_for_a_human(journal, row)
    assert journal.sort(TOUCHED) == frozenset()
    _waits_for_a_human(journal, row)


def test_issue_3780_5_redline_row_proceeds_even_when_marked_parked() -> None:
    row = {"round": 2, "by": "codex", "severity": "major", "redline": True, PARKED: True, **UNCHANGED}
    assert leftover_decide.proceeds(row | {"check": {"tracks": ["delivered"]}}, TOUCHED)


# --- 線引き --------------------------------------------------------------------------------


def _row(**other) -> dict:
    return {"round": 2, "by": "codex", "severity": "major", **UNCHANGED} | other


@pytest.mark.parametrize(
    ("row", "want"),
    [
        pytest.param(_row(), False, id="unchanged-line"),
        pytest.param(_row(severity="critical"), True, id="critical"),
        pytest.param(_row(line=10), True, id="changed-line"),
        pytest.param(_row(path="./a.py", line=10), True, id="dot-slash-path"),
        pytest.param(_row(path="b.py", line=10), False, id="same-line-of-another-file"),
        pytest.param(_row(severity="minor"), True, id="minor"),
        pytest.param(_row(severity="nit"), True, id="nit"),
        pytest.param(_row(by=journal_mod.BY_NDF), True, id="made-by-ndf"),
        pytest.param(_row(round=0), True, id="carried"),
        pytest.param(_row(protect=["safety"]), True, id="protect-safety"),
        pytest.param(_row(check={"tracks": ["user_data"]}), True, id="tracked-protect-kind"),
        pytest.param(_row(check={"tracks": ["delivered", "approval"]}), False, id="tracked-other-kinds"),
        pytest.param({k: v for k, v in _row().items() if k != "line"}, True, id="no-line-changed-file"),
        pytest.param({"round": 2, "by": "codex", "severity": "major", "path": "b.py"}, False, id="no-line-unchanged-file"),
        pytest.param({"round": 2, "by": "codex", "severity": "major"}, True, id="no-path"),
    ],
)
def test_issue_3780_5_proceeds(row: dict, want: bool) -> None:
    assert leftover_decide.proceeds(row, TOUCHED) is want


@pytest.mark.parametrize("kind", criteria_decide.protect_kinds())
def test_issue_3780_5_every_protect_kind_proceeds_on_an_unchanged_line(kind: str) -> None:
    assert leftover_decide.proceeds(_row(protect=[kind]), TOUCHED)


def test_issue_3780_returned_and_unconfirmed_findings_proceed() -> None:
    assert leftover_decide.proceeds(_row(), TOUCHED, returned=True)
    assert leftover_decide.proceeds(_row(), TOUCHED, unconfirmed=True)


def test_finding_kinds_joins_the_protect_kinds_and_the_tracks() -> None:
    row = {"protect": ["secret"], "check": {"tracks": ["secret", "delivered"]}}
    assert leftover_decide.finding_kinds(row) == frozenset({"secret", "delivered"})
    assert leftover_decide.finding_kinds({}) == frozenset()


def test_normalized_folds_spaces_and_case_and_keeps_the_whole_text() -> None:
    assert leftover_decide.normalized("  Foo\n\t BAR ") == leftover_decide.normalized("foo bar") == "foo bar"
    long = "あ" * 500
    assert leftover_decide.normalized(long + "x") != leftover_decide.normalized(long + "y")


# --- ジャーナル -----------------------------------------------------------------------------


def test_issue_3780_5_sort_parks_a_major_on_an_unchanged_line_and_keeps_the_rest_pending() -> None:
    journal = journal_mod.FindingJournal()
    raised = [
        _told("変えていない行", **UNCHANGED),
        _told("変えた行", path="a.py", line=10),
        _told("critical", severity="critical", **UNCHANGED),
        _told("秘密", protect=["secret"], **UNCHANGED),
    ]
    keys = journal.enter(2, raised)
    parked = journal.sort(TOUCHED)
    assert parked == frozenset({journal.rows["r2-1"]["key"]}) and parked < keys
    assert [f["text"] for f in journal.pending([])[0]] == ["変えた行", "critical", "秘密"]
    assert journal.rows["r2-1"]["outcome"] == fin.UNRESOLVED


@pytest.mark.parametrize(
    "again",
    [
        pytest.param({"severity": "critical", **UNCHANGED}, id="critical"),
        pytest.param({"path": "a.py", "line": 10}, id="changed-line"),
        pytest.param({"protect": ["authz"], **UNCHANGED}, id="protect"),
    ],
)
def test_issue_3780_5_parked_finding_raised_again_is_unparked(again: dict) -> None:
    journal, row = _parked()
    keys = journal.enter(3, [_told(**again)])
    assert row["key"] in keys
    assert journal.sort(TOUCHED) == frozenset()
    assert PARKED not in row and journal.handable() == [row]
    assert (row["path"], row["line"]) == (again["path"], again["line"])


def test_issue_3780_any_report_of_the_round_on_a_changed_line_proceeds() -> None:
    journal = journal_mod.FindingJournal()
    journal.enter(2, [_told(**UNCHANGED), _told(path="a.py", line=10)])
    assert journal.sort(TOUCHED) == frozenset()


@pytest.mark.parametrize(
    "again",
    [pytest.param({"severity": "critical"}, id="critical"), pytest.param({"protect": ["user_data"]}, id="protect")],
)
def test_issue_3780_5_parked_finding_raised_again_is_unparked_without_sorting(again: dict) -> None:
    journal, row = _parked()
    # 通常の報告が先に届いても、見送りを解いた報告の位置が残る
    journal.enter(3, [_told(**UNCHANGED), _told(path="a.py", line=7, **again)])
    assert journal.sort(TOUCHED) == frozenset()
    assert PARKED not in row and journal.handable() == [row]
    assert (row["path"], row["line"]) == ("a.py", 7)
    assert (row["round"], row[journal_mod.PLACED_ROUND]) == (2, 3)


def test_a_parked_finding_unparked_by_sorting_keeps_its_round_and_places_the_line_in_the_current_one() -> None:
    journal, row = _parked()
    journal.enter(3, [_told(path="a.py", line=10)])
    journal.sort(TOUCHED)
    assert (row["round"], row["line"], row[journal_mod.PLACED_ROUND]) == (2, 10, 3)


def test_a_new_finding_takes_the_place_of_the_report_on_a_changed_line() -> None:
    journal = journal_mod.FindingJournal()
    journal.enter(2, [_told(**UNCHANGED), _told(path="a.py", line=10)])
    journal.sort(TOUCHED)
    row = journal.rows["r2-1"]
    assert (row["path"], row["line"]) == ("a.py", 10)


def test_parked_finding_raised_again_unconfirmed_is_unparked() -> None:
    journal, row = _parked()
    journal.enter(3, [_told(**UNCHANGED)])
    journal.sort(TOUCHED, {"r2-1"})
    assert PARKED not in row


def test_parked_finding_not_asked_to_the_judge_stays_parked() -> None:
    journal, row = _parked()
    journal.enter(3, [_told(**UNCHANGED)])
    assert journal.sort(TOUCHED) == frozenset({row["key"]})
    assert row[PARKED] is True and journal.handable() == []


def test_a_finding_that_once_proceeded_keeps_proceeding() -> None:
    journal = journal_mod.FindingJournal()
    journal.enter(2, [_told(path="a.py", line=10)])
    journal.sort(TOUCHED)
    journal.enter(3, [_told(**UNCHANGED)])
    assert journal.sort(Touched()) == frozenset()
    assert journal.handable() == [journal.rows["r2-1"]]


def test_a_new_finding_the_judge_is_not_asked_about_is_still_sorted() -> None:
    """内容の違う重い報告があって判定役へ渡さない新しい指摘も、線引きの対象である。"""
    journal = journal_mod.FindingJournal()
    journal.enter(2, [_told(**UNCHANGED), _told(path="a.py", line=6)])
    assert journal.fresh == []
    assert journal.sort(TOUCHED) == frozenset({journal.rows["r2-1"]["key"]})


def test_a_fixed_finding_coming_back_on_an_unchanged_line_proceeds() -> None:
    journal = journal_mod.FindingJournal()
    journal.enter(1, [_told(**UNCHANGED)])
    journal.apply_fix([{"id": "r1-1", "outcome": fin.FIXED}], [], 1)
    journal.enter(2, [_told(**UNCHANGED)])
    assert journal.returned == ["r1-1"] and journal.raised_ids == ["r1-1"]
    assert journal.sort(Touched()) == frozenset()
    journal.enter(3, [])
    assert journal.returned == [] and journal.raised_ids == []


@pytest.mark.parametrize("again", [pytest.param({}, id="sorted"), pytest.param({"severity": "critical"}, id="unparked-on-entry")])
def test_a_report_without_a_path_keeps_the_place_and_its_commit(again: dict) -> None:
    journal, row = _parked()
    journal.enter(3, [_told(**again)])
    journal.sort(TOUCHED, {"r2-1"})
    assert PARKED not in row
    assert (row["path"], row["line"]) == ("a.py", 5) and journal_mod.PLACED_ROUND not in row


def test_a_parked_finding_unparked_by_the_approval_item_takes_the_place_of_that_report() -> None:
    journal, row = _parked()
    journal.enter(3, [_told(**UNCHANGED), _told(mvv="P1", path="a.py", line=7)], ITEMS, V11)
    _waits_for_a_human(journal, row)
    assert (row["round"], row["line"], row[journal_mod.PLACED_ROUND]) == (2, 7, 3)


@pytest.mark.parametrize("sorted_", [pytest.param(True, id="sorted"), pytest.param(False, id="not-sorted")])
def test_an_unparking_report_without_a_path_takes_the_place_of_another_report(sorted_: bool) -> None:
    journal, row = _parked()
    journal.enter(3, [_told(severity="critical"), _told(path="a.py", line=7)])
    if sorted_:
        journal.sort(TOUCHED)
    assert PARKED not in row
    assert (row["line"], row[journal_mod.PLACED_ROUND]) == (7, 3)


@pytest.mark.parametrize(
    "first",
    [pytest.param({}, id="no-path"), pytest.param({"severity": "critical", **UNCHANGED}, id="unchanged-line")],
)
def test_sorting_prefers_the_report_on_a_changed_line(first: dict) -> None:
    journal = journal_mod.FindingJournal()
    journal.enter(2, [_told(**first), _told(path="a.py", line=10)])
    assert journal.sort(TOUCHED) == frozenset()
    row = journal.rows["r2-1"]
    assert (row["path"], row["line"]) == ("a.py", 10)


@pytest.mark.parametrize(
    "first",
    [pytest.param({"severity": "critical"}, id="no-path"), pytest.param({"severity": "critical", "path": "a.py"}, id="file-only")],
)
def test_an_unparking_report_without_a_line_takes_the_line_of_another_report(first: dict) -> None:
    journal, row = _parked()
    journal.enter(3, [_told(**first), _told(path="a.py"), _told(path="a.py", line=7)])
    assert (row["path"], row["line"]) == ("a.py", 7)


def test_sorting_prefers_a_report_with_a_line_over_a_file_only_one() -> None:
    journal = journal_mod.FindingJournal()
    journal.enter(2, [_told(path="a.py"), _told(**UNCHANGED)])
    assert journal.sort(TOUCHED, {"r2-1"}) == frozenset()
    row = journal.rows["r2-1"]
    assert (row["path"], row["line"]) == ("a.py", 5)


def test_the_thread_commit_of_a_moved_finding_is_the_head_of_the_placed_round() -> None:
    rounds = [{"round": 2, "head": "old"}, {"round": 3, "head": "new"}]
    assert threads._placed_head(rounds, {"round": 2}) == "old"
    assert threads._placed_head(rounds, {"round": 2, journal_mod.PLACED_ROUND: 3}) == "new"


# --- 変えた行 ------------------------------------------------------------------------------


def test_issue_3780_5_touched_since_has_added_rewritten_and_the_neighbours_of_removed_lines(wt: Path) -> None:
    since = _write(wt, {"a.txt": _lines(), "gone.txt": "x\n", "blob.bin": b"\0one"}, "前のラウンド")
    text = _lines((3, "書き換えた")).replace("line 20\n", "").replace("line 10\n", "line 10\n足した\n")
    _write(wt, {"a.txt": text, "gone.txt": None, "blob.bin": b"\0two", "new.txt": "a\nb\n"}, "直し")
    got = changes.touched_since(git.Git.offline(wt), since, _head(wt), {})
    assert got.files == frozenset({"a.txt", "gone.txt", "blob.bin", "new.txt"})
    # 3 は書き換え、11 は追加、20・21 は消した行（元の 20 行目）の前後
    assert got.lines["a.txt"] == frozenset({3, 11, 20, 21})
    assert got.lines["new.txt"] == frozenset({1, 2})
    assert "blob.bin" not in got.lines


def test_touched_since_a_commit_that_is_not_local_raises(wt: Path) -> None:
    with pytest.raises(git.GitError):
        changes.touched_since(git.Git.offline(wt), "0" * 40, _head(wt), {})
