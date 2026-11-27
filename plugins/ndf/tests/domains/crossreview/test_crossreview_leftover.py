"""2 ラウンド目以降の線引き（#3780）。修正へ進む指摘と直さずに残す指摘の分け方、ジャーナルの `parked`、変えた行の読み取り、
`ndf cross-review run` の組み込み（相手役は偽物、git は一時の origin で本物を打つ）。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ndf.adapters import git
from ndf.domains.crossreview import changes, criteria_decide, drive, journal as journal_mod, leftover_decide, round_scope, threads
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview.leftover_decide import PARKED, Touched

from ._judge import holding
from ._repos import Repos
from .test_crossreview_journal import ITEMS, V11, _told
from .test_crossreview_mvv import _push_base, mvv_state  # noqa: F401
from .test_crossreview_ranges import _read_against_target
from .test_crossreview_run import IMPACT, Fake, _run, _state
from .test_crossreview_scope import _head, _lines, _write

# a.py の 10 行目だけを変えたラウンド（PR の差分には 2〜13 行目が載る）
TOUCHED = Touched(frozenset({"a.py"}), {"a.py": frozenset({10})}, {"a.py": frozenset(range(2, 14))})
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


def test_a_dot_slash_path_is_saved_without_it() -> None:
    journal = journal_mod.FindingJournal()
    journal.enter(2, [_told(path="./a.py", line=10)])
    journal.sort(TOUCHED)
    assert journal.rows["r2-1"]["path"] == "a.py"


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


@pytest.mark.parametrize(
    ("commentable", "line"),
    [pytest.param(frozenset({5}), 5, id="in-pr-diff"), pytest.param(frozenset(), None, id="outside-pr-diff")],
)
def test_sorting_prefers_a_line_over_a_file_only_report_only_when_it_takes_a_comment(commentable, line) -> None:
    journal = journal_mod.FindingJournal()
    journal.enter(2, [_told(path="a.py"), _told(**UNCHANGED)])
    assert journal.sort(TOUCHED._replace(commentable={"a.py": commentable}), {"r2-1"}) == frozenset()
    assert threads.location(journal.rows["r2-1"]) == ("a.py", line)


@pytest.mark.parametrize(
    ("reports", "line"),
    [pytest.param((10, 5), 5, id="postable-over-touched"), pytest.param((10,), None, id="touched-outside-pr-diff")],
)
def test_sorting_never_places_a_line_outside_the_pr_diff(reports: tuple[int, ...], line) -> None:
    journal = journal_mod.FindingJournal()
    journal.enter(2, [_told(path="a.py", line=n) for n in reports])
    # 5 行目は変えていない行なので、判定役の回答が無い扱いにして、両方の出し直しを進む側にする
    assert journal.sort(TOUCHED._replace(commentable={"a.py": frozenset({5})}), {"r2-1"}) == frozenset()
    assert threads.location(journal.rows["r2-1"]) == ("a.py", line)


@pytest.mark.parametrize(
    ("others", "line"),
    [pytest.param((), None, id="file-only"), pytest.param((7,), 7, id="another-report")],
)
def test_sorting_replaces_an_unparking_line_outside_the_pr_diff(others: tuple[int, ...], line) -> None:
    journal, row = _parked()
    journal.enter(3, [_told(severity="critical", path="a.py", line=99), *(_told(path="a.py", line=n) for n in others)])
    journal.sort(TOUCHED)
    assert (threads.location(row), row[journal_mod.PLACED_ROUND]) == (("a.py", line), 3)
    assert row["line"] == (line or 99)


@pytest.mark.parametrize(("number", "line"), [(5, 5), (99, None)])
def test_sorting_checks_the_line_the_thread_reads_from_the_text(number: int, line) -> None:
    journal = journal_mod.FindingJournal()
    journal.enter(2, [_told(f"a.py:{number} が落ちる", severity="critical", section="a.py#f")])
    journal.sort(TOUCHED)
    assert threads.location(journal.rows["r2-1"]) == ("a.py", line)


@pytest.mark.parametrize("sorted_", [pytest.param(True, id="sorted"), pytest.param(False, id="not-sorted")])
def test_a_parked_line_outside_the_pr_diff_is_not_posted_when_it_proceeds_without_a_path(sorted_: bool) -> None:
    journal = journal_mod.FindingJournal()
    journal.enter(2, [_told(path="a.py", line=99)])
    journal.sort(TOUCHED)
    row = journal.rows["r2-1"]
    assert (row[PARKED], row["line"]) == (True, 99)
    journal.enter(3, [_told(severity="critical")])
    if sorted_:
        journal.sort(TOUCHED)
    assert PARKED not in row and row["line"] == 99
    assert threads.location(row) == ("a.py", None)


def test_the_thread_commit_of_a_moved_finding_is_the_head_of_the_placed_round() -> None:
    rounds = [{"round": 2, "head": "old"}, {"round": 3, "head": "new"}]
    assert threads._placed_head(rounds, {"round": 2}) == "old"
    assert threads._placed_head(rounds, {"round": 2, journal_mod.PLACED_ROUND: 3}) == "new"


# --- 変えた行 ------------------------------------------------------------------------------


def test_issue_3780_5_touched_since_has_added_rewritten_and_the_neighbours_of_removed_lines(wt: Path) -> None:
    since = _write(wt, {"a.txt": _lines(), "gone.txt": "x\n", "blob.bin": b"\0one"}, "前のラウンド")
    text = _lines((3, "書き換えた")).replace("line 20\n", "").replace("line 10\n", "line 10\n足した\n")
    _write(wt, {"a.txt": text, "gone.txt": None, "blob.bin": b"\0two", "new.txt": "a\nb\n"}, "直し")
    got = changes.touched_since(git.Git.offline(wt), since, changes.Range(since, _head(wt)), {})
    assert got.files == frozenset({"a.txt", "gone.txt", "blob.bin", "new.txt"})
    # 3 は書き換え、11 は追加、20・21 は消した行（元の 20 行目）の前後
    assert got.lines["a.txt"] == frozenset({3, 11, 20, 21})
    assert got.lines["new.txt"] == frozenset({1, 2})
    assert "blob.bin" not in got.lines
    # PR の差分に載る行は、変えた行と前後 3 行
    assert {1, 3, 6, 11} <= got.commentable["a.txt"] and 30 not in got.commentable["a.txt"]


def test_touched_since_does_not_read_the_line_diff_it_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    """比較基点が起点と同じラウンドは、`since_diff` の返りを渡して `since..head` の行の差分を読み直さない。"""
    spans: list[str] = []
    monkeypatch.setattr(git.Git, "run_bytes", lambda self, *args: spans.append(args[-1]) or b"")
    text = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
    listed: changes.Listings = {"h1..h2": ({}, frozenset())}
    got = changes.touched_since(git.Git(), "h1", changes.Range("b", "h2"), listed, text)
    assert (got.lines, spans) == ({"a.py": frozenset({1})}, ["b...h2"])


@pytest.mark.parametrize(("name", "spans"), [("a.py", []), ("\ufffd.py", ["h1..h2", "b...h2"])])
def test_touched_since_reads_only_the_diffs_it_cannot_use(monkeypatch: pytest.MonkeyPatch, name: str, spans: list[str]) -> None:
    read: list[str] = []
    monkeypatch.setattr(git.Git, "run_bytes", lambda self, *args: read.append(args[-1]) or b"")
    text = f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n@@ -1 +1 @@\n-x\n+y\n"
    changes.touched_since(git.Git(), "h1", changes.Range("b", "h2"), {"h1..h2": ({}, frozenset())}, text, text)
    assert read == spans


def test_touched_since_a_commit_that_is_not_local_raises(wt: Path) -> None:
    with pytest.raises(git.GitError):
        changes.touched_since(git.Git.offline(wt), "0" * 40, changes.Range("0" * 40, _head(wt)), {})


# --- git と判定役を起動する条件 --------------------------------------------------------------


def test_changed_lines_are_needed_only_for_findings_to_sort() -> None:
    journal, _ = _parked()
    assert journal.sortable()
    journal.enter(3, [], ITEMS, V11)
    assert not journal.sortable()


@pytest.mark.parametrize(("returned", "wanted"), [pytest.param([], True, id="new"), pytest.param(["r2-1"], False, id="returned")])
def test_the_judge_is_not_asked_the_kinds_of_a_returned_finding(returned: list[str], wanted: bool) -> None:
    journal = journal_mod.FindingJournal()
    journal.enter(2, [_told(mvv="Value 1", **UNCHANGED)], ITEMS, V11)
    journal.returned = returned
    assert drive._wants_tracks(SimpleNamespace(journal=journal), TOUCHED) is wanted  # type: ignore[arg-type]


@pytest.mark.parametrize(("confirmed", "asked"), [pytest.param("h1", 0, id="same-head"), pytest.param("h0", 1, id="other-head")])
def test_line_origin_does_not_confirm_the_confirmed_head_again(confirmed: str, asked: int) -> None:
    calls: list[tuple[str, str]] = []
    g = SimpleNamespace(is_ancestor=lambda prev, head: calls.append((prev, head)) or True)
    state = {"rounds": [{"round": 1, "base": "b", "head": "h1", "verdict": "changes_required"}]}
    assert round_scope.line_origin(g, state, "h2", confirmed) == changes.Range("b", "h1")  # type: ignore[arg-type]
    assert len(calls) == asked


# --- 駆動（`run` を通す） --------------------------------------------------------------------


def _said(text: str, path: str = "doc.md", line: int = 1, severity: str = "major", **other) -> dict:
    """相手役の出力の指摘 1 件。既定の位置（`doc.md` の 1 行目）は、どのラウンドの修正も変えない行。"""
    return {"text": text, "severity": severity, "impact": IMPACT, "path": path, "line": line, **other}


def _rounds(tmp_path: Path, capsys, monkeypatch, wt: Path, *said: list, extra: tuple[str, ...] = ()) -> tuple[Fake, dict, dict]:
    """codex が `said` の順に指摘する `run`。1 ラウンド目は `doc.md` の指摘で、偽の修正担当は `fix0.txt` を足す。"""
    fake = Fake(tmp_path / "st", {"codex": [[_said("初めの指摘")], *said], "kiro": []})
    code, got = _run(capsys, monkeypatch, fake, wt, *extra)
    assert code == 0, got
    return fake, got, _state(fake.state_dir)


def _parked_texts(state: dict) -> list[str]:
    return [f["text"] for f in state["findings"] if f.get(PARKED)]


def test_issue_3780_2_only_unchanged_majors_and_minors_in_round_two_converge(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    second = [_said("古い行の指摘"), _said("細かい指摘", line=3, severity="minor")]
    fake, got, state = _rounds(tmp_path, capsys, monkeypatch, wt, second)
    assert (got["status"], got["outputs"]["gaps"]) == ("ok", [])
    assert [r["verdict"] for r in state["rounds"]] == ["changes_required", fin.CONVERGED]
    # 修正担当は 1 ラウンド目だけで、1 ラウンド目は変えていない行でも直す
    assert fake.calls.count("claude") == 1
    assert _parked_texts(state) == ["古い行の指摘"]
    assert "未解決 0" in got["summary"]
    assert not [body for *_, body in hub.port.opened if "古い行の指摘" in body]


@pytest.mark.parametrize(
    "finding",
    [
        pytest.param(_said("変えた行の指摘", path="fix0.txt"), id="changed-line"),
        pytest.param(_said("critical の指摘", severity="critical"), id="critical"),
        pytest.param(_said("秘密の指摘", protect=["secret"]), id="secret"),
    ],
)
def test_issue_3780_5_round_two_findings_that_proceed_are_fixed(wt: Path, tmp_path: Path, capsys, monkeypatch, finding: dict) -> None:
    fake, got, state = _rounds(tmp_path, capsys, monkeypatch, wt, [finding])
    assert got["status"] == "ok" and len(state["rounds"]) == 3
    assert fake.calls.count("claude") == 2 and _parked_texts(state) == []


def test_issue_3780_5_a_protected_major_alone_does_not_launch_the_judge(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    fake, _, _ = _rounds(tmp_path, capsys, monkeypatch, wt, [_said("秘密の指摘", protect=["secret"])])
    assert len(fake.judged) == 1 and fake.calls.count("claude") == 2


def test_issue_3780_a_design_pr_is_not_sorted(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    fake, _, state = _rounds(tmp_path, capsys, monkeypatch, wt, [_said("古い行の指摘")], extra=("--purpose", "design"))
    assert fake.calls.count("claude") == 2 and _parked_texts(state) == []


def test_issue_3780_unreadable_changed_lines_let_every_finding_proceed(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise git.GitError("差分を読めない")

    monkeypatch.setattr(changes, "touched_since", fail)
    fake, got, state = _rounds(tmp_path, capsys, monkeypatch, wt, [_said("古い行の指摘")])
    assert fake.calls.count("claude") == 2 and _parked_texts(state) == []
    assert any("線引き" in w for w in got["warnings"])


def test_the_changed_lines_are_read_from_the_handed_diff(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """比較基点が起点と同じラウンドは、相手役へ渡した差分を `since..head` の行の差分として渡し、読み直さない。"""
    given: list[tuple] = []
    read = changes.touched_since

    def spy(g, since, current, listed, since_lines=None, pr_lines=None):
        given.append((since, since_lines, pr_lines))
        return read(g, since, current, listed, since_lines, pr_lines)

    monkeypatch.setattr(changes, "touched_since", spy)
    fake, _, state = _rounds(tmp_path, capsys, monkeypatch, wt, [_said("古い行の指摘")])
    assert given == [(state["rounds"][0]["head"], fake.inputs["round2.diff"], None)]


def test_a_parked_finding_is_not_counted_by_the_oscillation(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    old = _said("古い行の指摘")
    said = (
        [old, _said("変えた行の一つ目", path="fix0.txt")],
        [_said("変えた行の二つ目", path="fix1.txt")],
        [old, _said("変えた行の三つ目", path="fix2.txt")],
    )
    _, got, state = _rounds(tmp_path, capsys, monkeypatch, wt, *said, extra=("--max-rounds", "6"))
    assert [r["verdict"] for r in state["rounds"]] == ["changes_required"] * 4 + [fin.CONVERGED]
    assert got["status"] == "ok" and _parked_texts(state) == ["古い行の指摘"]


@pytest.mark.parametrize(("tracks", "fixes"), [pytest.param([], 1, id="parked"), pytest.param(["safety"], 2, id="protected")])
def test_issue_3780_the_judge_is_asked_the_kinds_of_an_unchanged_mvv_major(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, tracks: list[str], fixes: int
) -> None:
    """変えていない行の MVV の major は下げないが、回答の指摘の種類で進むかが変わるので判定役へ渡す。"""
    _push_base(repos)
    fake = Fake(tmp_path / "st", {"codex": [[_said("初めの指摘")], [_said("古い行の指摘", mvv="Value 1")]], "kiro": []})
    fake.verdict = lambda row: holding(row) | {"tracks": tracks if row["text"] == "古い行の指摘" else []}
    code, got = _run(capsys, monkeypatch, fake, wt, "--base", "main")
    assert code == 0, got
    assert len(fake.judged) == 2 and fake.calls.count("claude") == fixes


@pytest.mark.parametrize(("base", "fixes"), [pytest.param("origin/target", 0, id="same-base"), pytest.param("origin/main", 1, id="added")])
def test_issue_3780_a_base_change_that_adds_files_is_not_sorted(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, base: str, fixes: int
) -> None:
    """比較先を変えて新しく PR の変更になったファイルがあるラウンドは、線引きしない。"""
    state_dir, _ = _read_against_target(repos, wt, tmp_path, capsys, monkeypatch)
    fake = Fake(state_dir, {"codex": [[_said("古い行の指摘")]], "kiro": []})
    code, got = _run(capsys, monkeypatch, fake, wt, "--base", base)
    assert code == 0, got
    assert fake.calls.count("claude") == fixes
