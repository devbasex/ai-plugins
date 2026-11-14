"""C4 が閉じる Issue の場面を、ndf-next の参照の部品（`ndf.core.refs`）で再現するテスト。

1 つの関数が 1 つの Issue の場面を、今の NDF で起きた入力の形のまま通す。ネットワークへ出ない。
#1592（状態の置き場を退避して頭から流し直すと、耐久の記録から途中のステップへ戻る）は実行の鍵の選び方
（`core/plan/assemble_decide.choose_run`）の場面で、参照の部品の範囲に無い。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.contracts.exit_codes import GATE, OK, STOPPED
from ndf.core import refs
from ndf.core.plan.queue_decide import Outcome
from ndf.core.refs import resume

from ._refs_helpers import graphql_hub, graphql_pull, write_progress, write_result

# 計画の課題と、それを閉じる PR（#2568 の場面の番号）
ISSUE, CLOSING_PR = 2568, 2601


def _closing_answer(node: str, args: str) -> dict:
    assert node == "issue"
    return {"closedByPullRequestsReferences": {"totalCount": 1, "nodes": [{"number": CLOSING_PR, "merged": True}]}}


def test_issue_3255_unresolved_refs_stop_with_a_kind_instead_of_running(tmp_path: Path) -> None:
    # N1 #3255: 流す直前に参照で解き、解けなければ種類つきで止まる
    plan = {"name": "release", "refs": {"version": {"kind": "version", "of": "bump.state"}, "prs": {"kind": "queue_prs"}}}
    out = refs.resolve_plan(plan, refs.Context((), (), tmp_path), refs.RepoSources(graphql_hub(_closing_answer)))
    assert out.decision == STOPPED
    assert {name: u.cause for name, u in out.unresolved.items()} == {"version": "input_missing", "prs": "input_missing"}
    assert out.values == {}
    assert out.gaps() == ["ref:prs", "ref:version"]


def test_issue_2344_the_plan_holds_a_reference_and_reads_the_value_just_before_running(tmp_path: Path) -> None:
    # #2344: プランを組んだ時点の値でなく、流す直前の値を使う
    plan = {"name": "release", "refs": {"version": {"kind": "version", "of": "bump.state"}}}
    ctx, src = refs.Context((), (), tmp_path), refs.RepoSources(graphql_hub(_closing_answer))
    write_result(tmp_path / "bump.state", OK, {"version": "10.27.64"}, key="k-1")
    assert refs.resolve_plan(plan, ctx, src).values == {"version": "10.27.64"}
    write_result(tmp_path / "bump.state", OK, {"version": "10.27.65"}, key="k-2")
    assert refs.resolve_plan(plan, ctx, src).values == {"version": "10.27.65"}
    assert plan == {"name": "release", "refs": {"version": {"kind": "version", "of": "bump.state"}}}


@pytest.mark.parametrize("record_pr", [0, "#0", "00"])
def test_issue_2408_release_verify_is_skipped_not_ok_when_the_record_pr_resolves_to_zero(tmp_path: Path, record_pr: object) -> None:
    # #2408: リリース記録の番号が 0 でも流れて ok で終わり、「リリース後テスト」の印が付いた（`#` 付き・0 の並びも同じ）
    write_result(tmp_path / "record.state", OK, {"record_pr": record_pr})
    src = refs.RepoSources(graphql_hub(_closing_answer))
    verify = {"name": "release-verify", "refs": {"pr": {"kind": "record_pr", "of": "record.state", "when_empty": "skip"}}}
    out = refs.resolve_plan(verify, refs.Context((), (), tmp_path), src)
    assert (out.decision, out.skipped, out.values) == (refs.SKIP, ("pr",), {})
    strict = {"name": "release-verify", "refs": {"pr": {"kind": "record_pr", "of": "record.state"}}}
    assert refs.resolve_plan(strict, refs.Context((), (), tmp_path), src).decision == STOPPED


def test_issue_2489_redesign_starts_from_the_latest_head_of_the_design_pr() -> None:
    # #2489: 出し直しの設計プランが設計 PR の最新から始まらなかった
    head = "c0ffee" + "0" * 34
    pulls = [graphql_pull(2489, False, "open", head, "design/issue-2489")]
    pr = refs.RepoSources(graphql_hub(lambda node, args: {"nodes": pulls})).design_pr("design/issue-2489")
    start = resume.design_start(pr)
    assert (start.skip, start.commit) == (False, head)


def test_issue_2528_a_design_merged_in_the_previous_lap_is_not_redone() -> None:
    # #2528: 前のループの 1 回でマージ済みの設計を、次のループの 1 回が設計からやり直した
    pulls = [graphql_pull(2530, True, "merged", "a" * 40, "design/issue-2528")]
    pr = refs.RepoSources(graphql_hub(lambda node, args: {"nodes": pulls})).design_pr("design/issue-2528")
    assert resume.design_start(pr).skip is True


def test_issue_2568_a_release_only_lap_fills_queue_prs_from_the_prs_that_close_the_issues(tmp_path: Path) -> None:
    # #2568: リリースだけのループの 1 回で前のステージに PR が無く、record が {queue_prs} を置き換えずに落ちた
    seen: list[str] = []
    src = refs.RepoSources(graphql_hub(_closing_answer, seen))
    record = {"name": "record", "refs": {"queue_prs": {"kind": "queue_prs"}}}
    out = refs.resolve_plan(record, refs.Context((), (ISSUE,), tmp_path), src)
    assert out.values == {"queue_prs": str(CLOSING_PR)}
    assert len(seen) == 1


def test_issue_2569_rerunning_the_check_plan_from_the_middle_keeps_the_review_counts(tmp_path: Path) -> None:
    # #2569: 検査のプランを途中から流し直すと、記録の行に前の実行のレビューの件数が入らず findings が null になった
    rows = [
        {"event": "run", "start": None},
        {"event": "step", "step": "review", "counts": {"findings": 4}},
        {"event": "run", "start": "record"},
    ]
    write_progress(tmp_path / "check.state", rows)
    plan = {"name": "check", "refs": {"findings": {"kind": "counts", "of": "check.state", "key": "review"}}}
    out = refs.resolve_plan(plan, refs.Context((), (), tmp_path), refs.RepoSources(graphql_hub(_closing_answer)))
    assert json.loads(out.values["findings"]) == {"findings": 4}


def test_issue_2638_a_question_during_reclosing_a_held_lap_finds_its_lap() -> None:
    # #2638: 保留のループの 1 回を締め直す途中の問いを進行中のループの 1 回が無いとして断った
    laps = [resume.Lap("lap-3", "held", ("/r/.ndf/lap-3/close.json",), closing=True)]
    found = resume.question_target(laps, "/r/.ndf/lap-3/./close.json")
    assert found is not None and found.name == "lap-3"


def test_issue_2648_results_outside_the_current_run_resolve_the_same_on_reclose_and_resume(tmp_path: Path) -> None:
    # #2648: ループの 1 回とプランが今の起動の外にある成果を参照せず、締め直しと再開で食い違った
    write_result(tmp_path / "bump.state", OK, {"version": "10.27.66"})
    write_progress(tmp_path / "check.state", [{"event": "step", "step": "review", "counts": {"findings": 1}}])
    plan = {
        "name": "close",
        "refs": {
            "version": {"kind": "version", "of": "bump.state"},
            "counts": {"kind": "counts", "of": "check.state"},
            "queue_prs": {"kind": "queue_prs"},
        },
    }
    src = refs.RepoSources(graphql_hub(_closing_answer))
    first = refs.resolve_plan(plan, refs.Context((Outcome("impl", OK, prs=(2650,)),), (ISSUE,), tmp_path), src)
    again = refs.resolve_plan(plan, refs.Context((), (ISSUE,), tmp_path), src)
    assert first.values["version"] == again.values["version"] == "10.27.66"
    assert first.values["counts"] == again.values["counts"]
    assert first.values["queue_prs"] == f"{CLOSING_PR} 2650"
    assert again.values["queue_prs"] == str(CLOSING_PR)


def test_issue_2663_insert_values_and_counts_come_from_outside_the_current_run(tmp_path: Path) -> None:
    # #2663: プランの差し込みの値と検査の件数を、今の起動の外から参照で解く
    write_result(tmp_path / "impl.state", OK, {"branch": "feat/issue-2663"})
    write_progress(tmp_path / "check.state", [{"event": "step", "step": "lint", "counts": {"findings": 0}}])
    plan = {
        "name": "pr",
        "refs": {
            "branch": {"kind": "plan_result", "of": "impl.state", "key": "branch"},
            "lint": {"kind": "counts", "of": "check.state", "key": "lint"},
        },
    }
    out = refs.resolve_plan(plan, refs.Context((), (), tmp_path), refs.RepoSources(graphql_hub(_closing_answer)))
    assert out.values == {"branch": "feat/issue-2663", "lint": json.dumps({"findings": 0})}
    assert out.sources["branch"] == str(tmp_path / "impl.state")


def test_issue_2664_question_target_and_resume_position_come_from_outside_the_current_lap(tmp_path: Path) -> None:
    # #2664: 問いの当て先と再開の位置を、今のループの 1 回の外から参照で解く
    laps = [resume.Lap("lap-1", "done", ("a.json",)), resume.Lap("lap-2", "running", ("b.json",))]
    assert resume.question_target(laps, "b.json") == laps[1]
    done = resume.resume_target("b", "d1", {"status": OK, "digest": "d1"}, None)
    changed = resume.resume_target("b", "d2", {"status": OK, "digest": "d1"}, None)
    assert (done.action, changed.action) == (refs.SKIP, GATE)


def test_issue_2665_the_next_lap_design_starts_from_the_design_pr_state() -> None:
    # #2665: 次のループの 1 回の設計の起点を、設計 PR の状態から決める
    states = {
        "design/issue-1": [graphql_pull(1, True, "merged", "a" * 40, "design/issue-1")],
        "design/issue-2": [graphql_pull(2, False, "closed", "b" * 40, "design/issue-2")],
        "design/issue-3": [],
    }

    def answer(node: str, args: str) -> dict:
        branch = args.split('"')[1]
        return {"nodes": states[branch]}

    src = refs.RepoSources(graphql_hub(answer))
    got = {b: resume.design_start(src.design_pr(b)) for b in states}
    assert [(s.skip, s.commit) for s in got.values()] == [(True, None), (False, None), (False, None)]


def test_issue_2806_resume_from_accepts_the_path_of_the_plan(tmp_path: Path) -> None:
    # #2806: queue --resume の --from が計画のパスを受け付けず「前の起動の一覧に無い」と断った
    plans = {"impl-2806": tmp_path / ".ndf" / "plans" / "impl-2806.json", "pr-2806": tmp_path / ".ndf" / "plans" / "pr.json"}
    assert resume.match_plan(plans, str(plans["impl-2806"]), tmp_path / "sub") == ["impl-2806"]
    assert resume.match_plan(plans, ".ndf/plans/pr.json", tmp_path) == ["pr-2806"]
    assert resume.match_plan(plans, "plans/impl-2806.json", tmp_path / "other") == ["impl-2806"]
    assert resume.match_plan(plans, "impl-2806", tmp_path) == ["impl-2806"]
