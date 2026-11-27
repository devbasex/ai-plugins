"""判定役の判断（verify_decide）・起動（verify.ask）・駆動への組み込み（#4363）。判断は確かめる指摘の選び方・指示・回答の
読み方・下げるかの決め方。駆動は `ndf cross-review run` を偽の起動（`test_crossreview_run.Fake`）で通す。"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional, Union

import pytest

from ndf.adapters import agents
from ndf.contracts import writing
from ndf.core.converge import participants
from ndf.domains.crossreview import criteria_decide, report_decide, reviewing, scope_decide, store, verify, verify_decide
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.records import mvv
from ndf.domains.records import mvv_decide as md

from ._judge import holding, lines, materials, output, read_rows
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
def test_issue_4363_select_passes_registered_findings_alone_only_for_their_tracks(marks: dict) -> None:
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
    text = verify_decide.instruction(Path("/in/findings.jsonl"), [Path("/in/round2.diff")], Path("/work/tree"), criteria)
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
        self.cfg = SimpleNamespace(worktree=root / "wt", state_dir=root, settings=SimpleNamespace(focus=()))
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


def _given(spec: agents.Launch) -> Path:
    """判定役が worktree のほかに読めるディレクトリ。"""
    [place] = [spec.argv[i + 1] for i, a in enumerate(spec.argv) if a == "--add-dir"]
    return Path(place)


def _launcher(
    seen: list,
    verdicts: Optional[list],
    state: Any = agents.AgentState.DONE,
    denied: Union[Path, str, None] = None,
    read: Optional[dict] = None,
):
    """起動を `seen` に残し、回答 `verdicts`（None は JSON の無い本文）を claude の出力の形で書く偽の起動。`denied` の文字列は
    資料のディレクトリのファイル名。資料は起動の後に消えるので、中身は起動の中で `read` へ写す。"""

    def launch(spec: agents.Launch) -> agents.AgentStatus:
        seen.append(spec)
        if read is not None:
            read.update({f.name: f.read_text(encoding="utf-8") for f in _given(spec).iterdir()})
        body: dict = {"result": "読んだ" if verdicts is None else json.dumps({"verdicts": verdicts}, ensure_ascii=False)}
        body["permission_denials"] = []
        if denied is not None:
            path = _given(spec) / denied if isinstance(denied, str) else denied
            body["permission_denials"] = [{"tool_name": "Read", "tool_input": {"file_path": str(path)}}]
        spec.paths.out.write_text(output(body, materials(spec)), encoding="utf-8")
        return agents.AgentStatus(spec.agent, state, 0, "", ())

    return launch


def _approved(root: Path) -> mvv.Snapshot:
    body, shared = root / "v1-body.md", root / "principles-p.md"
    body.write_text("# MVV の本文", encoding="utf-8")
    shared.write_text("# 共通原則", encoding="utf-8")
    return mvv.Snapshot(md.State.APPROVED, 1, "a" * 64, body, (), {}, "c" * 40, "b" * 64, shared)


def test_issue_4363_ask_launches_a_read_only_claude_once_and_returns_the_answers(tmp_path: Path) -> None:
    run, diff, seen = _AskRun(tmp_path), _diff(tmp_path), []
    # 前からある同じ名前のリンクやファイルは、開かず置き換えず、リンク先の権限も中身も変えない
    other = tmp_path / "other"
    other.write_text("別のファイル", encoding="utf-8")
    other.chmod(0o644)
    (tmp_path / "round1-verify.out").symlink_to(other)
    (tmp_path / "round1-verify.err.log").write_text("前の記録", encoding="utf-8")
    modes: list[int] = []
    read: dict[str, str] = {}
    answered = _launcher(seen, [holding(_row())], read=read)

    def launch(spec: agents.Launch) -> agents.AgentStatus:
        modes.append(spec.paths.out.parent.stat().st_mode & 0o777)
        return answered(spec)

    answers = verify.ask(run, 1, [_row()], {"r1-1": [diff]}, launch)
    assert answers.verdicts["r1-1"].holds is True and answers.warnings == ()
    [spec] = seen
    argv = list(spec.argv)
    assert spec.agent == participants.FIXER and spec.cwd == run.cfg.worktree
    assert argv[argv.index("--tools") + 1] == participants.READ_TOOLS and "--restricted" in argv
    place = _given(spec)
    assert place != diff.parent and str(place / diff.name) in argv[-1] and str(diff) not in argv[-1]
    assert spec.paths.out.name == "round1-verify.out" and not place.exists()
    assert str(place / "round1-verify-findings.jsonl") in argv[-1] and not any("保存が止まる" in a for a in argv)
    assert "保存が止まる" in read["round1-verify-findings.jsonl"] and read[diff.name] == "diff"
    # 出力はこの起動の間だけの実行者だけが開けるディレクトリに書かせ、起動ごとの名前で状態の保存先へ新規作成する
    written = spec.paths.out.parent
    assert modes == [0o700] and not written.exists()
    record = tmp_path / f"{answers.record}.out"
    assert not record.is_symlink() and record.stat().st_mode & 0o777 == 0o600 and "verdicts" in record.read_text(encoding="utf-8")
    assert (tmp_path / "round1-verify.out").is_symlink()
    assert (tmp_path / "round1-verify.err.log").read_text(encoding="utf-8") == "前の記録"
    assert (other.read_text(encoding="utf-8"), other.stat().st_mode & 0o777) == ("別のファイル", 0o644)
    assert run.notes == [(1, "verify", "判定役へ 1 件、有効な回答 1 件")]


def test_issue_4363_ask_keeps_the_records_of_every_launch_and_replaces_nothing(tmp_path: Path, monkeypatch) -> None:
    """同じラウンドの起動し直しでも前の記録は残る。同じ名前があれば置き換えずに過程へ残し、判定の結果は変えない。"""
    run, diff, seen = _AskRun(tmp_path), _diff(tmp_path), []
    answered = _launcher(seen, [holding(_row())])
    first, second = (verify.ask(run, 1, [_row()], {"r1-1": [diff]}, answered).record for _ in range(2))
    assert first != second and all("verdicts" in (tmp_path / f"{n}.out").read_text(encoding="utf-8") for n in (first, second))
    monkeypatch.setattr(verify.uuid, "uuid4", lambda: SimpleNamespace(hex="taken000"))
    taken = tmp_path / "round1-verify-taken000.out"
    taken.write_text("前の記録", encoding="utf-8")
    answers = verify.ask(run, 1, [_row()], {"r1-1": [diff]}, answered)
    assert answers.verdicts["r1-1"].holds is True and answers.record == ""
    assert taken.read_text(encoding="utf-8") == "前の記録"
    assert [note for note in run.notes if "書けない" in note[2]] and str(tmp_path) not in str(run.notes)


@pytest.mark.parametrize("source", ["path", "principles_path"])
def test_issue_4363_ask_does_not_answer_when_reading_the_mvv_copy_is_denied(tmp_path: Path, source: str) -> None:
    snap, diff = _approved(tmp_path), _diff(tmp_path)
    denied = getattr(snap, source).name
    answers = verify.ask(_AskRun(tmp_path, snap), 1, [_row()], {"r1-1": [diff]}, _launcher([], [holding(_row())], denied=denied))
    assert answers.verdicts == {} and denied in answers.warnings[0]


@pytest.mark.parametrize(
    "outer",
    [
        "警告の行\n{inner}",
        '{{"permission_denials": [], "nested": {inner}}}',
        '{{"result": {quoted}}}',
        '{{"result": {quoted}, "permission_denials": ["壊れた要素"]}}',
    ],
)
def test_issue_4363_ask_does_not_take_a_negative_answer_from_an_unverified_output(tmp_path: Path, outer: str) -> None:
    """外側の結果か拒否の一覧を確かめられない出力から読めた否定の回答は採らず、指摘の種類は残す。"""
    inner = json.dumps({"verdicts": [_answer(tracks=["secret"])]}, ensure_ascii=False)

    def launch(spec: agents.Launch) -> agents.AgentStatus:
        spec.paths.out.write_text(outer.format(inner=inner, quoted=json.dumps(inner)), encoding="utf-8")
        return agents.AgentStatus(spec.agent, agents.AgentState.DONE, 0, "", ())

    answers = verify.ask(_AskRun(tmp_path), 1, [_row()], {"r1-1": [_diff(tmp_path)]}, launch)
    verdict = answers.verdicts["r1-1"]
    assert (verdict.holds, verdict.tracks) == (None, ("secret",))
    assert any("確かめられない" in warning for warning in answers.warnings)


@pytest.mark.parametrize("skipped", ["round1.diff", "round1-verify-findings.jsonl"])
def test_issue_4363_ask_does_not_take_a_negative_answer_without_reading_the_materials(tmp_path: Path, skipped: str) -> None:
    """差分か指摘の一覧を読んだ記録の無い起動の否定の回答は採らない。保存する記録に、読んだファイルの中身を残さない。"""

    def launch(spec: agents.Launch) -> agents.AgentStatus:
        body = {"result": json.dumps({"verdicts": [_answer()]}, ensure_ascii=False), "permission_denials": []}
        read = [p for p in materials(spec) if Path(p).name != skipped]
        spec.paths.out.write_text(output(body, read), encoding="utf-8")
        return agents.AgentStatus(spec.agent, agents.AgentState.DONE, 0, "", ())

    answers = verify.ask(_AskRun(tmp_path), 1, [_row()], {"r1-1": [_diff(tmp_path)]}, launch)
    assert answers.verdicts["r1-1"].holds is None
    assert any(skipped in warning for warning in answers.warnings)
    saved = (tmp_path / f"{answers.record}.out").read_text(encoding="utf-8")
    assert "tool_use" in saved and "読んだ中身" not in saved


@pytest.mark.parametrize(
    "ranges, taken",
    [([(1, 442)], False), ([(1, 442), (600, 902)], False), ([(443, 1059), (1, 442)], True)],
    ids=["head-only", "gap", "continued"],
)
def test_issue_4363_ask_takes_a_negative_answer_only_after_every_line_was_read(tmp_path: Path, ranges: list, taken: bool) -> None:
    """大きいファイルは先頭だけが成功の結果で返るので、返った行を合わせて最後まで届いたときだけ否定の回答を採る。"""

    def launch(spec: agents.Launch) -> agents.AgentStatus:
        listed, diff = materials(spec)
        rows = read_rows(0, listed)
        for n, (start, count) in enumerate(ranges, 1):
            rows += read_rows(n, diff, start, count, 1501)
        body = {"result": json.dumps({"verdicts": [_answer()]}, ensure_ascii=False), "permission_denials": []}
        spec.paths.out.write_text(lines([*rows, {"type": "result", **body}]), encoding="utf-8")
        return agents.AgentStatus(spec.agent, agents.AgentState.DONE, 0, "", ())

    answers = verify.ask(_AskRun(tmp_path), 1, [_row()], {"r1-1": [_diff(tmp_path)]}, launch)
    assert answers.verdicts["r1-1"].holds is (False if taken else None)


def test_issue_4363_recordable_output_keeps_only_the_result_and_the_tool_calls() -> None:
    """キーの順や空白が違う行、知らない種類の行、途中で切れた行に読んだ中身があっても、保存する記録に残さない。"""
    use, done = read_rows(0, "/wt/secret.env")
    text = {"type": "text", "text": "読んだ中身"}
    rows = [
        " " + json.dumps({"message": done["message"], "tool_use_result": done["tool_use_result"], "type": "user"}),
        json.dumps({"type": "tool_output", "content": "読んだ中身"}),
        json.dumps({"type": "assistant", "message": {"content": [text, *use["message"]["content"], *done["message"]["content"]]}}),
        json.dumps({"type": "result", "result": "回答", "permission_denials": [], "total_cost_usd": 0.25, "extra": "読んだ中身"}),
        '{"type":"user","message":"読んだ中身',
    ]
    saved = participants.recordable_output("\n".join(rows).encode()).decode()
    assert "読んだ中身" not in saved and "\\u8aad" not in saved
    assert "/wt/secret.env" in saved and "回答" in saved and len(saved.splitlines()) == 2
    assert json.loads(saved.splitlines()[-1])["total_cost_usd"] == 0.25


def test_issue_4363_ask_does_not_take_a_negative_answer_when_reading_the_implementation_is_denied(tmp_path: Path) -> None:
    """資料でないファイルの拒否は指摘に対応づけられないので、否定の回答は採らず、起きるとした回答と指摘の種類は残す。"""
    run, diff = _AskRun(tmp_path), _diff(tmp_path)
    given = [_answer("r1-1", tracks=["secret"]), holding(_row("r1-2"))]
    launch = _launcher([], given, denied=run.cfg.worktree / "src" / "settings.py")
    rows = [_row("r1-1"), _row("r1-2")]
    answers = verify.ask(run, 1, rows, {"r1-1": [diff], "r1-2": [diff]}, launch)
    first, second = answers.verdicts["r1-1"], answers.verdicts["r1-2"]
    assert (first.holds, first.tracks, second.holds) == (None, ("secret",), True)
    [warning] = answers.warnings
    assert "r1-1" in warning and "r1-2" not in warning and str(tmp_path) not in warning + str(run.notes)
    assert "settings.py" in (tmp_path / f"{answers.record}.detail.log").read_text(encoding="utf-8")


def test_issue_4363_ask_points_the_mvv_criterion_at_the_copies_in_the_input_directory(tmp_path: Path) -> None:
    """MVV の本文と共通原則は、判定役が読める資料のディレクトリへ写してから基準 5 で指す。"""
    snap, diff, seen = _approved(tmp_path), _diff(tmp_path), []
    read: dict[str, str] = {}
    verify.ask(_AskRun(tmp_path, snap), 1, [_row()], {"r1-1": [diff]}, _launcher(seen, [], read=read))
    prompt = seen[0].argv[-1]
    for source in (snap.path, snap.principles_path):
        assert str(_given(seen[0]) / source.name) in prompt and str(source) not in prompt
        assert read[source.name] == source.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "key, names",
    [("other", ["round2.diff"]), ("filled", ["full.diff"]), ("both", ["round2.diff", "full.diff"])],
)
def test_issue_4363_filled_read_gives_the_diffs_read_by_the_seats_of_the_finding(key: str, names: list) -> None:
    filled = reviewing.FilledRead(Path("full.diff"), frozenset({"filled", "both"}), frozenset({"other", "both"}))
    assert [d.name for d in filled.diffs(key, Path("round2.diff"))] == names


def test_issue_4363_ask_lists_the_diffs_of_each_finding_when_they_differ(tmp_path: Path) -> None:
    """指示は渡す指摘の差分をすべて挙げ、一覧の各行はその指摘の差分だけを持つ。渡さない指摘の差分は挙げない。"""
    diff, seen = _diff(tmp_path), []
    full, unused = diff.parent / "round1-full.diff", diff.parent / "unused.diff"
    full.write_text("full", encoding="utf-8")
    diffs = {"r1-1": [diff], "r1-2": [diff, full], "r1-3": [unused]}
    read: dict[str, str] = {}
    verify.ask(_AskRun(tmp_path), 1, [_row("r1-1"), _row("r1-2")], diffs, _launcher(seen, [], read=read))
    prompt, place = seen[0].argv[-1], _given(seen[0])
    assert str(place / full.name) in prompt and unused.name not in prompt
    shown = [json.loads(line)["diffs"] for line in read["round1-verify-findings.jsonl"].splitlines()]
    assert shown == [[str(place / diff.name)], [str(place / diff.name), str(place / full.name)]]


def test_issue_4363_ask_masks_the_credentials_in_everything_it_gives_the_judge(tmp_path: Path) -> None:
    """指摘の引用・差分の削除行・MVV の写し・指示に埋める重点の認証情報は、判定役へ渡す前に伏せ、元の差分は変えない。"""
    snap, diff, seen = _approved(tmp_path), _diff(tmp_path), []
    token, password = "ghp_" + "A" * 24, "pass" + "word123"
    removed = f"-url = https://user:{password}@example.com\n-token = {token}\n+url = None\n"
    diff.write_text(removed, encoding="utf-8")
    snap.path.write_text(f"# MVV の本文 {token}", encoding="utf-8")
    run = _AskRun(tmp_path, snap)
    run.cfg.settings.focus = (f"{token} の扱い",)
    row = _row(text=f"{token} をログに出す", impact=f"https://user:{password}@example.com が読まれる")
    read: dict[str, str] = {}
    verify.ask(run, 1, [row], {"r1-1": [diff]}, _launcher(seen, [], read=read))
    given = "".join(read.values()) + seen[0].argv[-1]
    assert token not in given and password not in given
    assert "をログに出す" in given and "の扱い" in given and "+url = None" in read[diff.name] and "# MVV の本文" in read[snap.path.name]
    assert diff.read_text(encoding="utf-8") == removed


@pytest.mark.parametrize("how", ["failed", "denied", "no-json", "empty"])
def test_issue_4363_ask_failure_gives_no_answer_and_one_warning(tmp_path: Path, how: str) -> None:
    """起動の失敗・差分の読み取りの拒否・読めない回答・空の回答は、回答なしと注意 1 件にし、起動し直さない。"""
    diff, seen = _diff(tmp_path), []
    launch = {
        "failed": _launcher(seen, [holding(_row())], state=agents.AgentState.FAILED),
        "denied": _launcher(seen, [holding(_row())], denied=diff.name),
        "no-json": _launcher(seen, None),
        "empty": _launcher(seen, []),
    }[how]
    answers = verify.ask(_AskRun(tmp_path), 1, [_row()], {"r1-1": [diff]}, launch)
    assert answers.verdicts == {} and len(answers.warnings) == 1 and len(seen) == 1
    assert not seen[0].paths.out.parent.exists() and (tmp_path / f"{answers.record}.out").is_file()
    # 注意は PR の記録に載るので、手元の絶対パスを書かない
    assert str(tmp_path) not in answers.warnings[0]


def test_issue_4363_ask_keeps_the_failure_detail_out_of_the_process_and_in_the_private_log(tmp_path: Path) -> None:
    """起動の状態の詳細は接続先やアカウントを含みうるので過程へ載せず、実行者だけが読める別の記録に残し、標準エラーは変えない。"""
    run, diff, seen = _AskRun(tmp_path), _diff(tmp_path), []
    answered = _launcher(seen, None, state=agents.AgentState.FAILED)

    def launch(spec: agents.Launch) -> agents.AgentStatus:
        spec.paths.err.write_text("usage limit", encoding="utf-8")
        return answered(spec)._replace(detail="usage limit: https://private.example.test")

    answers = verify.ask(run, 1, [_row()], {"r1-1": [diff]}, launch)
    assert "private.example.test" not in str(run.notes) + str(answers.warnings)
    log = tmp_path / f"{answers.record}.detail.log"
    assert "private.example.test" in log.read_text(encoding="utf-8") and log.stat().st_mode & 0o777 == 0o600
    err = tmp_path / f"{answers.record}.err.log"
    assert err.read_text(encoding="utf-8") == "usage limit"


def test_issue_4363_ask_saves_the_output_when_the_launch_is_interrupted(tmp_path: Path) -> None:
    """監視中の割り込みは判定役を止めた後に届くので、出力を保存して一時ディレクトリを閉じてから伝える。"""
    seen: list = []

    def interrupted(spec: agents.Launch) -> agents.AgentStatus:
        seen.append(spec)
        spec.paths.out.write_text(lines(read_rows(0, "/wt/a.py")[:1]) + "書きかけ", encoding="utf-8")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        verify.ask(_AskRun(tmp_path), 1, [_row()], {"r1-1": [_diff(tmp_path)]}, interrupted)
    [record] = tmp_path.glob("round1-verify-*.out")
    saved = record.read_text(encoding="utf-8")
    assert "/wt/a.py" in saved and "書きかけ" not in saved and not seen[0].paths.out.parent.exists()


@pytest.mark.parametrize(
    "asked, handed, resumed",
    [((), (), True), (("a.md#見出し",), (), False), ((), (fin.Handed("g1", "足すべき内容"),), False)],
    ids=["unsent-head", "other-sections", "other-handed"],
)
def test_issue_4363_kept_review_needs_the_same_request_but_not_the_citation_check(asked: tuple, handed: tuple, resumed: bool) -> None:
    """承認済みの MVV の根拠の記述を確かめ終えていない（head が PR に届いていない）結果は使い、詳細レビューを求める
    セクションか足すべき内容が違う結果は使わない。"""
    approved = f"{md.State.APPROVED}:v1"
    record = {
        "round": 1,
        "head": "h" * 40,
        scope_decide.BASE: "b" * 40,
        scope_decide.REVIEWERS: ["codex"],
        scope_decide.FOCUS: [],
        scope_decide.MVV: {scope_decide.MVV_ID: approved},
        fin.MVV_CHECKED: False,
        "scope": scope_decide.FULL,
    }
    scope = scope_decide.Scope(scope_decide.FULL, None, ())
    prep = reviewing.Prepared(scope, [], set(), "h" * 40, Path("round1.diff"), (), "b" * 40, None)
    then = SimpleNamespace(cfg=SimpleNamespace(handed=()), history=[frozenset({"a"})])
    held = reviewing.pending(then, record, prep, reviewing.Reviewed([HEAVY], {}, False))
    cfg = SimpleNamespace(handed=handed)
    run = SimpleNamespace(cfg=cfg, data={store.PENDING_REVIEW: held}, seats=["codex"], filled=[], warnings=[], process=[], history=[])
    now = prep._replace(scope=scope._replace(unreviewed=asked))
    basis = scope_decide.Basis("b" * 40, ("codex",), (), approved, unit="")
    got = reviewing.kept(run, 1, now, basis)
    assert (got is not None and got.raised == [HEAVY]) is resumed
    assert run.history == ([frozenset({"a"})] if resumed else [])
    if resumed:
        # 振動の判定の履歴を持たない記録も、相手役の結果は使う
        run.data[store.PENDING_REVIEW] = {k: v for k, v in held.items() if k != "history"}
        assert reviewing.kept(run, 1, now, basis).raised == [HEAVY]
    # 足すべき内容を持たない前のバージョンの記録は使わない（例外で止めない）
    run.data[store.PENDING_REVIEW] = {k: v for k, v in held.items() if k != "handed"}
    assert reviewing.kept(run, 1, now, basis) is None


def test_issue_4363_ask_does_not_launch_when_the_mvv_cannot_be_copied(tmp_path: Path) -> None:
    snap = _approved(tmp_path)._replace(path=tmp_path / "無い.md")
    seen: list = []
    run = _AskRun(tmp_path, snap)
    answers = verify.ask(run, 1, [_row()], {"r1-1": [_diff(tmp_path)]}, _launcher(seen, []))
    assert answers.verdicts == {} and len(answers.warnings) == 1 and seen == []
    # 注意も過程も結果 JSON に出るので、絶対パスを含む詳細は実行者だけが読めるログにだけ残す
    assert str(tmp_path) not in answers.warnings[0] + str(run.notes)
    [log] = tmp_path.glob("round1-verify-*.detail.log")
    assert str(tmp_path) in log.read_text(encoding="utf-8") and log.stat().st_mode & 0o777 == 0o600


def test_issue_4363_judge_monitor_warning_names_the_kind_but_not_the_stderr_line(tmp_path: Path, capsys) -> None:
    seen: list = []
    verify.ask(_AskRun(tmp_path), 1, [_row()], {"r1-1": [_diff(tmp_path)]}, _launcher(seen, []))
    spec = replace(seen[0], paths=agents.AgentPaths.at(tmp_path / "watched"))
    spec.paths.err.write_text("Error: host.example.invalid へ接続できない\n", encoding="utf-8")
    agents._report_warnings(agents._Watch(spec, 0.0, 0.0))
    shown = capsys.readouterr().err
    assert "stderr" in shown and "host.example.invalid" not in shown


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
    # ラウンドの記録は、このラウンドで起動した判定役の出力を名指す
    assert list(fake.state_dir.glob(f"{state['rounds'][-1]['judge_output']}.*"))


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
    written: list[dict] = []
    write = store.write_object
    monkeypatch.setattr(store, "write_object", lambda path, value: (written.append(dict(value)), write(path, value)))
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok") and fake.judged == []
    # 判定役を起動しないラウンドは、判定待ちのための保存をしない
    assert not any(store.PENDING_REVIEW in value for value in written)


def test_issue_4363_run_gives_the_judge_the_diffs_read_by_the_seats_of_the_asked_findings(
    wt: Path, tmp_path: Path, capsys, monkeypatch
) -> None:
    """席を埋めたラウンドの判定役は 1 回の起動で、渡す指摘を出した席が読んだ差分をすべて読む。"""
    codex, filled = {**HEAVY, "text": "削除が止まる"}, {**HEAVY, "text": "読み込みが止まる"}
    fake = Fake(tmp_path / "st", {"codex": [[HEAVY], [codex]], "kiro": [[], FAIL, FAIL], "claude": [[filled]]})
    _run(capsys, monkeypatch, fake, wt)
    first, second = (spec.argv[-1] for spec in fake.judged[:2])
    assert "round1.diff" in first
    [line] = [line for line in second.splitlines() if line.startswith("差分: ")]
    assert re.findall(r"round2[\w-]*\.diff", line) == ["round2.diff", "round2-full.diff"]


def test_issue_4363_lowered_finding_is_not_counted_as_a_return(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """確認で下げた指摘は、そのラウンドの振動の判定のキーに数えない（後で保護の種類が付いて戻っても、往復と見ない）。"""
    other, third = {**HEAVY, "text": "読み込みが止まる"}, {**HEAVY, "text": "削除が止まる"}
    fake = Fake(tmp_path / "st", {"codex": [[HEAVY, other], [third], [{**HEAVY, "protect": ["user_data"]}]], "kiro": [[], [], []]})
    fake.verdict = lambda row: _broken(row) if row["text"] == HEAVY["text"] else holding(row)
    _run(capsys, monkeypatch, fake, wt, "--max-rounds", "3")
    rounds = _state(fake.state_dir)["rounds"]
    assert [r["verdict"] for r in rounds] == ["changes_required"] * 3


def test_issue_4363_run_resumes_from_the_judge_without_launching_the_reviewers_again(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """判定役の待ちで中断した打ち直しは、保存した相手役の指摘から続ける。"""

    def interrupted(_: dict) -> Optional[dict]:
        raise KeyboardInterrupt

    first = Fake(tmp_path / "st", {"codex": [[HEAVY]], "kiro": [[]]})
    first.verdict = interrupted
    with pytest.raises(KeyboardInterrupt):
        _run(capsys, monkeypatch, first, wt)
    second = Fake(tmp_path / "st", {})
    second.verdict = _broken
    code, got = _run(capsys, monkeypatch, second, wt)
    assert (code, got["status"]) == (0, "ok")
    assert second.calls == [] and len(second.judged) == 1
    state = _state(second.state_dir)
    [row] = state["findings"]
    assert (row["id"], row["check"]["lowered"]) == ("r1-1", True)
    assert len(state["rounds"]) == 1 and "pending_review" not in state


def test_issue_4363_run_resumed_from_the_judge_still_sees_the_oscillation(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """指摘が戻った第 3 ラウンドの判定中に中断しても、打ち直しは前のラウンドの指摘を覚えていて振動で止める。"""
    other = {**HEAVY, "text": "読み込みが止まる"}
    asked: list[str] = []

    def interrupted_on_the_return(row: dict) -> Optional[dict]:
        if row["text"] == HEAVY["text"] and asked.count(HEAVY["text"]):
            raise KeyboardInterrupt
        asked.append(row["text"])
        return holding(row)

    first = Fake(tmp_path / "st", {"codex": [[HEAVY], [other], [HEAVY]], "kiro": [[], [], []]})
    first.verdict = interrupted_on_the_return
    with pytest.raises(KeyboardInterrupt):
        _run(capsys, monkeypatch, first, wt)
    second = Fake(tmp_path / "st", {})
    _run(capsys, monkeypatch, second, wt)
    assert second.calls == []
    assert _state(second.state_dir)["rounds"][-1]["verdict"] == fin.OSCILLATING


def test_issue_4363_run_resumes_by_launching_only_the_seat_without_a_result(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """結果の無い席があるラウンドを判定役の待ちで中断した打ち直しは、成功した席の指摘を残し、結果の無い席だけを起動する。"""

    def interrupted(_: dict) -> Optional[dict]:
        raise KeyboardInterrupt

    first = Fake(tmp_path / "st", {"codex": [FAIL, FAIL], "kiro": [[HEAVY]], "claude": [FAIL]})
    first.verdict = interrupted
    with pytest.raises(KeyboardInterrupt):
        _run(capsys, monkeypatch, first, wt)
    second = Fake(tmp_path / "st", {"claude": [[]]})
    second.verdict = _broken
    code, got = _run(capsys, monkeypatch, second, wt)
    assert (code, got["status"]) == (0, "ok")
    assert second.calls == ["claude"] and len(second.judged) == 1
    state = _state(second.state_dir)
    [row] = state["findings"]
    assert (row["by"], row["check"]["lowered"]) == ("kiro", True)
    assert len(state["rounds"]) == 1 and "pending_review" not in state


def test_issue_4363_run_writes_the_full_diff_once_when_it_resumes_a_filled_seat(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """差分を絞るラウンドで埋めた席を起動し直すとき、判定待ちから復元した全差分を作り直さない。"""
    other = {**HEAVY, "text": "読み込みが止まる"}

    def interrupted_on_the_second_round(row: dict) -> Optional[dict]:
        if row["text"] == other["text"]:
            raise KeyboardInterrupt
        return holding(row)

    first = Fake(tmp_path / "st", {"codex": [[HEAVY], FAIL, FAIL], "kiro": [[], [other]], "claude": [FAIL]})
    first.verdict = interrupted_on_the_second_round
    with pytest.raises(KeyboardInterrupt):
        _run(capsys, monkeypatch, first, wt)
    second = Fake(tmp_path / "st", {"claude": [[]]})
    second.verdict = _broken
    written: list[str] = []
    write = reviewing.write_diff
    monkeypatch.setattr(reviewing, "write_diff", lambda *args: (written.append(args[2]), write(*args))[1])
    code, got = _run(capsys, monkeypatch, second, wt)
    assert (code, got["status"]) == (0, "ok") and second.calls == ["claude"]
    assert written.count(reviewing.FULL_DIFF_SUFFIX) == 1
