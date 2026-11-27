"""判定役の判断（verify_decide）。確かめる指摘の選び方・指示・回答の読み方・下げるかの決め方（#4363）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.contracts import writing
from ndf.domains.crossreview import criteria_decide, drive, report_decide, verify_decide

NDF_BY = "ndf"


def _row(fid: str = "r1-1", **other) -> dict:
    return {"id": fid, "severity": "major", "text": "保存が止まる", "by": "codex", **other}


def _answer(fid: str = "r1-1", holds: object = False, **other) -> dict:
    base = {"id": fid, "holds": holds, "condition": "", "path": "", "result": "", "reason": "呼ぶ経路が無い", "tracks": []}
    return {**base, **other}


def _verdict(holds=False, tracks=(), unknown=False) -> verify_decide.Verdict:
    return verify_decide.Verdict("r1-1", holds, reason="呼ぶ経路が無い", tracks=tuple(tracks), unknown=unknown)


@pytest.mark.parametrize("use_tracks", [False, True])
def test_issue_4363_select_passes_ordinary_findings_and_never_redline_or_ndf(use_tracks: bool) -> None:
    rows = [_row("a"), _row("b", redline=True, mvv="P1"), _row("c", by=NDF_BY), _row("d", mvv="Value 5"), _row("e", protect=["secret"])]
    assert [r["id"] for r in verify_decide.select(rows, use_tracks)] == ["a", "d", "e"]
    assert verify_decide.select(rows[1:3], use_tracks) == []


@pytest.mark.parametrize("marks", [{"mvv": "Value 5"}, {"protect": ["secret"]}])
def test_issue_4363_select_passes_marked_findings_alone_only_for_their_tracks(marks: dict) -> None:
    """下がらない指摘だけのラウンドは、回答の種類を使う処理があるときだけ判定役へ渡す。"""
    rows = [_row(**marks)]
    assert verify_decide.select(rows) == []
    assert verify_decide.select(rows, use_tracks=True) == rows


def test_issue_4363_instruction_lists_the_findings_without_their_author() -> None:
    rows = [
        _row("r2-1", by="kiro-unique", impact="入力が消える", path="src/a.py", line=10),
        _row("r2-2", severity="critical", text="二つ目", section="a.md#見出し"),
        _row("r2-3", text="三つ目"),
    ]
    criteria = ["1. 基準の行"]
    text = verify_decide.instruction(rows, Path("/in/round2.diff"), Path("/work/tree"), criteria)
    for part in ("r2-1", "r2-2", "r2-3", "保存が止まる", "critical", "入力が消える", "src/a.py", "10", "a.md#見出し"):
        assert part in text
    for part in ("/in/round2.diff", "/work/tree", "1. 基準の行", "verdicts"):
        assert part in text
    assert all(kind in text for kind in criteria_decide.track_kinds())
    assert "kiro-unique" not in text


def test_issue_4363_read_takes_the_valid_answers() -> None:
    shown = _answer("a", True, condition="保存", path="a.py:1", result="消える", reason="", tracks=["delivered", "authz"])
    answers = verify_decide.read([shown, _answer("b")], ["a", "b"])
    assert answers.warnings == ()
    a, b = answers.verdicts["a"], answers.verdicts["b"]
    assert (a.holds, a.condition, a.path, a.result, a.unknown) == (True, "保存", "a.py:1", "消える", False)
    assert set(a.tracks) == {"authz", "delivered"}
    assert (b.holds, b.reason, b.tracks) == (False, "呼ぶ経路が無い", ())


def test_issue_4363_read_keeps_the_tracks_of_an_undecided_answer_and_warns() -> None:
    answers = verify_decide.read([_answer("a", None, reason="資料が無い", tracks=["secret"])], ["a"])
    assert (answers.verdicts["a"].holds, answers.verdicts["a"].tracks) == (None, ("secret",))
    assert len(answers.warnings) == 1 and "a" in answers.warnings[0]


def test_issue_4363_read_keeps_the_judges_free_text_out_of_the_public_comment() -> None:
    """判定役が読んだ資料の内容（作り物の値）は、理由と番号のどちらに写されても記録のコメントに出ない。"""
    private = "sample-corp-internal-plan"
    found = [_answer("a", None, reason=private), _answer(private)]
    warnings = verify_decide.read(found, ["a"]).warnings
    body = report_decide.comment(1, {}, None, warnings)
    assert warnings and private not in body


def test_issue_4363_the_reason_of_a_lowered_finding_stays_out_of_the_public_comment() -> None:
    """下げた指摘の `check.reason`（作り物の値）は、記録のコメントのどこにも出ない。"""
    private = "sample-corp-internal-endpoint"
    journal = drive.FindingJournal()
    journal.enter(1, [{"text": "保存が止まる", "severity": "major", "impact": "入力が消える", "by": "codex"}])
    journal.apply_checks(["r1-1"], verify_decide.read([_answer("r1-1", reason=private)], ["r1-1"]).verdicts)
    assert journal.rows["r1-1"]["check"]["reason"] == private
    body = report_decide.comment(1, {"findings": list(journal.rows.values())}, None)
    assert "保存が止まる" in body and private not in body


@pytest.mark.parametrize(
    "item",
    [
        _answer("a", True, condition="c", path="p", result=""),
        _answer("a", True, condition="c", path=" ", result="r"),
        _answer("a", True, path="p", result="r"),
        _answer("a", False, reason=" "),
        _answer("a", False, tracks=None),
        _answer("a", False, tracks="delivered"),
        {"id": "a", "holds": False, "reason": "tracks が無い"},
        _answer("a", "false"),
        _answer("a", 0),
        {"id": "a", "reason": "holds が無い"},
        _answer("z"),
    ],
)
def test_issue_4363_read_leaves_out_invalid_unknown_and_missing_answers(item: dict) -> None:
    answers = verify_decide.read([item], ["a"])
    assert answers.verdicts == {}
    assert any("a" in w for w in answers.warnings)


def test_issue_4363_read_without_an_answer_warns_for_every_finding() -> None:
    answers = verify_decide.read(None, ["a", "b"])
    assert answers.verdicts == {}
    assert len(answers.warnings) == 1 and "a" in answers.warnings[0] and "b" in answers.warnings[0]


@pytest.mark.parametrize("first", [_answer("a"), _answer("a", tracks=None)])
def test_issue_4363_read_does_not_decide_a_repeated_id_and_keeps_every_track(first: dict) -> None:
    answers = verify_decide.read([first, _answer("a", tracks=["user_data"]), _answer("a", True, tracks=["secret"])], ["a"])
    assert (answers.verdicts["a"].holds, answers.verdicts["a"].tracks) == (None, ("secret", "user_data"))
    assert len(answers.warnings) == 1 and "a" in answers.warnings[0]


@pytest.mark.parametrize("tracks, unknown", [(["delivered", "速さ"], True), ([1], True), ([], False)])
def test_issue_4363_read_marks_tracks_outside_the_table(tracks: object, unknown: bool) -> None:
    assert verify_decide.read([_answer("a", tracks=tracks)], ["a"]).verdicts["a"].unknown is unknown


@pytest.mark.parametrize("holds", [False, None])
def test_issue_4363_read_keeps_a_protect_kind_written_as_one_string(holds: object) -> None:
    """#4363: `tracks` が配列でない回答は下げる根拠にせず、書かれた保護の種類は残す。"""
    verdict = verify_decide.read([_answer("a", holds, tracks="secret")], ["a"]).verdicts["a"]
    assert (verdict.holds, verdict.tracks) == (None, ("secret",))


@pytest.mark.parametrize("tracks", [(), ("delivered",), ("delivered", "approval")])
def test_issue_4363_lowers_an_ordinary_finding_that_does_not_hold(tracks: tuple) -> None:
    assert verify_decide.lowers(_row(), _verdict(tracks=tracks)) is True


@pytest.mark.parametrize(
    "row, verdict",
    [
        (_row(), _verdict(holds=True)),
        (_row(), _verdict(holds=None)),
        (_row(redline=True), _verdict()),
        (_row(mvv="Value 5"), _verdict()),
        (_row(protect=["safety"]), _verdict()),
        (_row(by=NDF_BY), _verdict()),
        *[(_row(), _verdict(tracks=(kind,))) for kind in ("secret", "authz", "user_data", "irreversible", "safety")],
        (_row(), _verdict(unknown=True)),
    ],
)
def test_issue_4363_lowers_nothing_else(row: dict, verdict: verify_decide.Verdict) -> None:
    assert verify_decide.lowers(row, verdict) is False


def test_issue_4363_document_line_is_the_sixth_criterion_only_for_documents() -> None:
    assert criteria_decide.document_line(False) == []
    line, *excerpt = criteria_decide.document_line(True)
    assert line.startswith("6. ")
    assert writing.types_section() in excerpt
    assert criteria_decide.track_kinds()[: len(criteria_decide.protect_kinds())] == criteria_decide.protect_kinds()
    assert criteria_decide.track_kinds()[len(criteria_decide.protect_kinds()) :] == ("delivered", "approval")
