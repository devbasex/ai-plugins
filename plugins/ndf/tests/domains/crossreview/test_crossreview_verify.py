"""判定役の判断（verify_decide）・起動（verify.ask）・駆動への組み込み（#4363）。判断は確かめる指摘の選び方・指示・回答の
読み方・下げるかの決め方。駆動は `ndf cross-review run` を偽の起動（`test_crossreview_run.Fake`）で通す。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from ndf.adapters import agents
from ndf.contracts import writing
from ndf.core.converge import participants
from ndf.domains.crossreview import criteria_decide, report_decide, verify, verify_decide
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.records import mvv
from ndf.domains.records import mvv_decide as md

from ._judge import holding
from .test_crossreview_run import FAIL, IMPACT, Fake, _run, _state

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
    listed = verify_decide.listing(rows)
    for part in ("r2-1", "r2-2", "r2-3", "保存が止まる", "critical", "入力が消える", "src/a.py", "10", "a.md#見出し"):
        assert part in listed
    text = verify_decide.instruction(Path("/in/findings.jsonl"), Path("/in/round2.diff"), Path("/work/tree"), criteria)
    for part in ("/in/findings.jsonl", "/in/round2.diff", "/work/tree", "1. 基準の行", "verdicts"):
        assert part in text
    assert all(kind in text for kind in criteria_decide.track_kinds())
    assert "kiro-unique" not in listed + text
    # 指示はプロセスの引数に載るので、指摘の文を書かない
    assert "保存が止まる" not in text and "入力が消える" not in text


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


@pytest.mark.parametrize(
    "documents, places, added",
    [
        (True, [{"path": "src/a.py"}, {"path": "docs/a.md"}], True),
        (True, [{"section": "a.md#見出し"}], True),
        (True, [{"path": "src/a.py"}], False),
        (False, [{"path": "docs/a.md"}], False),
    ],
)
def test_issue_4363_document_line_is_added_only_for_a_possible_document_finding(documents: bool, places: list, added: bool) -> None:
    lines = criteria_decide.document_line(documents, [_row(**place) for place in places])
    assert bool(lines) is added
    if added:
        assert lines[0].startswith("6. ") and {writing.readers_section(), writing.supplement_rules(), writing.types_section()} <= set(lines)


def test_issue_4363_track_kinds_start_with_the_protect_kinds() -> None:
    assert criteria_decide.track_kinds()[: len(criteria_decide.protect_kinds())] == criteria_decide.protect_kinds()
    assert criteria_decide.track_kinds()[len(criteria_decide.protect_kinds()) :] == ("delivered", "approval")


# --- 起動（verify.ask） ----------------------------------------------------------------


class _AskRun:
    """`verify.ask` が読む、1 回の起動の値の偽物。"""

    def __init__(self, root: Path, snapshot: Optional[mvv.Snapshot] = None):
        self.cfg = SimpleNamespace(worktree=root / "wt", settings=SimpleNamespace(focus=()))
        self.snapshot, self.documents, self.root = snapshot, False, root
        self.notes: list[tuple[int, str, str]] = []

    def round_stem(self, round_no: int, suffix: str) -> Path:
        return self.root / f"round{round_no}{suffix}"

    def note(self, round_no: int, who: str, result: str, warning: Optional[str] = None) -> None:
        self.notes.append((round_no, who, result))


def _diff(root: Path) -> Path:
    path = root / "in" / "round1.diff"
    path.parent.mkdir()
    path.write_text("diff", encoding="utf-8")
    return path


def _launcher(seen: list, verdicts: Optional[list], state: Any = agents.AgentState.DONE, denied: Optional[Path] = None):
    """起動を `seen` に残し、回答 `verdicts`（None は JSON の無い本文）を claude の出力の形で書く偽の起動。"""

    def launch(spec: agents.Launch) -> agents.AgentStatus:
        seen.append(spec)
        body: dict = {"result": "読んだ" if verdicts is None else json.dumps({"verdicts": verdicts}, ensure_ascii=False)}
        if denied is not None:
            body["permission_denials"] = [{"tool_name": "Read", "tool_input": {"file_path": str(denied)}}]
        spec.paths.out.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return agents.AgentStatus(spec.agent, state, 0, "", ())

    return launch


def _approved(root: Path) -> mvv.Snapshot:
    body, shared = root / "v1-body.md", root / "principles-p.md"
    body.write_text("# MVV の本文", encoding="utf-8")
    shared.write_text("# 共通原則", encoding="utf-8")
    return mvv.Snapshot(md.State.APPROVED, 1, "a" * 64, body, (), {}, "c" * 40, "b" * 64, shared)


def test_issue_4363_ask_launches_a_read_only_claude_once_and_returns_the_answers(tmp_path: Path) -> None:
    run, diff, seen = _AskRun(tmp_path), _diff(tmp_path), []
    # 前からある出力の名前のリンクは開かず、リンク先の権限も中身も変えない
    other = tmp_path / "other"
    other.write_text("別のファイル", encoding="utf-8")
    other.chmod(0o644)
    (tmp_path / "round1-verify.out").symlink_to(other)
    answers = verify.ask(run, 1, [_row()], diff, _launcher(seen, [holding(_row())]))
    assert answers.verdicts["r1-1"].holds is True and answers.warnings == ()
    [spec] = seen
    argv = list(spec.argv)
    assert spec.agent == participants.FIXER and spec.cwd == run.cfg.worktree
    assert argv[argv.index("--tools") + 1] == participants.READ_TOOLS and "--restricted" in argv
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"] == [str(diff.parent)]
    assert str(diff) in argv[-1] and spec.paths.out.name == "round1-verify.out"
    listed = diff.parent / "round1-verify-findings.jsonl"
    assert str(listed) in argv[-1] and not any("保存が止まる" in a for a in argv)
    assert "保存が止まる" in listed.read_text(encoding="utf-8")
    # 判定役の出力は、この起動で作った実行者だけが開けるディレクトリに置く
    held = spec.paths.out.parent
    assert held.parent == tmp_path and spec.paths.err.parent == held and held.stat().st_mode & 0o777 == 0o700
    assert (other.read_text(encoding="utf-8"), other.stat().st_mode & 0o777) == ("別のファイル", 0o644)
    assert run.notes == [(1, "verify", "判定役へ 1 件、有効な回答 1 件")]


def test_issue_4363_ask_points_the_mvv_criterion_at_the_copies_in_the_input_directory(tmp_path: Path) -> None:
    """MVV の本文と共通原則は、判定役が読める資料のディレクトリへ写してから基準 5 で指す。"""
    snap, diff, seen = _approved(tmp_path), _diff(tmp_path), []
    verify.ask(_AskRun(tmp_path, snap), 1, [_row()], diff, _launcher(seen, []))
    prompt = seen[0].argv[-1]
    for source in (snap.path, snap.principles_path):
        copied = diff.parent / source.name
        assert str(copied) in prompt and str(source) not in prompt
        assert copied.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


@pytest.mark.parametrize("how", ["failed", "denied", "no-json", "empty"])
def test_issue_4363_ask_failure_gives_no_answer_and_one_warning(tmp_path: Path, how: str) -> None:
    """起動の失敗・差分の読み取りの拒否・読めない回答・空の回答は、回答なしと注意 1 件にし、起動し直さない。"""
    diff, seen = _diff(tmp_path), []
    launch = {
        "failed": _launcher(seen, [holding(_row())], state=agents.AgentState.FAILED),
        "denied": _launcher(seen, [holding(_row())], denied=diff),
        "no-json": _launcher(seen, None),
        "empty": _launcher(seen, []),
    }[how]
    answers = verify.ask(_AskRun(tmp_path), 1, [_row()], diff, launch)
    assert answers.verdicts == {} and len(answers.warnings) == 1 and len(seen) == 1
    # 注意は PR の記録に載るので、手元の絶対パスを書かない
    assert str(tmp_path) not in answers.warnings[0]


def test_issue_4363_ask_does_not_launch_when_the_mvv_cannot_be_copied(tmp_path: Path) -> None:
    snap = _approved(tmp_path)._replace(path=tmp_path / "無い.md")
    seen: list = []
    answers = verify.ask(_AskRun(tmp_path, snap), 1, [_row()], _diff(tmp_path), _launcher(seen, []))
    assert answers.verdicts == {} and len(answers.warnings) == 1 and seen == []
    assert str(tmp_path) not in answers.warnings[0]


# --- 駆動への組み込み（ndf cross-review run） -------------------------------------------

HEAVY = {"text": "保存が止まる", "severity": "major", "impact": IMPACT}


def _broken(row: dict) -> dict:
    return _answer(row["id"])


def test_issue_4363_3_run_converges_without_the_fixer_when_the_major_does_not_hold(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    fake = Fake(tmp_path / "st", {"codex": [[HEAVY]], "kiro": [[]]})
    fake.verdict = _broken
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    assert "claude" not in fake.calls and len(fake.judged) == 1
    state = _state(fake.state_dir)
    [row] = state["findings"]
    assert (row["severity"], row["raised_severity"], row["check"]["lowered"]) == ("minor", "major", True)
    assert state["rounds"][-1]["verdict"] == fin.CONVERGED


def test_issue_4363_4_run_hands_the_major_that_holds_to_the_fixer(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    fake = Fake(tmp_path / "st", {"codex": [[HEAVY], []], "kiro": [[], []]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    assert fake.calls.count("claude") == 1 and len(fake.judged) == 1
    [row] = _state(fake.state_dir)["findings"]
    assert (row["severity"], row["outcome"], row["check"]["holds"], row["check"]["lowered"]) == ("major", fin.FIXED, True, False)


def test_issue_4363_run_keeps_the_major_and_warns_when_the_judge_fails(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    fake = Fake(tmp_path / "st", {"codex": [[HEAVY], []], "kiro": [[], []]})
    fake.judge_fails = True
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    assert fake.calls.count("claude") == 1 and len(fake.judged) == 1
    [row] = _state(fake.state_dir)["findings"]
    assert (row["severity"], "check" in row) == ("major", False)
    assert [w for w in got["warnings"] if w.startswith("round 1: ") and "判定役" in w]


@pytest.mark.parametrize("found", [{**HEAVY, "protect": ["secret"]}, {**HEAVY, "severity": "minor"}], ids=["protected-only", "light-only"])
def test_issue_4363_run_does_not_launch_the_judge_without_a_finding_to_ask(
    wt: Path, tmp_path: Path, capsys, monkeypatch, found: dict
) -> None:
    fake = Fake(tmp_path / "st", {"codex": [[found], []], "kiro": [[], []]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok") and fake.judged == []


@pytest.mark.parametrize(
    "from_codex, from_filled, diff",
    [([], "major", "round2-full.diff"), ([{**HEAVY, "text": "削除が止まる"}], "minor", "round2.diff")],
    ids=["filled-finding-asked", "filled-finding-not-asked"],
)
def test_issue_4363_run_gives_the_judge_the_full_diff_only_for_a_filled_seats_finding(
    wt: Path, tmp_path: Path, capsys, monkeypatch, from_codex: list, from_filled: str, diff: str
) -> None:
    """差分を絞るラウンドでは、埋めた席の指摘を判定役へ渡すときだけ、その席が読んだ全差分を渡す。"""
    other = {**HEAVY, "text": "読み込みが止まる", "severity": from_filled}
    fake = Fake(tmp_path / "st", {"codex": [[HEAVY], from_codex], "kiro": [[], FAIL, FAIL], "claude": [[other]]})
    _run(capsys, monkeypatch, fake, wt)
    first, second = (spec.argv[-1] for spec in fake.judged[:2])
    assert "round1.diff" in first and diff in second


def test_issue_4363_lowered_finding_is_not_counted_as_a_return(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """確認で下げた指摘は、そのラウンドの振動の判定のキーに数えない（後で保護の種類が付いて戻っても、往復と見ない）。"""
    other, third = {**HEAVY, "text": "読み込みが止まる"}, {**HEAVY, "text": "削除が止まる"}
    fake = Fake(tmp_path / "st", {"codex": [[HEAVY, other], [third], [{**HEAVY, "protect": ["user_data"]}]], "kiro": [[], [], []]})
    fake.verdict = lambda row: _broken(row) if row["text"] == HEAVY["text"] else holding(row)
    _run(capsys, monkeypatch, fake, wt)
    rounds = _state(fake.state_dir)["rounds"]
    assert [r["verdict"] for r in rounds] == ["changes_required"] * 3
