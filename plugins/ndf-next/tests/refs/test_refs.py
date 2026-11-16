"""参照の部品（`ndf.core.refs`）の種類ごとの解決と、解けない理由を、DBOS も GitHub も起こさずに確かめる。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from ndf.contracts import causes
from ndf.contracts.exit_codes import GATE, OK, STOPPED
from ndf.core import refs
from ndf.core.plan.queue_decide import Outcome
from ndf.core.refs import resume

from ._refs_helpers import FakeSources, failing_hub, graphql_hub, graphql_pull, hub, write_progress, write_result


def _ctx(tmp_path: Path, prior: tuple[Outcome, ...] = (), issues: tuple[int, ...] = ()) -> refs.Context:
    return refs.Context(prior, issues, tmp_path)


# --- 宣言 ---------------------------------------------------------------------------------


def test_declared_reads_the_refs_of_the_plan() -> None:
    raw = {"refs": {"v": {"kind": "version", "of": "rel.state"}, "prs": {"kind": "queue_prs", "when_empty": "skip"}}}
    assert refs.declared(raw) == [
        refs.Ref("v", "version", "rel.state", None, False),
        refs.Ref("prs", "queue_prs", None, None, True),
    ]
    assert refs.declared({"name": "p"}) == []


@pytest.mark.parametrize(
    "raw",
    [
        {"refs": []},
        {"refs": {"x": {"kind": "nope", "of": "a"}}},
        {"refs": {"x": {"kind": "version"}}},
        {"refs": {"x": {"kind": "version", "of": ""}}},
        {"refs": {"x": {"kind": "queue_prs", "when_empty": "zero"}}},
    ],
)
def test_declared_refuses_a_broken_declaration(raw: dict) -> None:
    with pytest.raises(refs.RefError):
        refs.declared(raw)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"kind": "nope"}, "参照 x の kind は version・record_pr・queue_prs・plan_result・counts のどれか: {'kind': 'nope'}"),
        ("version", "参照 x の kind は version・record_pr・queue_prs・plan_result・counts のどれか: 'version'"),
        ({"kind": "version", "of": "", "key": 3}, "参照 x の of・key は空でない文字列にする"),
        ({"kind": "counts", "key": "s"}, "参照 x に参照の先（of）が無い"),
        ({"kind": "queue_prs", "when_empty": "zero"}, "参照 x の when_empty は skip だけ: 'zero'"),
    ],
)
def test_declared_names_what_is_wrong_in_the_declaration(raw: object, message: str) -> None:
    """現状固定: 宣言の誤りのメッセージ（検査の順は kind → of/key → of の有無 → when_empty）。"""
    with pytest.raises(refs.RefError) as caught:
        refs.declared({"refs": {"x": raw}})
    assert str(caught.value) == message


def test_declared_keeps_of_and_key_and_an_explicit_null_when_empty() -> None:
    raw = {"refs": {"r": {"kind": "plan_result", "of": "p.state", "key": "out", "when_empty": None}}}
    assert refs.declared(raw) == [refs.Ref("r", "plan_result", "p.state", "out", False)]


def test_resolve_plan_returns_a_broken_declaration_as_invalid(tmp_path: Path) -> None:
    out = refs.resolve_plan({"refs": {"x": {"kind": "nope"}}}, _ctx(tmp_path), FakeSources())
    assert out.decision == STOPPED
    assert out.unresolved["refs"].cause == "invalid"


# --- 種類ごとの解決 -----------------------------------------------------------------------


def test_version_resolves_from_the_outputs_of_the_referenced_plan(tmp_path: Path) -> None:
    src = FakeSources(records={tmp_path / "rel": refs.PlanRecord(OK, {"version": "10.27.65"})})
    out = refs.resolve([refs.Ref("v", "version", "rel")], _ctx(tmp_path), src)
    assert (out.values, out.decision) == ({"v": "10.27.65"}, OK)
    assert out.sources["v"] == str(tmp_path / "rel")


@pytest.mark.parametrize(
    ("record", "reason"),
    [
        (None, "前の回の結果が無い"),
        (refs.PlanRecord(STOPPED, {"version": "1.2.3"}), "完了でない"),
        (refs.PlanRecord(OK, {"version": "v-next"}), "版の形でない"),
        (refs.PlanRecord(OK, {"version": "unknown"}), "が無いか空"),
        (refs.PlanRecord(OK, {"version": "0.0.0"}), "が無いか空"),
        (refs.PlanRecord(OK, {}), "が無いか空"),
    ],
)
def test_version_never_fills_a_placeholder(tmp_path: Path, record: refs.PlanRecord | None, reason: str) -> None:
    src = FakeSources(records={tmp_path / "rel": record} if record is not None else {})
    out = refs.resolve([refs.Ref("v", "version", "rel")], _ctx(tmp_path), src)
    assert out.values == {}
    assert out.unresolved["v"].cause == "input_missing"
    assert reason in out.unresolved["v"].reason
    assert out.gaps() == ["ref:v"]


def test_record_pr_accepts_a_number_with_a_hash(tmp_path: Path) -> None:
    src = FakeSources(records={tmp_path / "r": refs.PlanRecord(OK, {"record_pr": "#3410"})})
    assert refs.resolve([refs.Ref("pr", "record_pr", "r")], _ctx(tmp_path), src).values == {"pr": "3410"}


def test_record_pr_refuses_a_value_that_is_not_a_number(tmp_path: Path) -> None:
    src = FakeSources(records={tmp_path / "r": refs.PlanRecord(OK, {"record_pr": "abc"})})
    assert refs.resolve([refs.Ref("pr", "record_pr", "r")], _ctx(tmp_path), src).unresolved["pr"].cause == "input_missing"


def test_plan_result_reads_the_named_output_and_writes_non_strings_as_json(tmp_path: Path) -> None:
    src = FakeSources(records={tmp_path / "a": refs.PlanRecord(OK, {"branch": "feat/x", "files": ["b", "a"]})})
    ctx = _ctx(tmp_path)
    got = refs.resolve([refs.Ref("b", "plan_result", "a", "branch"), refs.Ref("f", "plan_result", "a", "files")], ctx, src)
    assert got.values == {"b": "feat/x", "f": '["b", "a"]'}


def test_plan_result_without_a_key_is_invalid(tmp_path: Path) -> None:
    out = refs.resolve([refs.Ref("b", "plan_result", "a")], _ctx(tmp_path), FakeSources())
    assert out.unresolved["b"].cause == "invalid"


def test_absolute_of_is_read_as_is(tmp_path: Path) -> None:
    where = tmp_path / "elsewhere" / "rel"
    src = FakeSources(records={where: refs.PlanRecord(OK, {"version": "1.0.0"})})
    assert refs.resolve([refs.Ref("v", "version", str(where))], _ctx(tmp_path / "base"), src).values == {"v": "1.0.0"}


def test_queue_prs_is_the_sorted_union_without_release_prs() -> None:
    prior = [Outcome("a", OK, prs=(7, 3)), Outcome("rel", OK, prs=(9,), release=True), Outcome("b", STOPPED, prs=(11,))]
    assert refs.queue_prs_of(prior, [5, 3]) == [3, 5, 7]


def test_queue_prs_reads_github_once_for_all_issues(tmp_path: Path) -> None:
    src = FakeSources(closing={1: [20], 2: [21, 20]})
    out = refs.resolve([refs.Ref("prs", "queue_prs")], _ctx(tmp_path, issues=(1, 2)), src)
    assert out.values == {"prs": "20 21"}
    assert src.calls == ["closing"]


def test_queue_prs_without_issues_does_not_read_github(tmp_path: Path) -> None:
    src = FakeSources()
    out = refs.resolve([refs.Ref("prs", "queue_prs")], _ctx(tmp_path, prior=(Outcome("a", OK, prs=(4,)),)), src)
    assert (out.values, src.calls) == ({"prs": "4"}, [])


def test_unreadable_github_is_kept_apart_from_empty(tmp_path: Path) -> None:
    src = FakeSources(unreadable=refs.Unreadable("transient", "GitHub へ届かない"))
    out = refs.resolve([refs.Ref("prs", "queue_prs", skip_when_empty=True)], _ctx(tmp_path, issues=(1,)), src)
    assert out.skipped == ()
    assert out.unresolved["prs"] == refs.Unresolved("transient", "GitHub へ届かない")


def test_every_cause_the_refs_return_is_in_the_causes_table(tmp_path: Path) -> None:
    out = refs.resolve(
        [refs.Ref("v", "version", "x"), refs.Ref("p", "plan_result", "x"), refs.Ref("q", "queue_prs")],
        _ctx(tmp_path),
        FakeSources(),
    )
    assert {u.cause for u in out.unresolved.values()} <= set(causes.table())


# --- 件数 ---------------------------------------------------------------------------------


def test_counts_of_takes_the_last_counts_per_step_after_the_last_full_run() -> None:
    rows = [
        {"event": "run", "start": None},
        {"event": "step", "step": "review", "counts": {"findings": 9}},
        {"event": "run", "start": None},
        {"event": "step", "step": "review", "counts": {"findings": 2}},
        {"event": "step", "step": "lint", "counts": {"findings": 1}},
        {"event": "run", "start": "record"},
        {"event": "step", "step": "lint", "counts": {"findings": 0}},
        {"event": "chosen", "step": "x"},
    ]
    assert refs.counts_of(rows) == {"review": {"findings": 2}, "lint": {"findings": 0}}


def test_counts_of_a_single_step_by_key(tmp_path: Path) -> None:
    src = FakeSources(rows={tmp_path / "c": [{"event": "step", "step": "review", "counts": {"findings": 3}}]})
    got = refs.resolve([refs.Ref("n", "counts", "c", "review"), refs.Ref("m", "counts", "c", "lint")], _ctx(tmp_path), src)
    assert got.values == {"n": json.dumps({"findings": 3})}
    assert got.unresolved["m"].cause == "input_missing"


# --- 再開・設計の起点・問いの当て先 -------------------------------------------------------


def test_match_plan_does_not_match_a_different_file_with_the_same_tail(tmp_path: Path) -> None:
    plans = {"impl": tmp_path / "x" / "impl.json", "impl2": tmp_path / "y" / "ximpl.json"}
    assert resume.match_plan(plans, "impl.json", tmp_path) == ["impl"]
    assert resume.match_plan(plans, "nothing", tmp_path) == []


@pytest.mark.parametrize(
    ("recorded", "start", "action"),
    [
        (None, None, OK),
        ({"status": STOPPED, "digest": "d"}, None, OK),
        ({"status": OK, "digest": "d"}, None, refs.SKIP),
        ({"status": OK, "digest": "other"}, None, GATE),
        ({"status": OK}, None, GATE),
        ({"status": OK, "digest": "other"}, "review", OK),
    ],
)
def test_resume_target(recorded: dict | None, start: str | None, action: str) -> None:
    got = resume.resume_target("p", "d", recorded, start)
    assert (got.action, got.start) == (action, start)


def test_question_target_ignores_done_and_plain_held_laps() -> None:
    laps = [resume.Lap("l1", "done", ("a.json",)), resume.Lap("l2", "held", ("a.json",))]
    assert resume.question_target(laps, "a.json") is None


# --- 読み手 -------------------------------------------------------------------------------


def test_repo_sources_read_the_last_result_of_the_plan(tmp_path: Path) -> None:
    write_result(tmp_path / "rel", OK, {"version": "1.2.3"})
    src = refs.RepoSources(failing_hub(500))
    assert src.plan_record(tmp_path / "rel") == refs.PlanRecord(OK, {"version": "1.2.3"})
    assert src.plan_record(tmp_path / "none") is None


def test_repo_sources_skip_a_half_written_progress_line(tmp_path: Path) -> None:
    write_progress(tmp_path / "c", [{"event": "step", "step": "s", "counts": {"n": 1}}], tail='{"event": "st')
    src = refs.RepoSources(failing_hub(500))
    assert src.progress(tmp_path / "c") == [{"event": "step", "step": "s", "counts": {"n": 1}}]
    assert src.progress(tmp_path / "missing") == []


@pytest.mark.parametrize(("status", "cause"), [(500, "transient"), (404, "precondition")])
def test_repo_sources_map_github_failures(status: int, cause: str) -> None:
    src = refs.RepoSources(failing_hub(status))
    with pytest.raises(refs.Unreadable) as raised:
        src.closing_prs([1])
    assert raised.value.cause == cause
    with pytest.raises(refs.Unreadable):
        src.design_pr("design/issue-1")


def _unreached(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("down", request=request)


@pytest.mark.parametrize(
    ("handler", "cause"),
    [
        (_unreached, "transient"),
        (lambda _r: httpx.Response(200, json={"data": None, "errors": [{"message": "boom", "type": "FORBIDDEN"}]}), "precondition"),
        (lambda _r: httpx.Response(200, json={"data": {"repository": None}}), "precondition"),
    ],
)
def test_repo_sources_retry_only_a_github_that_was_not_reached(handler: Callable[[httpx.Request], httpx.Response], cause: str) -> None:
    # 状態が 0 の失敗のうち、届かなかったものだけを打ち直す。GraphQL の誤りと repository が null は人へ戻す
    with pytest.raises(refs.Unreadable) as raised:
        refs.RepoSources(hub(handler)).closing_prs([1])
    assert raised.value.cause == cause


def test_repo_sources_take_only_the_merged_prs_that_close_the_issue() -> None:
    # 閉じた未マージの PR（放棄・出し直しで置き換えた）と開いたままの PR は queue_prs へ入れない
    def answer(node: str, args: str) -> dict:
        nodes = [{"number": 7, "merged": True}, {"number": 8, "merged": False}, {"number": 9, "merged": False}]
        return {"closedByPullRequestsReferences": {"totalCount": 3, "nodes": nodes}}

    assert refs.RepoSources(graphql_hub(answer)).closing_prs([5]) == [7]


def test_repo_sources_refuse_more_closing_prs_than_one_page() -> None:
    def answer(node: str, args: str) -> dict:
        return {"closedByPullRequestsReferences": {"totalCount": refs.CLOSING_PER_ISSUE + 1, "nodes": [{"number": 1}]}}

    with pytest.raises(refs.Unreadable) as raised:
        refs.RepoSources(graphql_hub(answer)).closing_prs([5])
    assert raised.value.cause == "precondition"


def test_repo_sources_return_no_design_pr_when_the_branch_has_none() -> None:
    src = refs.RepoSources(graphql_hub(lambda node, args: {"nodes": []}))
    assert src.design_pr("design/issue-9") == refs.DesignPr(None)


def test_repo_sources_read_the_newest_design_pr() -> None:
    pulls = [graphql_pull(12, False, "open", "b" * 40, "design/issue-9"), graphql_pull(8, False, "closed", "a" * 40, "design/issue-9")]
    src = refs.RepoSources(graphql_hub(lambda node, args: {"nodes": pulls}))
    assert src.design_pr("design/issue-9") == refs.DesignPr(12, False, "open", "b" * 40, "design/issue-9")
