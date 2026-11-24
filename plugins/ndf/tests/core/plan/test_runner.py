"""耐久の実行の runner（`prepare`・`final_result`）。同じプロセスで呼び、キーと回の選び方と結果の組み方を確かめる。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ndf.contracts import internal, result, routing
from ndf.core import durable
from ndf.core.plan import assemble_decide, model, records, runner

from . import _plan_helpers as H

import scenes


# --- 現状固定テスト: `runner.prepare` を同じプロセスで呼ぶ（DBOS を開かない） ---


@pytest.fixture
def in_repo(repo, monkeypatch) -> Path:
    """相対のプランのパスはプロセスの cwd で読まれる（`model.load`）ため、リポジトリへ移る。"""
    monkeypatch.chdir(repo)
    return repo


def _head_of(repo: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def _root(repo: Path) -> Path:
    return repo / ".git" / "ndf" / "state"


def test_prepare_a_fresh_plan(in_repo):
    rel = H.write_plan(in_repo, "p", [H.py_step("s", "pass")])
    root = _root(in_repo)
    inv = runner.prepare(root, Path(rel), None, None, in_repo)
    plan = model.load(in_repo / rel)
    head = _head_of(in_repo)
    sdir = in_repo / "plans" / "p.state"
    assert inv.plan_path == in_repo / rel
    assert inv.plan == plan
    assert (inv.start, inv.prelude, inv.state_dir, inv.head) == (None, (), sdir, head)
    assert inv.digest == plan.digest()
    assert (inv.key, inv.round_no) == (assemble_decide.run_key("p", plan.digest(), None, head, 0), 0)
    assert inv.values == {
        "state_dir": str(sdir),
        "repo": str(in_repo),
        "worktree": "",
        "plan": str(in_repo / rel),
        "owner": assemble_decide.owner_of("p", str(sdir)),
        "work_base": str(root.parent / runner.WORK_DIR),
    }
    assert inv.again == (str(in_repo / rel),)


def test_prepare_with_start_state_dir_and_sets(in_repo):
    rel = H.write_plan(in_repo, "p", [H.py_step("a", "pass"), H.py_step("b", "pass")])
    inv = runner.prepare(_root(in_repo), Path(rel), "b", "st", in_repo, runner.RunOptions({"b": "2", "a": "1"}))
    plan = model.load(in_repo / rel)
    assert inv.state_dir == in_repo / "st"
    assert inv.digest == assemble_decide.run_digest(plan.digest(), {"a": "1", "b": "2"})
    assert inv.digest != plan.digest()
    assert (inv.values["a"], inv.values["b"]) == ("1", "2")
    assert inv.again == (str(in_repo / rel), "--from", "b", "--state-dir", "st", "--set", "a=1", "--set", "b=2")
    assert inv.key == assemble_decide.run_key("p", inv.digest, "b", _head_of(in_repo), 0)


def test_prepare_uses_the_state_dir_of_the_plan(in_repo):
    rel = H.write_plan(in_repo, "p", [H.py_step("s", "pass")], state_dir="from-plan")
    inv = runner.prepare(_root(in_repo), Path(rel), None, None, in_repo)
    assert inv.state_dir == in_repo / "from-plan"
    assert inv.again == (str(in_repo / rel),)


def test_prepare_keeps_an_absolute_plan_path_and_a_plan_without_a_location(in_repo, tmp_path):
    path = tmp_path / "loose.json"
    path.write_text(json.dumps({"name": "loose", "steps": [H.py_step("s", "pass")]}), encoding="utf-8")
    inv = runner.prepare(_root(in_repo), path, None, None, in_repo)
    assert inv.plan_path == path
    assert inv.head is None
    assert (inv.values["repo"], inv.values["worktree"], inv.values["plan"]) == ("", "", str(path))
    assert inv.state_dir == tmp_path / "loose.state"


def test_prepare_keeps_the_resume_prelude_before_the_start(in_repo):
    ids = ["base-sync", "x", "deep-due", "y"]
    rel = H.write_plan(in_repo, "p", [H.py_step(i, "pass") for i in ids])
    assert runner.prepare(_root(in_repo), Path(rel), "y", None, in_repo).prelude == ("base-sync", "deep-due")
    assert runner.prepare(_root(in_repo), Path(rel), routing.END, None, in_repo).prelude == ()


def test_prepare_refuses_an_unknown_start(in_repo):
    rel = H.write_plan(in_repo, "p", [H.py_step("s", "pass")])
    with pytest.raises(model.PlanError, match="^p に nope は無い$"):
        runner.prepare(_root(in_repo), Path(rel), "nope", None, in_repo)


def test_prepare_refuses_sets_that_override_fixed_values(in_repo):
    rel = H.write_plan(in_repo, "p", [H.py_step("s", "pass")])
    with pytest.raises(model.PlanError, match="^差し込みの値 plan, repo は上書きできない$"):
        runner.prepare(_root(in_repo), Path(rel), None, None, in_repo, runner.RunOptions({"repo": "x", "plan": "y", "ok": "z"}))


def _write_record(sdir: Path, **record) -> None:
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / runner.RUN_FILE).write_text(json.dumps(record), encoding="utf-8")


def test_prepare_continues_the_recorded_round_that_has_not_finished(in_repo):
    rel = H.write_plan(in_repo, "p", [H.py_step("s", "pass")])
    digest = model.load(in_repo / rel).digest()
    _write_record(in_repo / "plans" / "p.state", key="K", name="p", digest=digest, start=None, round=3, heads=["other"])
    inv = runner.prepare(_root(in_repo), Path(rel), None, None, in_repo)
    assert (inv.key, inv.round_no) == ("K", 3)


def test_prepare_starts_the_next_round_when_a_finished_round_had_another_head(in_repo):
    rel = H.write_plan(in_repo, "p", [H.py_step("s", "pass")])
    digest = model.load(in_repo / rel).digest()
    root = _root(in_repo)
    _write_record(in_repo / "plans" / "p.state", key="K", name="p", digest=digest, start=None, round=3, heads=["other"])
    retired = root / durable.DBOS_DIR / durable.RETIRED_DIR
    retired.mkdir(parents=True)
    (retired / f"{durable.run_name('K')}-1{durable.DB_SUFFIX}").touch()
    inv = runner.prepare(root, Path(rel), None, None, in_repo)
    assert (inv.key, inv.round_no) == (assemble_decide.run_key("p", digest, None, _head_of(in_repo), 4), 4)


def test_prepare_keeps_a_finished_round_with_the_same_head(in_repo):
    rel = H.write_plan(in_repo, "p", [H.py_step("s", "pass")])
    digest = model.load(in_repo / rel).digest()
    root = _root(in_repo)
    _write_record(in_repo / "plans" / "p.state", key="K", name="p", digest=digest, start=None, round=2, heads=[_head_of(in_repo)])
    retired = root / durable.DBOS_DIR / durable.RETIRED_DIR
    retired.mkdir(parents=True)
    (retired / f"{durable.run_name('K')}{durable.DB_SUFFIX}").touch()
    inv = runner.prepare(root, Path(rel), None, None, in_repo)
    assert (inv.key, inv.round_no) == ("K", 2)


def test_prepare_fresh_skips_a_recorded_round(in_repo):
    """`fresh` は終わっていない回を続けず、保存先にキーの記録が無い番号の回を選ぶ。無ければ今の振る舞いのまま。"""
    rel = H.write_plan(in_repo, "p", [H.py_step("s", "pass")])
    digest = model.load(in_repo / rel).digest()
    root = _root(in_repo)
    head = _head_of(in_repo)
    _write_record(in_repo / "plans" / "p.state", key="K", name="p", digest=digest, start=None, round=3, heads=[head])
    dbos = root / durable.DBOS_DIR
    dbos.mkdir(parents=True)
    (dbos / f"{durable.run_name(assemble_decide.run_key('p', digest, None, head, 4))}{durable.DB_SUFFIX}").touch()
    assert runner.prepare(root, Path(rel), None, None, in_repo).key == "K"
    inv = runner.prepare(root, Path(rel), None, None, in_repo, runner.RunOptions(fresh=True))
    assert (inv.key, inv.round_no) == (assemble_decide.run_key("p", digest, None, head, 5), 5)
    assert "--fresh" not in inv.again


# --- 現状固定テスト: `runner.final_result` が状態の保存先の記録（prs.json・outputs.json）から作る結果（I-026） ---


@pytest.fixture
def two_steps(in_repo) -> runner.Invocation:
    """ステップ a・b の 2 つのプランの回。状態の保存先を作り、記録はまだ無い。"""
    rel = H.write_plan(in_repo, "p", [H.py_step("a", "pass"), H.py_step("b", "pass")])
    inv = runner.prepare(_root(in_repo), Path(rel), None, None, in_repo)
    inv.state_dir.mkdir(parents=True, exist_ok=True)
    return inv


def _write_records(inv: runner.Invocation, prs=None, outputs=None) -> None:
    if prs is not None:
        (inv.state_dir / records.PRS_FILE).write_text(json.dumps({"prs": prs}), encoding="utf-8")
    if outputs is not None:
        (inv.state_dir / records.OUTPUTS_FILE).write_text(json.dumps(outputs), encoding="utf-8")


def _plan_item(inv: runner.Invocation, **extra) -> dict:
    return {"kind": "plan", "name": "p", "run": inv.run_id, **extra}


def test_final_result_of_a_workflow_without_a_result_is_unknown(two_steps):
    out = runner.final_result(two_steps, None, "ERROR")
    detail = "p の耐久ワークフローが ERROR で終わった"
    assert out == {
        "tool": runner.TOOL,
        "status": "stopped",
        "summary": detail,
        "items": [_plan_item(two_steps)],
        "metrics": {"round": 0, "steps": 0},
        "cause": {"kind": "unknown", "detail": detail},
        "run_id": two_steps.run_id,
    }


def test_final_result_ok_without_records(two_steps):
    out = runner.final_result(two_steps, {"status": "ok", "count": 2}, "SUCCESS")
    assert out == {
        "tool": runner.TOOL,
        "status": "ok",
        "summary": "p が終わった",
        "items": [_plan_item(two_steps)],
        "metrics": {"round": 0, "steps": 2},
        "run_id": two_steps.run_id,
    }


def test_final_result_ok_carries_the_recorded_prs_and_outputs(two_steps):
    _write_records(two_steps, prs=[5, 7], outputs={"pr": 5, "gaps": []})
    out = runner.final_result(two_steps, {"status": "ok", "count": 2}, "SUCCESS")
    assert out["status"] == "ok" and out["summary"] == "p が終わった"
    assert out["items"] == [_plan_item(two_steps), {"kind": "pr", "number": 5}, {"kind": "pr", "number": 7}]
    assert out["outputs"] == {"pr": 5, "gaps": []}


def test_final_result_moves_outputs_recorded_before_the_rename(tmp_path):
    records.write_json(tmp_path / records.OUTPUTS_FILE, {"version": "11.0.23", "pr": 5})
    assert records.recorded_outputs(tmp_path) == ({"product_version": "11.0.23", "pr": 5}, [])
    records._add_outputs(tmp_path, {"product_version": "11.0.24"})
    assert records.recorded_outputs(tmp_path) == ({"product_version": "11.0.24", "pr": 5}, [])


def test_stored_result_moves_outputs_backed_up_before_the_rename(tmp_path):
    old = result.result("plan", "ok", "済んだ", outputs={"version": "11.0.23"})
    records.write_json(runner.result_path(tmp_path, "r1"), old)
    got = runner.stored_result(tmp_path, "r1")
    assert got == {**old, "outputs": {"product_version": "11.0.23"}} and result.errors(got) == []
    assert runner.stored_result(tmp_path, "none") is None


def test_stored_result_of_a_newer_version_is_refused(tmp_path):
    newer = {**result.result("plan", "gate", "待つ", routes={"approved": None}), "version": 2}
    records.write_json(runner.result_path(tmp_path, "r1"), newer)
    with pytest.raises(internal.InternalError) as exc:
        runner.stored_result(tmp_path, "r1")
    assert exc.value.kind == "precondition"


def test_final_result_ok_with_recorded_gaps_is_incomplete(two_steps):
    gap = {"kind": "unresolved_finding", "name": "r1-1", "reason": "x"}
    _write_records(two_steps, prs=[5], outputs={"pr": 5, "gaps": [gap]})
    out = runner.final_result(two_steps, {"status": "ok", "count": 2}, "SUCCESS")
    detail = "p は終わったが、ステップが残した欠け 1 件がある"
    assert (out["status"], out["summary"], out["cause"]) == ("stopped", detail, {"kind": "incomplete", "detail": detail})
    assert out["gaps"] == [gap] and out["outputs"] == {"pr": 5, "gaps": [gap]}
    assert out["items"] == [_plan_item(two_steps), {"kind": "pr", "number": 5}]
    assert result.errors(out) == []


def test_final_result_drops_outputs_out_of_the_contract_with_a_warning(two_steps):
    _write_records(two_steps, outputs={"unknown_key": 1})
    out = runner.final_result(two_steps, {"status": "ok", "count": 1}, "SUCCESS")
    assert out["status"] == "ok" and "outputs" not in out
    assert len(out["warnings"]) == 1 and out["warnings"][0].startswith("ステップの outputs が結果 JSON の形に合わず載せなかった: ")


def test_final_result_stopped_points_next_at_the_last_step(two_steps):
    final = {"status": "stopped", "count": 1, "last": "a", "kind": "work_stopped", "detail": "落ちた"}
    out = runner.final_result(two_steps, final, "SUCCESS")
    assert (out["status"], out["summary"], out["cause"]) == ("stopped", "落ちた", {"kind": "work_stopped", "detail": "落ちた"})
    assert out["items"] == [_plan_item(two_steps, step="a")]
    assert out["metrics"] == {"round": 0, "steps": 1}
    assert out["next"] == {"argv": ["ndf", "plan", "next", str(two_steps.plan_path), "--from", "a"]}


def test_final_result_incomplete_carries_the_gaps_the_step_left(two_steps):
    # PR 4307 の r8-2: worker が必須の出力を書き忘れて止まった回は、欠けたキーを停止結果の gaps に持つ
    gap = {"kind": "output", "name": "branch", "reason": "作業の結果ファイルの outputs に無い"}
    records._keep_step_gaps(two_steps.state_dir, "a", [gap])
    final = {"status": "stopped", "count": 1, "last": "a", "kind": "incomplete", "detail": "ステップ a が incomplete で止まった"}
    out = runner.final_result(two_steps, final, "SUCCESS")
    assert out["cause"]["kind"] == "incomplete" and out["gaps"] == [gap] and result.errors(out) == []
    records._keep_step_gaps(two_steps.state_dir, "a", None)  # 打ち直しで通ったステップの欠けは残さない
    assert records.recorded_step_gaps(two_steps.state_dir) == {}


def test_final_result_incomplete_names_the_missing_outputs_in_its_summary(two_steps):
    # #3908: 工程の failed の理由はプランの停止結果の説明をそのまま載せるので、欠けの名前を説明の末尾に添える
    left = [{"kind": "output", "name": n, "reason": "作業の結果ファイルの outputs に無い"} for n in ("worktree", "branch")]
    records._keep_step_gaps(two_steps.state_dir, "a", left)
    final = {"status": "stopped", "count": 1, "last": "a", "kind": "incomplete", "detail": "ステップ a が incomplete で止まった"}
    out = runner.final_result(two_steps, final, "SUCCESS")
    detail = "ステップ a が incomplete で止まった（欠け: branch, worktree）"
    assert (out["summary"], out["cause"]["detail"], out["gaps"]) == (detail, detail, left) and result.errors(out) == []


def test_final_result_incomplete_without_step_gaps_fills_one(two_steps):
    # 欠けの詳細を持たない incomplete（JSON を出さないステップなど）は、止まったステップの欠けを補って形に合わせる
    final = {"status": "stopped", "count": 1, "last": "a", "kind": "incomplete", "detail": "ステップ a が incomplete で止まった"}
    out = runner.final_result(two_steps, final, "SUCCESS")
    assert out["gaps"] == [{"kind": "step", "name": "a", "reason": "ステップ a が incomplete で止まった"}]
    assert result.errors(out) == []
    assert "gaps" not in runner.final_result(two_steps, {**final, "kind": "work_stopped"}, "SUCCESS")


def test_final_result_stopped_without_a_last_step_has_no_next(two_steps):
    final = {"status": "stopped", "count": 0, "last": None, "kind": "work_stopped", "detail": "落ちた"}
    out = runner.final_result(two_steps, final, "SUCCESS")
    assert out["items"] == [_plan_item(two_steps, step=None)]
    assert "next" not in out


@pytest.mark.parametrize(("last", "approved"), [("a", ["--from", "b"]), ("b", None)])
def test_final_result_gate_routes_approval_to_the_following_step(two_steps, last, approved):
    out = runner.final_result(two_steps, {"status": "gate", "count": 1, "last": last, "detail": "承認を待つ"}, "SUCCESS")
    assert (out["status"], out["summary"], out["items"]) == ("gate", "承認を待つ", [_plan_item(two_steps)])
    cont = {"argv": ["ndf", "plan", "next", str(two_steps.plan_path), *approved]} if approved else None
    assert out["routes"] == {"approved": cont, "rejected": None}


# --- `runner._result_of`: 状態の保存先を読まずに、渡した記録から結果を組む（I-026） ---


def _records(inv: runner.Invocation, steps: int, outputs=None) -> "runner._FinalRecords":
    items = [_plan_item(inv), {"kind": "pr", "number": 9}]
    return runner._FinalRecords(items, {"round": 0, "steps": steps}, outputs, {"outputs": outputs, "warnings": [], "run_id": inv.run_id})


def test_result_of_builds_from_the_given_records_without_reading_the_state_dir(two_steps):
    _write_records(two_steps, prs=[5], outputs={"pr": 5, "gaps": [{"kind": "k", "name": "n", "reason": "r"}]})
    out = runner._result_of(two_steps, {"status": "ok", "count": 2}, "SUCCESS", _records(two_steps, 2))
    assert (out["status"], out["summary"]) == ("ok", "p が終わった")
    assert out["items"] == [_plan_item(two_steps), {"kind": "pr", "number": 9}]
    assert "outputs" not in out


def test_result_of_keeps_the_given_items_when_it_stops_at_a_step(two_steps):
    rec = _records(two_steps, 1)
    final = {"status": "stopped", "count": 1, "last": "a", "kind": "work_stopped", "detail": "落ちた"}
    out = runner._result_of(two_steps, final, "SUCCESS", rec)
    assert out["items"] == [_plan_item(two_steps, step="a"), {"kind": "pr", "number": 9}]
    assert rec.items[0] == _plan_item(two_steps)


def test_result_of_with_gaps_in_the_given_outputs_is_incomplete(two_steps):
    gap = {"kind": "unresolved_finding", "name": "r1-1", "reason": "x"}
    out = runner._result_of(two_steps, {"status": "ok", "count": 2}, "SUCCESS", _records(two_steps, 2, {"gaps": [gap]}))
    assert (out["status"], out["cause"]["kind"], out["gaps"]) == ("stopped", "incomplete", [gap])


# --- 承認待ちへの回答の記録（#4409 の決定 7） ---


@pytest.fixture
def gated(in_repo) -> runner.Invocation:
    """承認待ちのステップ merge（on_answer を持つ）のプランの回。"""
    steps = [H.py_step("merge", "pass", on_answer={"approved": "end", "rejected": "end"})]
    rel = H.write_plan(in_repo, "g", steps)
    inv = runner.prepare(_root(in_repo), Path(rel), None, None, in_repo)
    inv.state_dir.mkdir(parents=True, exist_ok=True)
    return inv


def _routes_answer_with_the_user_as_the_actor(gated):
    """受け入れ条件 4: 承認待ちの結果の経路は `ndf plan next <プラン> --answer <回答> --by human`。"""
    argv = runner._routes(gated)["merge"]["approved"]["argv"]
    assert argv[-4:] == ["--answer", "approved", "--by", "human"]


def _the_answer_record_copies_the_presented_id_of_the_waiting_step(gated):
    runner.write_json(runner.step_presented_path(gated.state_dir, "merge"), {"step": "merge", "presented_id": "p1"})
    runner._record_answer(gated, {"step": "merge", "seq": 1, "routes": {}}, "approved", "human")
    got = json.loads(runner.answer_path(gated.state_dir, "merge").read_text(encoding="utf-8"))
    assert (got["step"], got["answer"], got["by"], got["run"], got["presented_id"]) == ("merge", "approved", "human", gated.run_id, "p1")


def _the_answer_record_without_a_presented_id_holds_null(gated):
    runner._record_answer(gated, {"step": "merge", "seq": 1, "routes": {}}, "rejected", "human")
    assert json.loads(runner.answer_path(gated.state_dir, "merge").read_text(encoding="utf-8"))["presented_id"] is None


def _the_step_result_carries_the_presented_id_of_a_gate():
    from ndf.core.plan import steps

    step = model.Step(id="merge", run=["x"])
    out = steps.outcome_of(
        step, 10, json.dumps(result.result("ci", "gate", "待つ", metrics={"presented_id": "p1"}, routes={"approved": None})) + "\n"
    )
    assert out["presented_id"] == "p1"
    ok = steps.outcome_of(step, 0, json.dumps(result.result("ci", "ok", "済んだ", metrics={"presented_id": "p1"})) + "\n")
    assert ok["presented_id"] is None


def _next_refuses_an_actor_of_the_wrong_shape(capsys):
    from ndf.core.plan import cli

    assert cli.main(["next", "p.json", "--answer", "approved", "--by", "人"]) != 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["cause"]["kind"] == "invalid"


@pytest.mark.parametrize(
    scenes.ARGS,
    [
        scenes.scene(_routes_answer_with_the_user_as_the_actor, id="routes_answer_with_the_user_as_the_actor"),
        scenes.scene(
            _the_answer_record_copies_the_presented_id_of_the_waiting_step,
            id="the_answer_record_copies_the_presented_id_of_the_waiting_step",
        ),
        scenes.scene(_the_answer_record_without_a_presented_id_holds_null, id="the_answer_record_without_a_presented_id_holds_null"),
        scenes.scene(_the_step_result_carries_the_presented_id_of_a_gate, id="the_step_result_carries_the_presented_id_of_a_gate"),
        scenes.scene(_next_refuses_an_actor_of_the_wrong_shape, id="next_refuses_an_actor_of_the_wrong_shape"),
    ],
)
def test_answer_record_scenes(request, scene, values):
    """承認ゲートの回答の記録と提示の識別子の場面（#4409 の決定 7）。"""
    scenes.play(request, scene, values)
