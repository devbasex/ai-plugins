"""工程を流すサブコマンド（ndf.progress.run。#3092 の受け入れ条件 4・6・7・8、3364-7・3365-4・3366-1・3377-9、SF8）。

作業（プラン）は偽物（`Fake`）に差し替え、`next` が起動する作業は同じプロセスで `run.work` として流す。
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import git_sandbox
import pytest
import sprint_fixture
import strategy_fixture
from result_json import last_result

from ndf import contracts
from ndf.adapters.github import GitHubError
from ndf.contracts import causes, result
from ndf.core import route
from ndf.progress import command, definition, gate, invoke, model, records, run, stages, store

ISSUE = 3092


def _load_lanes():
    """同じ保存先のレーンの部品（lanes.py。偽の作業の結果と 1 手の打ち方）。"""
    spec = importlib.util.spec_from_file_location("lanes", Path(__file__).with_name("lanes.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lanes = _load_lanes()


def _main(capsys, fn, *argv: str) -> tuple[int, dict]:
    code = fn(list(argv))
    return code, last_result(capsys.readouterr().out)


def _new(capsys, mode: str = "standard", *extra: str) -> str:
    code, out = _main(capsys, command.main, "new", "s1", "--mode", mode, "--issues", str(ISSUE), *extra)
    assert code == 0, out
    return out["metrics"]["machine_id"]


class Fake:
    """作業の偽物。`script[ステップ]` に積んだ結果を順に返し、無ければ定義した出力を持つ ok を返す。"""

    def __init__(self, repo: Path) -> None:
        self.values = lanes.values(repo, "sprint/s1")
        self.calls: list[SimpleNamespace] = []
        self.script: dict[str, list] = {}

    def ok(self, r: invoke.StageRun) -> invoke.StageResult:
        return lanes.ok_result(r, self.values)

    def __call__(self, r, cwd, work_dir, answer):
        self.calls.append(
            SimpleNamespace(step=r.step, answer=answer, notes=r.notes, inputs=r.inputs, work_dir=work_dir, retries=r.retries, again=r.again)
        )
        queued = self.script.get(r.step)
        return queued.pop(0)(r) if queued else self.ok(r)


class Records:
    def __init__(self) -> None:
        self.bodies: dict[int, str] = {}
        self.writes = 0
        self.broken = False

    def read(self, number: int) -> str:
        if self.broken:
            raise GitHubError("書けない", 502)
        return self.bodies.get(number, "本文\n")

    def write(self, number: int, body: str) -> None:
        self.bodies[number] = body
        self.writes += 1


@pytest.fixture
def env(repo):
    fake, recs, launched, slept = Fake(repo), Records(), [], []
    e = run.Env(
        facts=lambda words, ctx, cwd: {w: True for w in words},
        launch=lambda st_, tid, cwd: launched.append(tid),
        invoker=fake,
        prepare=lambda r, work_dir, cwd: None,  # 作業が偽物なのでプランの差し込みも組まない
        records=lambda cwd: recs,
        sleep=slept.append,
    )
    return SimpleNamespace(env=e, fake=fake, records=recs, launched=launched, slept=slept)


def _labels(st, tid) -> dict[str, str]:
    v = run.read(st, tid)
    return {s.name: (v.rows[s.name].state if s.name in v.rows else None) for s in v.snap.stages}


def _approve(st, tid, stage, value="approved", notes=None):
    return lanes.answer(st, tid, stage, value, "人が回答した", notes)


def test_next_starts_the_first_pending_row_and_returns_wait(repo, st, env, capsys):
    tid = _new(capsys)
    out = run.advance(st, tid, repo, env.env)
    assert out["status"] == "ok" and out["next"]["argv"] == ["ndf", "progress", "wait", tid]
    assert env.launched == [tid]
    assert _labels(st, tid)["要求と受け入れ条件"] == "running"
    started = [e for e in run.read(st, tid).events if e.draft.kind == "started"][-1]
    assert started.draft.payload == {"inputs": {"issues": [ISSUE], "mode": "standard"}}


def test_work_flows_rows_until_the_design_gate_and_returns_routes(repo, st, env, capsys):
    tid = _new(capsys)
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate" and set(out["routes"]) == {"approved", "rejected", "request_changes"}
    assert out["routes"]["request_changes"]["ask"] == ["notes", "reason"]
    assert result.code_for(out) == 10
    labels = _labels(st, tid)
    assert labels["要求と受け入れ条件"] == labels["作業場所の用意"] == "finished" and labels["設計"] == "running"
    assert [c.step for c in env.fake.calls] == ["requirements", "worktree", "design", "design-review", "design-mvv"]
    # 作業場所の用意の出力が文脈に入り、設計の入力に届く（MVV 判定の材料のモード・進め方・承認済み MVV の参照も）
    assert env.fake.calls[2].inputs == {"issues": [ISSUE], "worktree": str(repo), "mode": "standard", "pace": "normal", "mvv_ref": None}


def test_answers_carry_the_sprint_to_completion_and_the_records_follow_the_marks(repo, st, env, capsys):
    tid = _new(capsys)
    assert lanes.turn(st, tid, repo, env.env)["metrics"]["stage"] == "設計"
    _approve(st, tid, "設計")
    gate = lanes.turn(st, tid, repo, env.env)
    assert gate["status"] == "gate" and gate["metrics"]["stage"] == "デプロイ"
    _approve(st, tid, "デプロイ")
    done = lanes.turn(st, tid, repo, env.env)
    assert done["status"] == "ok" and done["metrics"]["state"] == "completed"
    assert set(_labels(st, tid).values()) == {"finished"}
    body = env.records.bodies[ISSUE]
    assert body.startswith("本文\n\n## 進行\n\nモード: standard\n")
    assert body.count("- [x] ") == len(run.read(st, tid).snap.stages) and "- [ ] " not in body
    assert "## 通した工程" in env.records.bodies[8]
    # 揃え直しは何度打っても同じ（3377-9）
    writes = env.records.writes
    again = run.resync(st, tid, repo, env.env)
    assert again["status"] == "ok" and {i["changed"] for i in again["items"]} == {False}
    assert env.records.writes == writes
    assert run.advance(st, tid, repo, env.env)["metrics"]["move"] == "done"


def test_finished_steps_are_not_run_again_after_the_gate(repo, st, env, capsys):
    tid = _new(capsys)
    lanes.turn(st, tid, repo, env.env)
    before = len(env.fake.calls)
    _approve(st, tid, "設計")
    lanes.turn(st, tid, repo, env.env)
    after = [c.step for c in env.fake.calls[before:]]
    assert after[0] == "design-merge" and not {"design", "design-review"} & set(after)


def test_rejected_gate_fails_the_row_and_returns_the_conductor_routes(repo, st, env, capsys):
    tid = _new(capsys)
    lanes.turn(st, tid, repo, env.env)
    _approve(st, tid, "設計", "rejected")
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "stopped" and out["cause"]["kind"] == "refused"
    assert set(out["routes"]) == {"retry", "finish", "cancel"}
    assert out["routes"]["finish"]["ask"] == ["output", "reason"]
    assert result.code_for(out) == causes.exit_code("refused")


def test_revise_needs_a_notes_file_and_reruns_the_stage_with_the_notes(repo, st, env, capsys, tmp_path):
    tid = _new(capsys)
    lanes.turn(st, tid, repo, env.env)
    with pytest.raises(model.Refused):
        _approve(st, tid, "設計", "request_changes")
    empty = tmp_path / "empty.md"
    empty.write_text("  \n")
    with pytest.raises(model.Refused):
        _approve(st, tid, "設計", "request_changes", empty)
    notes = tmp_path / "notes.md"
    notes.write_text("セクション 2 の根拠が足りない")
    _approve(st, tid, "設計", "request_changes", notes)
    answered = [e for e in run.read(st, tid).events if e.draft.kind == "answered"][-1]
    assert answered.draft.payload["notes"] == "セクション 2 の根拠が足りない"
    before = len(env.fake.calls)
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate" and out["metrics"]["stage"] == "設計"
    rerun = env.fake.calls[before:]
    assert [c.step for c in rerun] == ["design", "design-review", "design-mvv"] and {c.notes for c in rerun} == {
        "セクション 2 の根拠が足りない"
    }
    assert rerun[0].work_dir.name == "design-g1"


JUDGED = "a" * 40


def _mvv(kind: str, skip: bool, why: list[str]):
    """承認ゲートの直前の MVV 判定のステップの偽の結果（出力 mvv_gate。判定した PR の head は JUDGED。レビューの保留を確かめて 0 件）。"""
    held = {
        "gate": kind,
        "skip": skip,
        "verdict": {"verdict": "follow", "reasons": ["Value 1"], "boundary": []},
        "why": why,
        "head": JUDGED,
        "holds": [],
    }
    return lambda r: invoke.finished(r, result.result("script", "ok", "MVV 判定", outputs={"mvv_gate": held}))


def test_a_skippable_mvv_verdict_passes_the_design_gate_as_service_mvv(repo, st, env, capsys):
    tid = _new(capsys, "standard", "--pace", "fast")
    env.fake.script["design-mvv"] = [_mvv("design", True, [])]
    # 省いた回答を書いた駆動はそこで止まり、回答は次の next が検証（recheck）を通してから読む
    wrote = lanes.turn(st, tid, repo, env.env)
    assert wrote["status"] == "ok" and wrote["next"]["argv"][2] == "next" and wrote["metrics"]["stage"] == "設計"
    assert gate.recheck(st, tid, lambda q: gate.Target(JUDGED)) is None
    out = lanes.turn(st, tid, repo, env.env)
    # 設計承認ゲートは人へ問わずに通り、次に人を待つのはリリース承認ゲート（判定は省かない）
    assert out["status"] == "gate" and out["metrics"]["stage"] == "デプロイ"
    assert _labels(st, tid)["設計"] == "finished"
    (answered,) = [e for e in run.read(st, tid).events if e.draft.kind == "answered"]
    p = answered.draft.payload
    assert (answered.draft.row_key, p["answer"], p["by"], p["step"]) == ("設計", "approved", "service:mvv", "design-gate")
    assert p["mvv_gate"]["skip"] is True and answered.draft.actor == "conductor"
    # 人の回答と同じく質問の id・区分・見た head を持ち、承認済みのコミット（ndf answer head）が読める
    assert (p["gate"], p["seen"]["head"], len(p["gate_id"])) == ("design", JUDGED, model.QUESTION_ID_LENGTH)
    assert gate.approved_head(run.read(st, tid), "design") == JUDGED


def test_an_mvv_approval_whose_head_moved_goes_back_to_a_human(repo, st, env, capsys):
    # 省いた回答を書いた後、読む前に PR の head が判定の head から進んだら、待ちへ戻して人へ問う（同じ判定で省き直さない）
    tid = _new(capsys, "standard", "--pace", "fast")
    env.fake.script["design-mvv"] = [_mvv("design", True, [])]
    lanes.turn(st, tid, repo, env.env)  # 省いた回答を書いたところで駆動が止まる
    moved = "b" * 40
    why = gate.recheck(st, tid, lambda q: gate.Target(moved))
    assert why is not None and moved in why
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate" and out["metrics"]["stage"] == "設計"
    assert [e.draft.payload["by"] for e in run.read(st, tid).events if e.draft.kind == "answered"] == ["service:mvv"]


@pytest.mark.parametrize(
    "held",
    [
        _mvv("design", False, ["レッドラインに当たる: C2・C4"]),
        # 別の区分の承認ゲートを省くと決めた判定は、この承認ゲートを省かない
        _mvv("release", True, []),
    ],
)
def test_an_mvv_verdict_that_may_not_skip_waits_for_a_human(repo, st, env, capsys, held):
    tid = _new(capsys, "standard", "--pace", "fast")
    env.fake.script["design-mvv"] = [held]
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate" and out["metrics"]["stage"] == "設計"
    assert not [e for e in run.read(st, tid).events if e.draft.kind == "answered"]


def test_the_dedicated_mvv_verdict_that_keeps_the_gate_wins_over_an_earlier_skip(repo, st, env, capsys):
    # 先の設計のステップが省けると書いても、直前の判定がレッドラインで止めた承認ゲートは省かない
    tid = _new(capsys, "standard", "--pace", "fast")
    env.fake.script["design"] = [_mvv("design", True, [])]
    env.fake.script["design-mvv"] = [_mvv("design", False, ["レッドラインに当たる: C2"])]
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate" and out["metrics"]["stage"] == "設計"
    assert not [e for e in run.read(st, tid).events if e.draft.kind == "answered"]


def test_new_writes_the_approved_mvv_reference_into_the_context(repo, st, capsys):
    (repo / ".ndf").mkdir()
    sha = "a" * 64
    (repo / ".ndf" / "mvv.json").write_text(json.dumps({"versions": [{"version": 1, "sha256": "b" * 64}, {"version": 2, "sha256": sha}]}))
    tid = _new(capsys)
    assert run.read(st, tid).context()["mvv_ref"] == sha


def test_answer_is_refused_where_nothing_waits(repo, st, env, capsys):
    tid = _new(capsys)
    with pytest.raises(model.Refused):
        _approve(st, tid, "設計")
    lanes.turn(st, tid, repo, env.env)
    with pytest.raises(model.Refused):
        _approve(st, tid, "設計", "retry")


def test_plan_gate_waits_and_the_answer_is_passed_back_to_the_plan(repo, st, env, capsys):
    tid = _new(capsys)
    routes = {"approved": result.command("plan", "next", "x"), "rejected": None}
    env.fake.script["design-review"] = [lambda r: invoke.StageResult(result.result("plan", "gate", "判断を待つ", routes=routes), {})]
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate" and out["metrics"]["step"] == "design-review" and set(out["routes"]) == {"approved", "rejected"}
    _approve(st, tid, "設計")
    out = lanes.turn(st, tid, repo, env.env)
    review = [c for c in env.fake.calls if c.step == "design-review"]
    assert [c.answer for c in review] == [None, "approved"]
    assert out["metrics"]["step"] == "design-gate"


def test_retryable_causes_are_retried_here_with_the_declared_backoff(repo, st, env, capsys):
    tid = _new(capsys)
    flaky = lambda r: invoke.StageResult(result.stopped("plan", "transient", "5xx"), {})  # noqa: E731
    env.fake.script["worktree"] = [flaky, flaky]
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate"
    assert env.slept == [2.0, 4.0]
    # 打ち直しは作業へ打ち直しと知らせ、プランは新しい回を始める
    assert [c.again for c in env.fake.calls if c.step == "worktree"] == [False, True, True]


def test_a_plan_that_returns_the_same_round_again_is_not_retried_further(repo, st, env, capsys):
    # PR 3464 の指摘: 打ち直してもバックアップの結果しか返らない回で、待ちと起動だけを繰り返さない
    tid = _new(capsys)
    same = lambda r: invoke.StageResult(result.stopped("plan", "transient", "5xx", run_id="r1"), {})  # noqa: E731
    env.fake.script["worktree"] = [same, same, same, same]
    out = lanes.turn(st, tid, repo, env.env)
    assert out["cause"]["kind"] == "transient"
    assert [c.step for c in env.fake.calls].count("worktree") == 2 and env.slept == [2.0]


def test_failures_that_need_a_decision_return_routes_and_the_exit_code_of_the_cause(repo, st, env, capsys):
    tid = _new(capsys)
    env.fake.script["worktree"] = [lambda r: invoke.StageResult(result.stopped("plan", "test_failed", "落ちた"), {})]
    out = lanes.turn(st, tid, repo, env.env)
    assert out["cause"]["kind"] == "test_failed" and set(out["routes"]) == {"retry", "finish", "cancel"}
    assert result.code_for(out) == causes.exit_code("test_failed") != 6
    assert _labels(st, tid)["作業場所の用意"] == "failed"
    # 失敗の行は conductor が retry で戻すまで流さない。戻せば失敗したステップから流し直す
    assert run.advance(st, tid, repo, env.env)["cause"]["kind"] == "test_failed"
    _main(capsys, command.main, "retry", tid, "作業場所の用意", "--reason", "直した")
    assert lanes.turn(st, tid, repo, env.env)["status"] == "gate"
    assert [c.retries for c in env.fake.calls if c.step == "worktree"] == [0, 1]


def test_stage_output_that_breaks_the_contract_gets_no_finish_mark(repo, st, env, capsys):
    # 受け入れ条件 8（出力の検証）
    tid = _new(capsys)
    env.fake.script["worktree"] = [lambda r: invoke.finished(r, result.result("plan", "ok", "出力なし"))]
    out = lanes.turn(st, tid, repo, env.env)
    assert out["cause"]["kind"] == "bad_report" and "worktree" in out["cause"]["detail"]
    assert _labels(st, tid)["作業場所の用意"] == "failed"


def test_retry_after_a_stage_output_that_breaks_the_contract_runs_the_steps_again(repo, st, env, capsys):
    # PR 3464 の指摘: 全ステップが終わったまま失敗すると、retry → next でも同じ出力を照らし直すだけになる
    tid = _new(capsys)
    env.fake.script["worktree"] = [lambda r: invoke.finished(r, result.result("plan", "ok", "出力なし"))]
    assert lanes.turn(st, tid, repo, env.env)["cause"]["kind"] == "bad_report"
    failed = [e for e in run.read(st, tid).events if e.draft.kind == "failed"][-1]
    assert failed.draft.payload["step"] is None  # 工程全体の出力の検査で落ちた
    before = len(env.fake.calls)
    _main(capsys, command.main, "retry", tid, "作業場所の用意", "--reason", "作り直す")
    assert lanes.turn(st, tid, repo, env.env)["status"] == "gate"
    again = env.fake.calls[before:]
    assert [c.step for c in again][0] == "worktree"
    # 終わった回のバックアップの結果を受け取らないよう、行の打ち直しの回数を渡し、プランは同じ保存先で新しい回を流す
    first = [c for c in env.fake.calls[:before] if c.step == "worktree"][-1]
    assert first.work_dir == again[0].work_dir and (first.retries, again[0].retries) == (0, 1)
    assert _labels(st, tid)["作業場所の用意"] == "finished"


def test_a_plan_gate_in_a_step_with_an_output_contract_is_recorded_as_waiting():
    # PR 3464 の指摘: 承認ゲートの待ちの結果は出力を持たないので、出力の規約で bad_report にしない
    s = next(s for s in definition.load_definition("standard").stages if any(st.id == "release" for st in s.steps))
    step = next(st for st in s.steps if st.id == "release")
    assert step.output_ref
    gate = invoke.StageResult(result.result("plan", "gate", "承認を待つ", routes={"approved": None}), {})
    draft = run._step_draft(SimpleNamespace(rev=1), s, step, run.StepProgress(), gate)
    assert draft.kind == run.STEP_EVENT and draft.payload["status"] == "waiting"


def test_retry_after_a_failed_step_keeps_the_steps_that_finished(repo, st, env, capsys):
    tid = _new(capsys)
    lanes.turn(st, tid, repo, env.env)
    _approve(st, tid, "設計")
    env.fake.script["implement"] = [lambda r: invoke.StageResult(result.stopped("plan", "test_failed", "落ちた"), {})]
    lanes.turn(st, tid, repo, env.env)
    failed = [e for e in run.read(st, tid).events if e.draft.kind == "failed"][-1]
    stage = failed.draft.row_key
    assert failed.draft.payload["step"] == "implement"
    prog = run.step_progress(run.read(st, tid), stage)
    done = dict(prog.done)
    _main(capsys, command.main, "retry", tid, stage, "--reason", "直した")
    run.advance(st, tid, repo, env.env)  # 行を始め直す（作業は起動するだけ）
    assert run.step_progress(run.read(st, tid), stage).done == done


def test_finish_by_hand_writes_the_outputs_to_the_context_like_the_workflow(repo, st, capsys, tmp_path):
    tid = _new(capsys)
    out_file = tmp_path / "worktree.json"
    outputs = {"worktree": str(repo), "branch": "sprint/s1"}
    out_file.write_text(json.dumps({"steps": [], "summary": "手で用意した", "outputs": outputs}))
    code, out = _main(capsys, command.main, "finish", tid, "作業場所の用意", "--output", str(out_file), "--reason", "手で行った")
    assert code == 0, out
    v = run.read(st, tid)
    assert v.rows["作業場所の用意"].state == "finished"
    assert {k: v.context()[k] for k in outputs} == outputs
    recorded = [e for e in v.events if e.draft.kind == stages.OUTPUTS_EVENT]
    assert [e.draft.actor for e in recorded] == ["conductor"]


def test_badly_shaped_outputs_are_not_passed_on(repo, st, env, capsys):
    # 3364-7: 次の工程の引数になる値は形を確かめてから渡す
    tid = _new(capsys)
    bad = {"worktree": str(repo), "branch": "--exec=x"}
    env.fake.script["worktree"] = [lambda r: invoke.finished(r, result.result("plan", "ok", "済んだ", outputs=bad))]
    out = lanes.turn(st, tid, repo, env.env)
    assert out["cause"]["kind"] == "bad_report"
    assert "branch" not in run.read(st, tid).context()


def test_false_condition_disables_the_row_with_the_word(repo, st, env, capsys):
    tid = _new(capsys, "standard")
    env.env = run.Env(**{**vars(env.env), "facts": lambda words, ctx, cwd: {w: w != "code_in_diff" for w in words}})
    lanes.turn(st, tid, repo, env.env)
    _approve(st, tid, "設計")
    lanes.turn(st, tid, repo, env.env)
    row = run.read(st, tid).rows["リファクタリング"]
    assert row.state == "disabled" and row.reason == "条件 code_in_diff が偽"


def test_stage_enabled_by_the_project_runs_even_when_the_condition_is_false(repo, st, env, capsys):
    # 設定 > 定義の条件: workflow.stages の enabled=true は code_in_diff が偽でも行を飛ばさない
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "project.json").write_text(json.dumps({"workflow": {"stages": {"リファクタリング": {"enabled": True}}}}))
    tid = _new(capsys, "standard")
    env.env = run.Env(**{**vars(env.env), "facts": lambda words, ctx, cwd: {w: w != "code_in_diff" for w in words}})
    lanes.turn(st, tid, repo, env.env)
    _approve(st, tid, "設計")
    lanes.turn(st, tid, repo, env.env)
    events = [e.draft.kind for e in run.read(st, tid).row_events("リファクタリング")]
    assert "skipped" not in events and "started" in events


def test_conditions_that_cannot_be_decided_stop_before_the_row(repo, st, env, capsys):
    tid = _new(capsys, "standard")
    env.env = run.Env(**{**vars(env.env), "facts": command.facts_of})
    lanes.turn(st, tid, repo, env.env)
    _approve(st, tid, "設計")
    out = lanes.turn(st, tid, repo, env.env)
    assert out["cause"]["kind"] == "precondition" and "code_in_diff" in out["cause"]["detail"]
    assert _labels(st, tid)["リファクタリング"] == "pending"


def test_facts_of_reads_the_ndf_settings_and_the_context(repo):
    words = ("ci_declared", "design_pr", "prod_branch", "release_record")
    strategy_fixture.write(repo, "web-trunk")  # trunk はベースブランチと本番ブランチが同じ
    assert command.facts_of(words, {}, repo) == dict.fromkeys(words, False)
    strategy_fixture.write(repo, "web-develop", {"test": {"ci": {"only": True}}})
    assert command.facts_of(words, {"design_pr": 3, "product_version": "11.0.0"}, repo) == dict.fromkeys(words, True)
    # 文脈の形のバージョン（version）だけなら、まだ出していない
    assert command.facts_of(("release_record",), {"version": 1}, repo) == {"release_record": False}


@pytest.mark.parametrize("kind", strategy_fixture.KINDS)
def test_prod_branch_follows_the_branch_strategy(repo, kind):
    strategy_fixture.write(repo, kind)
    _, sprint_pr, _, release_pr = strategy_fixture.EXPECTED[kind]
    assert command.facts_of(("prod_branch",), {}, repo) == {"prod_branch": sprint_pr != release_pr}


def test_prod_branch_cannot_be_decided_without_the_strategy(repo):
    with pytest.raises(command.FactUnknown, match="ndf project branch-strategy init"):
        command.facts_of(("prod_branch",), {}, repo)
    strategy_fixture.write(repo, "docs")
    (repo / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "production_branch": "main"}))
    with pytest.raises(command.FactUnknown, match="ndf project branch-strategy migrate"):
        command.facts_of(("prod_branch",), {}, repo)


def test_facts_of_reads_a_ndf_settings_that_is_not_an_object_as_empty_and_refuses_broken_json(repo):
    # 現状固定（I-018）: 設定の読み方は command._declared と同じ（オブジェクトでなければ空、読めなければ ContractError）
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "project.json").write_text("[1]")
    assert command.facts_of(("ci_declared",), {}, repo) == {"ci_declared": False}
    (repo / ".ndf" / "project.json").write_text("{")
    with pytest.raises(contracts.ContractError, match="を読めない"):
        command.facts_of(("ci_declared",), {}, repo)


def _sprint_branch(repo: Path, *changed: str) -> None:
    """ベースブランチ develop から sprint/s1 を切り、`changed` のファイルを足すコミットを積む。"""
    git_sandbox.sh(repo, "checkout", "-q", "-b", "develop", identity=True)
    git_sandbox.sh(repo, "commit", "-q", "--allow-empty", "-m", "base", identity=True)
    git_sandbox.sh(repo, "checkout", "-q", "-b", "sprint/s1", identity=True)
    for name in changed:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("x\n")
    if changed:
        git_sandbox.sh(repo, "add", "--", *changed, identity=True)
    git_sandbox.sh(repo, "commit", "-q", "--allow-empty", "-m", "change", identity=True)
    strategy_fixture.write(repo, "web-develop")


@pytest.mark.parametrize(
    ("changed", "kinds"),
    [
        (["src/a.py", "README.md"], (True, False, False)),
        (["docs/a.md"], (False, True, False)),
        (["plugins/x/skills/y/SKILL.md", "docs/a.md"], (False, True, False)),  # 振る舞いの文書の既定は無し（#3617）
        ([], (False, False, False)),
    ],
)
def test_diff_words_split_the_sprint_diff_into_code_and_docs(repo, changed, kinds):
    _sprint_branch(repo, *changed)
    words = ("code_in_diff", "docs_only_diff", "behavior_docs_in_diff")
    ctx = {"worktree": str(repo), "branch": "sprint/s1"}
    assert tuple(command.facts_of(words, ctx, repo)[w] for w in words) == kinds


def test_diff_words_follow_the_docs_ndf_settings(repo):
    _sprint_branch(repo, "a.txt", "rules/b.txt")
    words = ("code_in_diff", "docs_only_diff", "behavior_docs_in_diff")
    ctx = {"worktree": str(repo), "branch": "sprint/s1"}
    assert command.facts_of(words, ctx, repo) == {"code_in_diff": True, "docs_only_diff": False, "behavior_docs_in_diff": False}
    docs = {"files": ["**/*.md", "**/*.txt"], "behavior": ["rules/*.txt"]}
    strategy_fixture.write(repo, "web-develop", {"docs": docs})
    assert command.facts_of(words, ctx, repo) == {"code_in_diff": False, "docs_only_diff": False, "behavior_docs_in_diff": True}
    docs["files"] = ["**/*.txt", "!rules/**"]
    strategy_fixture.write(repo, "web-develop", {"docs": docs})
    assert command.facts_of(words, ctx, repo)["code_in_diff"] is True


def test_diff_words_without_a_base_branch_cannot_be_decided(repo):
    with pytest.raises(command.FactUnknown, match="code_in_diff"):
        command.facts_of(("code_in_diff",), {}, repo)
    strategy_fixture.write(repo, "web-develop")
    project = json.loads((repo / ".ndf" / "project.json").read_text(encoding="utf-8"))
    project["branch_strategy"]["branches"]["base"] = "--output=x"
    (repo / ".ndf" / "project.json").write_text(json.dumps(project))
    with pytest.raises(command.FactUnknown, match="- で始まる"):
        command.facts_of(("code_in_diff",), {}, repo)


def test_next_flows_the_refactor_row_from_the_sprint_diff(repo, st, env, capsys):
    _sprint_branch(repo, "README.md")
    tid = _new(capsys, "standard")
    env.env = run.Env(**{**vars(env.env), "facts": command.facts_of})
    lanes.turn(st, tid, repo, env.env)
    _approve(st, tid, "設計")
    lanes.turn(st, tid, repo, env.env)
    row = run.read(st, tid).rows["リファクタリング"]
    assert row.state == "disabled" and row.reason == "条件 code_in_diff が偽"


def test_retro_enabled_needs_a_finished_retrospective_row():
    retro = definition.stages.StageDef("retrospective", "振り返り", "close", ())
    other = definition.stages.StageDef("x", "x", "close", ())

    def view(state, stages=(retro, other)):
        rows = {} if state is None else {"振り返り": model.RowState(state, 1, None)}
        return SimpleNamespace(rows=rows, snap=SimpleNamespace(stages=stages))

    assert run.TABLE_FACTS["retro_enabled"](view("finished"))
    assert not run.TABLE_FACTS["retro_enabled"](view("disabled"))
    assert not run.TABLE_FACTS["retro_enabled"](view(None))
    assert not run.TABLE_FACTS["retro_enabled"](view("finished", (other,)))


@pytest.mark.parametrize("mode", ["light", "operation"])
def test_the_design_of_light_and_operation_has_no_design_gate(mode):
    # #3425: 設計承認ゲートが掛かるモードは工程の定義（ステップの modes）が持つ
    steps = definition.load_definition(mode).stage("設計").steps
    assert "design-gate" not in [st.id for st in steps] and steps[-1].id == "design-merge"


@pytest.mark.parametrize("mode", ["standard", "legacy-refactor", "documentation"])
def test_the_design_of_the_other_modes_keeps_the_design_gate(mode):
    assert definition.load_definition(mode).stage("設計").gates == ("design",)


def test_a_step_naming_an_unknown_mode_is_refused():
    docs = {
        p.stem: contracts.load_json(f"workflow/stages/{p.name}") for p in (contracts.contracts_dir() / "workflow/stages").glob("*.json")
    }
    gate = next(step["design-gate"] for step in docs["design"]["do"] if "design-gate" in step)
    gate["metadata"]["ndf"]["modes"] = ["heavy"]
    with pytest.raises(definition.WorkflowDefError, match="heavy は定義の無いモード"):
        definition.build(contracts.load_json("workflow/modes/standard.json"), docs)


def test_light_flows_the_design_to_the_merge_without_asking(repo, st, env, capsys):
    tid = _new(capsys, "light")
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate" and out["metrics"]["stage"] == "デプロイ"
    assert "design-merge" in [c.step for c in env.fake.calls]


def test_missing_input_does_not_start_the_row(repo, st, env, capsys):
    # 作る時点の --disable は --carry なしでは断られる（#3879）ので、作った後に conductor が無効にする
    tid = _new(capsys, "light")
    assert _main(capsys, command.main, "disable", tid, "作業場所の用意", "--reason", "手で用意した")[0] == 0
    out = lanes.turn(st, tid, repo, env.env)
    # 設計の入力 worktree を書く工程が無効なので、設計は始まらない
    assert out["cause"]["kind"] == "input_missing" and out["items"] == [{"key": "worktree"}]
    assert out["metrics"]["stage"] == "設計" and _labels(st, tid)["設計"] == "pending"


def test_records_failure_does_not_stop_the_work_and_resync_reports_it(repo, st, env, capsys):
    tid = _new(capsys)
    env.records.broken = True
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate" and any("進捗記録" in w for w in out["warnings"])
    bad = run.resync(st, tid, repo, env.env)
    assert bad["status"] == "stopped" and bad["next"]["argv"][2] == "resync"
    env.records.broken = False
    assert run.resync(st, tid, repo, env.env)["status"] == "ok"
    assert "- [x] 作業場所の用意" in env.records.bodies[ISSUE]


def test_replace_section_is_idempotent_and_keeps_the_rest():
    body = "前\n\n## 進行\n\n古い\n<!-- ndf:section-end -->\n\n## 後\n"
    once = records.replace_section(body, records.ISSUE_SECTION, "新しい")
    assert once == "前\n\n## 進行\n\n新しい\n<!-- ndf:section-end -->\n\n## 後\n"
    assert records.replace_section(once, records.ISSUE_SECTION, "新しい") == once
    assert records.replace_section("", records.PR_SECTION, "x") == "## 通した工程\n\nx\n<!-- ndf:section-end -->\n"


def test_retry_delay_follows_the_cause_table():
    assert causes.retry_delay(causes.cause("transient", "x"), 0) == 2.0
    assert causes.retry_delay(causes.cause("transient", "x"), 5) is None
    assert causes.retry_delay(causes.cause("test_failed", "x"), 0) is None
    assert causes.retry_delay(causes.cause("usage_limit", "x"), 0) is None
    assert causes.retry_delay(causes.cause("usage_limit", "x", retry_after=30), 0) == 30.0
    assert causes.retry_delay(None, 0) is None


def test_cli_answer_needs_a_known_answer(repo, capsys):
    tid = _new(capsys)
    code, out = _main(capsys, run.main, "answer", tid, "設計", "--answer", "approved", "--reason", "r")
    assert out["cause"]["kind"] == "refused" and code == causes.exit_code("refused")
    assert run.main(["answer", tid, "設計", "--answer", "maybe", "--reason", "r"]) == causes.exit_code("invalid")


def test_other_actions_go_to_the_table_commands(repo, capsys):
    tid = _new(capsys)
    code, out = _main(capsys, run.main, "disable", tid, "実装", "--reason", "外す")
    assert code == 0 and out["status"] == "ok"
    _, shown = _main(capsys, run.main, "show", tid)
    assert shown["next"]["argv"] == ["ndf", "progress", "next", tid]


# --- 現状固定: run.append（I-002。下書きの追記と、番号のぶつかりの打ち直し） ----------------------------


def _started(v: run.View) -> list[model.EventDraft]:
    """未着手の最初の行を始める下書き（作業が書く事象）。"""
    name = next(s.name for s in v.snap.stages if s.name in v.rows and v.rows[s.name].state == "pending")
    return [model.EventDraft("started", run.ACTOR, name, payload={"inputs": {}}, rev=v.rev)]


def test_append_writes_the_drafts_built_from_the_current_view(repo, st, capsys):
    tid = _new(capsys)
    before = run.read(st, tid)
    seen: list[run.View] = []

    def build(v):
        seen.append(v)
        return _started(v)

    seqs = run.append(st, tid, build)
    assert seqs == [before.events[-1].seq + 1]
    assert len(seen) == 1 and seen[0].machine_id == tid and seen[0].rev == before.rev
    assert [e.seq for e in seen[0].events] == [e.seq for e in before.events]
    after = run.read(st, tid)
    assert after.events[-1].seq == seqs[0] and after.events[-1].draft.kind == "started"


def test_append_with_no_drafts_returns_nothing_without_calling_the_store(repo, st, capsys, monkeypatch):
    tid = _new(capsys)
    before = run.read(st, tid).events[-1].seq
    called = []
    monkeypatch.setattr(store, "append_events", lambda *a, **k: called.append(a) or [99])
    assert run.append(st, tid, lambda v: []) == []
    assert called == [] and run.read(st, tid).events[-1].seq == before


def test_append_rebuilds_after_a_conflict_and_raises_when_the_attempts_run_out(repo, st, capsys, conflicts):
    tid = _new(capsys)
    builds: list[run.View] = []
    calls = conflicts(1)
    seqs = run.append(st, tid, lambda v: builds.append(v) or _started(v))
    assert len(builds) == 2 and calls["n"] == 2 and run.read(st, tid).events[-1].seq == seqs[0]

    calls, builds[:] = conflicts(None), []
    with pytest.raises(store.Conflict):
        run.append(st, tid, lambda v: builds.append(v) or _started(v))
    assert calls["n"] == command.append_attempts() and len(builds) == command.append_attempts()


def test_append_runs_after_in_the_same_transaction_and_a_failure_rolls_the_append_back(repo, st, capsys):
    tid = _new(capsys)
    before = run.read(st, tid).events[-1].seq
    seen: list[int] = []

    def after_sees(conn):
        seen.append(store.read_events(conn, tid)[-1].seq)

    seqs = run.append(st, tid, _started, after=after_sees)
    assert seen == seqs and run.read(st, tid).events[-1].seq == seqs[0]

    def after_fails(_conn):
        raise model.Refused("後で断る")

    with pytest.raises(model.Refused, match="後で断る"):
        run.append(st, tid, _started, after=after_fails)
    assert run.read(st, tid).events[-1].seq == seqs[0] != before


def test_append_lets_refusals_through_without_retrying(repo, st, capsys):
    tid = _new(capsys)
    calls = []

    def refuse(v):
        calls.append(v)
        raise model.Refused("断る")

    with pytest.raises(model.Refused, match="断る"):
        run.append(st, tid, refuse)
    assert len(calls) == 1
    with pytest.raises(model.Refused, match="無い"):
        run.append(st, "no-such-table", lambda v: [])


# --- 現状固定: run.wait（I-026。作業が動く間だけ設定の間隔で待ち、上限で同じ wait を返す） -------------


def test_wait_on_a_missing_table_is_refused(repo, st):
    with pytest.raises(model.Refused, match="無い"):
        run.wait(st, "no-such-table", 0, sleep=lambda s: None)


def test_next_while_the_work_runs_returns_wait_and_show_gives_the_hand(repo, st, env, capsys):
    tid = _new(capsys)
    run.advance(st, tid, repo, env.env)
    with store.run_lock(st, tid):
        assert run.advance(st, tid, repo, env.env)["next"]["argv"][2] == "wait"
        assert run.hand(st, run.read(st, tid))["argv"][2] == "wait"
        timed = run.wait(st, tid, 0)
        assert timed["cause"]["kind"] == "wait_timeout" and timed["next"]["argv"][2] == "wait"
    # 作業が落ちた（排他が空いた）後の 1 手は next で、打てば終わったステップの次から流す
    assert run.hand(st, run.read(st, tid))["argv"][2] == "next"
    assert run.wait(st, tid, 0)["next"]["argv"][2] == "next"


def test_wait_without_live_work_returns_the_report_at_once_without_sleeping(repo, st, capsys):
    tid = _new(capsys)
    slept: list[float] = []
    got = run.wait(st, tid, 60, sleep=slept.append)
    v = run.read(st, tid)
    assert slept == [] and got == run.report(v, run.decide(v))
    assert got["next"]["argv"] == ["ndf", "progress", "next", tid]


def test_wait_sleeps_the_declared_interval_until_the_work_lets_go(repo, st, capsys):
    from contextlib import ExitStack

    from ndf.contracts import limits

    tid = _new(capsys)
    held = ExitStack()
    held.enter_context(store.run_lock(st, tid))
    slept: list[float] = []

    def sleep(seconds):
        slept.append(seconds)
        if len(slept) == 2:
            held.close()  # 2 回待った後に作業が排他を放す

    try:
        got = run.wait(st, tid, 3600, sleep=sleep)
    finally:
        held.close()
    v = run.read(st, tid)
    assert slept == [limits.value(run.WAIT_POLL, "interval")] * 2
    assert got == run.report(v, run.decide(v))


def test_wait_while_the_work_runs_past_the_limit_returns_the_same_wait(repo, st, capsys):
    tid = _new(capsys)
    slept: list[float] = []
    now = [0.0]  # 偽物の時計。1 回の sleep で 0.02 秒進む（前の実時間の sleep と同じ刻み）

    def sleep(seconds):
        slept.append(seconds)
        now[0] += 0.02

    with store.run_lock(st, tid):
        at_once = run.wait(st, tid, 0, sleep=slept.append)
        later = run.wait(st, tid, 0.05, sleep=sleep, clock=lambda: now[0])
    assert at_once == result.stopped(
        run.TOOL,
        "wait_timeout",
        f"ステートマシン {tid} を流す作業がまだ動いている（0 秒待った）",
        next=result.command(run.TOOL, "wait", tid),
    )
    assert later["cause"] == {"kind": "wait_timeout", "detail": f"ステートマシン {tid} を流す作業がまだ動いている（0.05 秒待った）"}
    assert later["next"] == result.command(run.TOOL, "wait", tid) and len(slept) >= 1


# --- 差し込みを started の前に組んで確かめる（#3908 の受け入れ条件 3） ------------------------------------------------------


def _kinds(v: run.View, stage: str) -> list[str]:
    """行のイベントのうち、開始と失敗の種類。"""
    return [e.draft.kind for e in v.row_events(stage) if e.draft.kind in ("started", "failed")]


def _step_dir(st, tid: str, stage_id: str, step: str, generation: int = 0) -> Path:
    return run.work_root(st.root, tid) / stage_id / f"{step}-g{generation}"


def test_a_stage_whose_values_cannot_be_decided_stops_before_started(repo, st, env, capsys, monkeypatch):
    # worker の既定を外すと、工程「要求と受け入れ条件」の差し込み worker がどの出所からも決まらない
    sprint_fixture.without_worker(monkeypatch)
    tid = _new(capsys)
    real = run.Env(**{**vars(env.env), "prepare": invoke.prepare_plan})
    out = run.advance(st, tid, repo, real)
    assert out["status"] == "stopped" and out["cause"]["kind"] == "precondition"
    assert out["items"] == [{"key": "worker", "source": invoke.FROM_DEFAULT}]
    assert "要求と受け入れ条件" in out["cause"]["detail"] and "worker" in out["cause"]["detail"]
    v = run.read(st, tid)
    assert _kinds(v, "要求と受け入れ条件") == [] and _labels(st, tid)["要求と受け入れ条件"] == "pending"
    assert env.launched == [] and env.fake.calls == []
    assert not (_step_dir(st, tid, "requirements", "requirements") / invoke.VALUES_FILE).exists()


def test_a_stage_whose_values_are_decided_starts_with_the_values_in_its_step_dir(repo, st, env, capsys, monkeypatch):
    sprint_fixture.with_worker(monkeypatch)
    tid = _new(capsys)
    real = run.Env(**{**vars(env.env), "prepare": invoke.prepare_plan})
    assert run.advance(st, tid, repo, real)["status"] == "ok"
    assert _kinds(run.read(st, tid), "要求と受け入れ条件") == ["started"]
    held = invoke.read_values(_step_dir(st, tid, "requirements", "requirements"))
    assert held is not None and held["plan"] == "requirements" and held["inputs"] == {"issues": [ISSUE], "mode": "standard"}


def test_a_passing_github_failure_while_preparing_stops_as_transient_before_started(repo, st, env, capsys, monkeypatch):
    def fail(*a, **k):
        raise GitHubError("つながらない", 0, retry_after=30.0)

    monkeypatch.setattr(invoke, "template_values", fail)
    tid = _new(capsys)
    out = run.advance(st, tid, repo, run.Env(**{**vars(env.env), "prepare": invoke.prepare_plan}))
    assert out["cause"]["kind"] == "transient" and out["cause"]["retry_after"] == 30.0
    assert _kinds(run.read(st, tid), "要求と受け入れ条件") == []


def test_values_that_cannot_be_cleared_stop_as_precondition_before_started(repo, st, env, capsys, monkeypatch):
    def fail(state_dirs):
        raise PermissionError("読み取り専用")

    monkeypatch.setattr(invoke, "clear_values", fail)
    tid = _new(capsys)
    out = run.advance(st, tid, repo, env.env)
    assert out["status"] == "stopped" and out["cause"]["kind"] == "precondition"
    assert "要求と受け入れ条件" in out["cause"]["detail"] and "読み取り専用" in out["cause"]["detail"]
    assert _kinds(run.read(st, tid), "要求と受け入れ条件") == []


def test_the_values_are_built_once_from_next_to_running_the_step(repo, st, env, capsys, monkeypatch):
    # _start で組んだ値を、そのステップを流す plan_path が読むだけにする（設定・打った場所・差し込みを読むのは 1 回）
    sprint_fixture.with_worker(monkeypatch)
    calls = {"template_values": [], "_project": []}
    for name, seen in calls.items():
        held = getattr(invoke, name)
        monkeypatch.setattr(invoke, name, lambda *a, _held=held, _seen=seen, **k: _seen.append(a) or _held(*a, **k))
    derived, held_derived = [], route.derived_locally
    monkeypatch.setattr(route, "derived_locally", lambda *a: derived.append(a) or held_derived(*a))
    counted: list[tuple[int, int, int]] = []

    def runner(argv, cwd, environ):
        counted.append((len(calls["template_values"]), len(calls["_project"]), len(derived)))
        out = result.stopped("plan", "test_failed", "ここで止める")
        return subprocess.CompletedProcess(argv, 0, json.dumps(out) + "\n", "")

    invoker = lambda r, cwd, work_dir, answer: invoke.invoke(r, cwd, work_dir, answer, runner=runner)  # noqa: E731
    tid = _new(capsys)
    lanes.turn(st, tid, repo, run.Env(**{**vars(env.env), "prepare": invoke.prepare_plan, "invoker": invoker}))
    assert counted == [(1, 1, 1)]


def test_a_retry_clears_the_values_of_the_last_try_and_prepares_the_step_it_runs_next(repo, st, env, capsys):
    # 打ち直しは同じ世代の同じ保存先を使う。前の試行の values.json を残すと、後続のプランのステップが古い設定で描かれる
    tid = _new(capsys)
    lanes.turn(st, tid, repo, env.env)
    _approve(st, tid, "設計")
    env.fake.script["design-merge"] = [lambda r: invoke.StageResult(result.stopped("plan", "test_failed", "落ちた"), {})]
    lanes.turn(st, tid, repo, env.env)
    assert _labels(st, tid)["設計"] == "failed"
    dirs = [_step_dir(st, tid, "design", step) for step in ("design", "design-review", "design-merge")]
    for d in dirs:
        invoke.write_values(d, "古い", {}, {}, True)
    # 課題のコピーも消す。残すと新しい試行が GitHub で直した題・本文を使わない
    copies = [dirs[0] / invoke.ISSUE_BODY_FILE.format(number=ISSUE), dirs[0] / invoke.ISSUE_TITLE_FILE.format(number=ISSUE)]
    for c in copies:
        c.write_text("{}", encoding="utf-8")
    prepared = []
    recording = run.Env(**{**vars(env.env), "prepare": lambda r, work_dir, cwd: prepared.append((r.name, work_dir))})
    _main(capsys, command.main, "retry", tid, "設計", "--reason", "設定を直した")
    run.advance(st, tid, repo, recording)
    assert [d for d in dirs if (d / invoke.VALUES_FILE).exists()] == [] and [c for c in copies if c.exists()] == []
    assert prepared == [("design-merge", dirs[2])]


# --- #3959 Task 4: リリース承認ゲートを MVV 判定で省かない・保留を確かめていない回答を数えない（決定 3） ---

OLD_RELEASE = {"gate": "release", "skip": True, "verdict": None, "why": [], "head": JUDGED}


def _held(kind: str, **extra) -> dict:
    """判定のステップの出力 mvv_gate。`extra` に holds を渡さなければ、更新前の NDF が書いた形（holds のキーが無い）。"""
    return {
        "gate": kind,
        "skip": True,
        "verdict": {"verdict": "follow", "reasons": ["Value 1"], "boundary": []},
        "why": [],
        "head": JUDGED,
        **extra,
    }


def _held_step(held: dict):
    return lambda r: invoke.finished(r, result.result("script", "ok", "MVV 判定", outputs={"mvv_gate": held}))


def _answers(st, tid) -> list[model.Event]:
    return [e for e in run.read(st, tid).events if e.draft.kind == "answered"]


def _old_mvv_answer(st, tid, held: dict) -> model.Event:
    """更新前の NDF が書いた、承認ゲートを省いた `service:mvv` の回答（`model.mvv_answer` の形）を、今の待ちへ書く。"""
    v = run.read(st, tid)
    m = run.decide(v)
    assert m.stage is not None and m.step is not None and m.progress is not None
    qid = model.question_id(v.machine_id, v.rev, m.stage.name, m.step.id, m.progress.anchor)
    payload, reason = model.mvv_answer(m.step.id, invoke.LISTEN, run.APPROVED, held, qid)
    run._write(st, tid, model.EventDraft(run.ANSWERED, run.CONDUCTOR, m.stage.name, reason=reason, payload=payload, rev=v.rev))
    return _answers(st, tid)[-1]


def _consumed_by_old_ndf(st, tid, answered: model.Event) -> None:
    """更新前の NDF の `_consume` が、`service:mvv` の回答を読んで承認ゲートのステップを済みにした記録。"""
    v = run.read(st, tid)
    m = run.decide(v)
    assert m.stage is not None and m.step is not None and m.progress is not None
    payload = {"step": m.step.id, "status": run.FINISHED, "report": run.gate_report(m.step, answered), "generation": 0}
    run._write(st, tid, model.EventDraft(run.STEP_EVENT, run.ACTOR, m.stage.name, payload=payload, rev=v.rev))


def _to_release_gate(st, tid, repo, env, release_held: dict) -> dict:
    env.fake.script["release-mvv"] = [_held_step(release_held)]
    lanes.turn(st, tid, repo, env.env)
    _approve(st, tid, "設計")
    return lanes.turn(st, tid, repo, env.env)


def _human(st, tid, head: str = "h1") -> None:
    q = gate.waiting(run.read(st, tid))
    assert q is not None
    gate.answer(st, tid, gate.Answer(q.id, "approved", "人が承認した", "human:reviewer", f"k-{q.id}", seen=gate.Target(head=head)))


def test_a_skip_on_the_release_gate_waits_for_a_human_without_a_service_answer(repo, st, env, capsys):
    tid = _new(capsys, "standard", "--pace", "fast")
    out = _to_release_gate(st, tid, repo, env, _held("release", holds=[]))
    assert out["status"] == "gate" and out["metrics"]["step"] == "release-gate"
    assert [e.draft.payload["by"] for e in _answers(st, tid)] == ["human:test"]


def test_an_old_service_answer_on_the_release_gate_asks_a_human_and_the_human_answer_carries_on(repo, st, env, capsys):
    # 決定 3: 更新前の NDF が書いた判定の skip: true と、まだ読まれていない service:mvv の承認から再開する
    tid = _new(capsys, "standard", "--pace", "fast")
    _to_release_gate(st, tid, repo, env, OLD_RELEASE)
    old = _old_mvv_answer(st, tid, OLD_RELEASE)
    assert run.decide(run.read(st, tid)).action == run.Action.GATE
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate" and out["metrics"]["step"] == "release-gate"
    q = gate.waiting(run.read(st, tid))
    assert q is not None and q.id == old.draft.payload["gate_id"]
    assert gate.approved_head(run.read(st, tid), "release") is None
    _human(st, tid)
    assert run.decide(run.read(st, tid)).action == run.Action.CONSUME
    assert gate.approved_head(run.read(st, tid), "release") == "h1"
    done = lanes.turn(st, tid, repo, env.env)
    assert done["status"] == "ok" and done["metrics"]["state"] == "completed"


def test_a_release_gate_closed_by_an_old_service_answer_is_opened_again(repo, st, env, capsys):
    # 決定 3: 更新前の NDF が service:mvv の回答を消費して済みにした承認ゲートは、後ろのステップが済んでいなければ開き直す
    tid = _new(capsys, "standard", "--pace", "fast")
    _to_release_gate(st, tid, repo, env, OLD_RELEASE)
    old = _old_mvv_answer(st, tid, OLD_RELEASE)
    _consumed_by_old_ndf(st, tid, old)
    m = run.decide(run.read(st, tid))
    assert (m.action, m.step.id if m.step else None) == (run.Action.GATE, "release-gate")
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate" and out["metrics"]["step"] == "release-gate"
    q = gate.waiting(run.read(st, tid))
    assert q is not None and q.id != old.draft.payload["gate_id"]
    _human(st, tid)
    assert run.decide(run.read(st, tid)).action == run.Action.CONSUME
    done = lanes.turn(st, tid, repo, env.env)
    assert done["status"] == "ok" and done["metrics"]["state"] == "completed"
    assert gate.approved_head(run.read(st, tid), "release") == "h1"
    recorded = [e for e in run.read(st, tid).events if e.draft.kind == "step_recorded" and e.draft.payload["step"] == "release-gate"]
    assert len(recorded) == 2


def test_a_release_gate_closed_by_an_old_service_answer_is_not_opened_after_the_next_step(repo, st, env, capsys):
    tid = _new(capsys, "standard", "--pace", "fast")
    _to_release_gate(st, tid, repo, env, OLD_RELEASE)
    old = _old_mvv_answer(st, tid, OLD_RELEASE)
    _consumed_by_old_ndf(st, tid, old)
    v = run.read(st, tid)
    after = {"step": "release", "status": run.FINISHED, "report": {"status": "ok"}, "generation": 0}
    run._write(st, tid, model.EventDraft(run.STEP_EVENT, run.ACTOR, "デプロイ", payload=after, rev=v.rev))
    assert run.decide(run.read(st, tid)).action == run.Action.RESUME


def test_a_failed_merge_after_an_old_service_answer_asks_a_human_after_the_retry(repo, st, env, capsys):
    tid = _new(capsys, "standard", "--pace", "fast")
    _to_release_gate(st, tid, repo, env, OLD_RELEASE)
    old = _old_mvv_answer(st, tid, OLD_RELEASE)
    _consumed_by_old_ndf(st, tid, old)
    v = run.read(st, tid)
    run._write(st, tid, run._failed(v.snap.stage("デプロイ"), "release", v.rev, "refused", "承認した head が無い"))
    out = run.advance(st, tid, repo, env.env)
    assert out["status"] == "stopped"
    assert command.main(["retry", tid, "デプロイ", "--reason", "打ち直す"]) == 0
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate" and out["metrics"]["step"] == "release-gate"


def test_the_design_gate_counts_only_a_service_answer_that_checked_the_holds(repo, st, env, capsys):
    # 保留を確かめた証拠（holds: []）を持つ判定だけが設計承認ゲートを省く
    tid = _new(capsys, "standard", "--pace", "fast")
    env.fake.script["design-mvv"] = [_held_step(_held("design", holds=[]))]
    lanes.turn(st, tid, repo, env.env)
    assert [e.draft.payload["by"] for e in _answers(st, tid)] == ["service:mvv"]
    assert run.decide(run.read(st, tid)).action == run.Action.CONSUME
    assert gate.approved_head(run.read(st, tid), "design") == JUDGED
    out = lanes.turn(st, tid, repo, env.env)
    assert out["metrics"]["stage"] == "デプロイ"


@pytest.mark.parametrize("extra", [{}, {"holds": None}, {"holds": ["直っていないレッドライン"]}])
def test_the_design_gate_does_not_count_a_verdict_without_checked_holds(repo, st, env, capsys, extra):
    tid = _new(capsys, "standard", "--pace", "fast")
    held = _held("design", **extra)
    env.fake.script["design-mvv"] = [_held_step(held)]
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate" and out["metrics"]["step"] == "design-gate" and _answers(st, tid) == []
    # 更新前の NDF が書いた、まだ読まれていない service:mvv の回答も数えない
    _old_mvv_answer(st, tid, held)
    assert run.decide(run.read(st, tid)).action == run.Action.GATE
    assert gate.approved_head(run.read(st, tid), "design") is None


def test_consume_does_not_take_an_answer_that_is_not_counted(repo, st, env, capsys):
    tid = _new(capsys, "standard", "--pace", "fast")
    _to_release_gate(st, tid, repo, env, OLD_RELEASE)
    old = _old_mvv_answer(st, tid, OLD_RELEASE)
    v = run.read(st, tid)
    m = run.decide(v)
    d = run.Drive(st, tid, repo, env.env, inline=True)
    before = len(v.events)
    out = run._consume(d, v, dataclasses.replace(m, action=run.Action.CONSUME, event=old))
    assert out is not None and out["status"] == "gate" and len(run.read(st, tid).events) == before
    # 回答が無く、直前の判定が省かないと決めた手でも service:mvv の回答を書かない
    out = run._consume(d, v, dataclasses.replace(m, action=run.Action.CONSUME, event=None))
    assert out is not None and out["status"] == "gate" and len(run.read(st, tid).events) == before


@pytest.mark.parametrize(
    ("payload", "kind", "void"),
    [
        ({"by": "service:mvv", "mvv_gate": {"holds": []}}, "release", True),
        ({"by": "service:mvv", "mvv_gate": {"holds": []}}, None, True),
        ({"by": "service:mvv", "mvv_gate": {"holds": []}}, "design", False),
        ({"by": "service:mvv", "mvv_gate": {}}, "design", True),
        ({"by": "service:mvv", "mvv_gate": {"holds": None}}, "design", True),
        ({"by": "service:mvv", "mvv_gate": None}, "design", True),
        ({"by": "service:mvv"}, "design", True),
        ({"by": "human:a", "mvv_gate": {}}, "release", False),
        ({"by": "conductor"}, "release", False),
        ({}, "release", False),
        ({"by": 3}, "release", False),
    ],
)
def test_mvv_answer_void(payload, kind, void):
    assert model.mvv_answer_void(payload, kind) is void


def test_mvv_skip_takes_only_a_design_verdict_that_checked_the_holds():
    done = {"m": {"outputs": {"mvv_gate": _held("design", holds=[])}}}
    assert model.mvv_skip(done, "m", "design") is not None
    for held in (_held("design"), _held("design", holds=None), _held("design", holds=["x"])):
        assert model.mvv_skip({"m": {"outputs": {"mvv_gate": held}}}, "m", "design") is None
    assert model.mvv_skip({"m": {"outputs": {"mvv_gate": _held("release", holds=[])}}}, "m", "release") is None
    assert model.hold_checked({"holds": []}) and not model.hold_checked({"holds": ()}) and not model.hold_checked(None)
