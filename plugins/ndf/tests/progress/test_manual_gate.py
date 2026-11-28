"""手で終えた承認ゲートの工程が回答を待つ（#4520 の受け入れ条件 1〜4）と、自動の流れが先の承認を使うこと。

ステートマシンの DB は本物で、作業（プラン）と GitHub は偽物にする。
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import merge_hub
import pytest
from result_json import last_result

from ndf.core import merge_approval
from ndf.progress import approval_decide, command, gate, invoke, model, run, store
from ndf.progress.gate import answering, finishing, release, targets

ISSUE = 4520
DESIGN = "設計"
DETAIL = "詳細設計"
DESIGN_PR = 7


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lanes = _load("lanes")


@pytest.fixture
def env(repo, st, monkeypatch):
    """作業と GitHub の偽物の `Env`。`finish` が読む GitHub も同じ偽物にする。"""
    values = lanes.values(repo, "sprint/s1")
    calls: list[str] = []
    script: dict[str, list] = {}
    hub = merge_hub.MergeHub()
    hub.pulls_of[8] = merge_hub.pull(8)  # 偽の作業が出すスプリントの PR（リリース承認ゲートの対象）

    def invoker(r, cwd, work_dir, answer):
        calls.append(r.step)
        queued = script.get(r.step)
        return queued.pop(0)(r) if queued else lanes.ok_result(r, values)

    reader = targets.TargetReader(lambda: hub)
    e = run.Env(
        facts=lambda words, ctx, cwd: {w: True for w in words},
        launch=lambda st_, tid, cwd: None,
        invoker=invoker,
        prepare=lambda r, work_dir, cwd: None,
        records=lambda cwd: lanes._NoRecords(),
        sleep=lambda s: None,
        standing=answering.standing(reader, st.root),
        forget=reader.forget,
    )
    monkeypatch.setattr(finishing, "shared_hub", lambda cwd: lambda: hub)
    return SimpleNamespace(env=e, calls=calls, script=script, hub=hub, reader=reader)


def _new(capsys, mode: str = "standard") -> str:
    code = command.main(["new", "s1", "--mode", mode, "--issues", str(ISSUE)])
    out = last_result(capsys.readouterr().out)
    assert code == 0, out
    return out["metrics"]["machine_id"]


def _finish(capsys, repo, tid: str, stage: str, outputs: dict, *, check: bool = False) -> dict:
    path = repo.parent / "outputs.json"
    path.write_text(json.dumps({"steps": [], "summary": f"{stage} を手で行った", "outputs": outputs}))
    extra = [] if check else ["--reason", "手で"]
    finishing.main(["check" if check else "finish", tid, stage, "--output", str(path), *extra])
    return last_result(capsys.readouterr().out)


def _before_design(capsys, repo, tid: str) -> None:
    """設計より前の工程を手で終える。"""
    assert _finish(capsys, repo, tid, "要求と受け入れ条件", {})["status"] == "ok"
    assert _finish(capsys, repo, tid, "作業場所の用意", {"worktree": str(repo), "branch": "sprint/s1"})["status"] == "ok"


def _state(st, tid: str, stage: str):
    v = run.read(st, tid)
    return v.rows[stage].state if stage in v.rows else None


def _kinds(st, tid: str, stage: str) -> list[str]:
    return [ev.draft.kind for ev in run.read(st, tid).row_events(stage)]


def _next(st, tid, repo, env) -> dict:
    """`ndf progress next` と同じく、待ちへ戻すかを確かめてから 1 手進め、承認ゲートの待ちは質問と route にする。"""
    env.reader.forget()
    observe = answering.github_observe(repo, st.root, reader=env.reader)
    why = gate.recheck(st, tid, observe)
    out = lanes.turn(st, tid, repo, env.env)
    out = gate.present(st, tid, repo, out, observe, env={})
    return command.with_warnings(out, before=[why] if why else [])


_FLAGS = {
    "--head": "head",
    "--material-sha256": "material",
    "--body-sha256": "body",
    "--title-sha256": "title",
    "--presentation": "presentation",
}


def _answer(
    st,
    tid,
    repo,
    env,
    shown: dict,
    value: str = "approved",
    key: str = "k-1",
    notes: Path | None = None,
    post=lambda row: True,
    by: str = "human:reviewer",
) -> dict:
    """提示した route が固定する対象を見て回答する（`ndf answer` と同じ引数）。"""
    argv = shown["routes"][value]["argv"]
    seen = gate.Target(**{_FLAGS[f]: v for f, v in zip(argv, argv[1:]) if f in _FLAGS})
    qid = shown["metrics"]["gate"]["id"]
    env.reader.forget()
    a = gate.Answer(qid, value, "回答", by, key, notes, seen=seen)
    return gate.answer(st, tid, a, answering.github_observe(repo, st.root, reader=env.reader), post)


def _merge(env, head: str = merge_hub.HEAD) -> None:
    env.hub.pulls_of[DESIGN_PR] = merge_hub.pull(DESIGN_PR, head=head, merged=True)


def _held_design(st, capsys, repo, env) -> str:
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    out = _finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR})
    assert out["status"] == "ok", out
    return tid


def test_a_hand_finished_gate_stage_waits_and_next_does_not_start_the_later_stage(repo, st, env, capsys):
    """受け入れ条件 1。"""
    tid = _held_design(st, capsys, repo, env)
    assert _state(st, tid, DESIGN) == "running" and "finish_held" in _kinds(st, tid, DESIGN)
    assert run.read(st, tid).context()["design_pr"] == DESIGN_PR
    assert env.hub.calls == []  # 承認の記録が無ければ PR を読まない

    out = _next(st, tid, repo, env)

    assert out["status"] == "gate" and out["metrics"]["stage"] == DESIGN, out
    assert out["metrics"]["gate"]["pr"] == DESIGN_PR
    assert "approved" in out["routes"] and "request_changes" in out["routes"]
    assert out["routes"]["approved"]["argv"][1] == "answer"
    assert _state(st, tid, DETAIL) == "pending" and env.calls == []


def test_an_approval_lets_next_go_on_after_the_design_pr_is_merged(repo, st, env, capsys):
    """受け入れ条件 2 と、未マージの間は終了を書かないこと。"""
    tid = _held_design(st, capsys, repo, env)
    shown = _next(st, tid, repo, env)
    assert _answer(st, tid, repo, env, shown)["status"] == "ok"

    stopped = _next(st, tid, repo, env)

    assert stopped["status"] == "stopped" and stopped["cause"]["kind"] == "precondition", stopped
    assert "マージ" in stopped["cause"]["detail"] and _state(st, tid, DESIGN) == "running"
    assert _state(st, tid, DETAIL) == "pending" and env.calls == []

    _merge(env)
    out = _next(st, tid, repo, env)

    assert _state(st, tid, DESIGN) == "finished", out
    assert _state(st, tid, DETAIL) == "finished" and env.calls


def test_a_head_merged_after_the_approval_is_asked_again(repo, st, env, capsys):
    tid = _held_design(st, capsys, repo, env)
    shown = _next(st, tid, repo, env)
    _answer(st, tid, repo, env, shown)
    _merge(env, merge_hub.OTHER)

    out = _next(st, tid, repo, env)

    assert out["status"] == "gate" and out["metrics"]["gate"]["id"] != shown["metrics"]["gate"]["id"], out
    assert _state(st, tid, DESIGN) == "running" and _state(st, tid, DETAIL) == "pending"


@pytest.mark.parametrize("changed", ["base", "production"])
def test_a_moved_merge_target_needs_a_new_approval(repo, st, env, capsys, changed):
    tid = _held_design(st, capsys, repo, env)
    shown = _next(st, tid, repo, env)
    _answer(st, tid, repo, env, shown)
    if changed == "base":
        env.hub.files["develop"] = env.hub.files["main"]
        env.hub.pulls_of[DESIGN_PR] = merge_hub.pull(DESIGN_PR, base="develop")
    else:
        env.hub.files["main"] = (merge_hub.project_text(merge_hub.strategy(base="main", production="prod")), "c2")

    out = _next(st, tid, repo, env)

    assert out["status"] == "gate" and out["metrics"]["gate"]["id"] != shown["metrics"]["gate"]["id"], out
    assert _state(st, tid, DESIGN) == "running"


def _approved_in_the_flow(st, capsys, repo, env, step: str = "design-merge") -> str:
    """自動の流れで設計を承認し、後ろのステップ `step` で失敗させる。"""
    env.script[step] = [lambda r: invoke.StageResult(run.result.stopped("plan", "test_failed", "落ちた"), {})]
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    shown = _next(st, tid, repo, env)
    assert shown["status"] == "gate", shown
    _answer(st, tid, repo, env, shown)
    _next(st, tid, repo, env)
    assert _state(st, tid, DESIGN) == "failed"
    return tid


def test_a_recorded_approval_of_the_same_target_is_not_asked_again(repo, st, env, capsys):
    """受け入れ条件 3。"""
    tid = _approved_in_the_flow(st, capsys, repo, env)
    before = len(_kinds(st, tid, DESIGN))
    refused = _finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR})
    assert refused["cause"]["kind"] == "refused" and "マージ" in refused["cause"]["detail"], refused
    assert len(_kinds(st, tid, DESIGN)) == before

    _merge(env)
    out = _finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR})

    assert out["status"] == "ok" and _state(st, tid, DESIGN) == "finished", out
    assert "finish_held" not in _kinds(st, tid, DESIGN)


def test_an_approval_of_another_head_is_not_used_by_finish(repo, st, env, capsys):
    tid = _approved_in_the_flow(st, capsys, repo, env)
    _merge(env, merge_hub.OTHER)

    out = _finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR})

    assert out["status"] == "ok" and _state(st, tid, DESIGN) == "running", out


def test_the_flow_uses_an_earlier_approval_without_asking_again(repo, st, env, capsys):
    """出力の失敗で初めから流し直した承認ゲートは、同じ対象の先の承認を使い、読み手役のステップも打たない。"""
    tid = _approved_in_the_flow(st, capsys, repo, env)
    finishing.main(["retry", tid, DESIGN, "--reason", "やり直す"])
    capsys.readouterr()
    # 初めから流し直す（出力の失敗の後と同じ形にする）
    run._write(st, tid, run.model.EventDraft("started", "workflow", DESIGN, payload={"inputs": {}}, rev=1))
    run._write(st, tid, run.model.EventDraft("failed", "workflow", DESIGN, payload={"step": None}, rev=1))
    finishing.main(["retry", tid, DESIGN, "--reason", "やり直す"])
    capsys.readouterr()
    env.calls.clear()

    _next(st, tid, repo, env)

    assert _state(st, tid, DESIGN) == "finished"
    assert "design" in env.calls and "design-merge" in env.calls and "design-reading" not in env.calls


def test_a_stage_without_a_gate_finishes_and_goes_on_as_before(repo, st, env, capsys):
    """受け入れ条件 4。light モードの設計の工程は承認ゲートを持たない。"""
    tid = _new(capsys, "light")
    _before_design(capsys, repo, tid)
    mvv = lanes.values(repo, "sprint/s1")["mvv_gate"]

    out = _finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR, "mvv_gate": mvv})

    assert out["status"] == "ok" and "manual_finished を書いた" in out["summary"], out
    assert _state(st, tid, DESIGN) == "finished" and env.hub.calls == []
    assert run.read(st, tid).context()["mvv_gate"] == mvv
    _next(st, tid, repo, env)
    assert _state(st, tid, "実装") == "finished"


def test_a_hand_written_design_output_needs_no_mvv_gate_and_it_is_not_used(repo, st, env, capsys):
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    assert _finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR}, check=True)["status"] == "ok"
    mvv = lanes.values(repo, "sprint/s1")["mvv_gate"]

    out = _finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR, "mvv_gate": mvv})

    assert out["status"] == "ok" and any("mvv_gate" in w for w in out["warnings"]), out
    assert "mvv_gate" not in run.read(st, tid).context()


def test_a_rejection_of_a_held_stage_fails_it(repo, st, env, capsys):
    tid = _held_design(st, capsys, repo, env)
    shown = _next(st, tid, repo, env)
    _answer(st, tid, repo, env, shown, "rejected")

    out = _next(st, tid, repo, env)

    assert _state(st, tid, DESIGN) == "failed" and _state(st, tid, DETAIL) == "pending", out


def test_a_change_request_of_a_held_stage_runs_the_stage_again(repo, st, env, capsys):
    tid = _held_design(st, capsys, repo, env)
    shown = _next(st, tid, repo, env)
    notes = repo.parent / "notes.md"
    notes.write_text("直して\n")
    _answer(st, tid, repo, env, shown, "request_changes", notes=notes)

    out = _next(st, tid, repo, env)

    assert out["status"] == "gate" and out["metrics"]["stage"] == DESIGN, out
    assert env.calls[0] == "design" and _state(st, tid, DETAIL) == "pending"


def test_a_held_stage_is_not_finished_again_and_can_be_reopened(repo, st, env, capsys):
    tid = _held_design(st, capsys, repo, env)
    again = _finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR})
    assert again["cause"]["kind"] == "refused" and "ndf progress next" in again["cause"]["detail"], again

    finishing.main(["retry", tid, DESIGN, "--reason", "直す"])
    refused = last_result(capsys.readouterr().out)
    assert refused["cause"]["kind"] == "refused" and "--later" in refused["cause"]["detail"], refused

    finishing.main(["retry", tid, DESIGN, "--reason", "直す", "--later", "reopen"])
    out = last_result(capsys.readouterr().out)

    assert out["status"] == "ok" and _state(st, tid, DESIGN) == "pending", out
    assert run.step_progress(run.read(st, tid), DESIGN).held_finish is None


def test_a_disabled_gate_stage_is_not_finished(repo, st, env, capsys):
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    run._write(st, tid, run.model.EventDraft("disabled", "conductor", DESIGN, reason="古い記録", rev=1))

    out = _finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR})

    assert out["cause"]["kind"] == "refused" and "ndf progress enable" in out["cause"]["detail"], out
    assert _state(st, tid, DESIGN) == "disabled"


def test_finish_is_refused_while_work_is_running(repo, st, env, capsys):
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    with store.run_lock(st, tid):
        out = _finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR})
    assert out["cause"]["kind"] == "refused" and _state(st, tid, DESIGN) == "pending", out


def test_a_machine_finished_by_hand_before_the_change_stays_finished(repo, st, env, capsys):
    """移行性: 変更の前の `manual_finished` は終了のまま読む。"""
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    run._write(st, tid, run.model.EventDraft("manual_finished", "conductor", DESIGN, reason="変更の前", rev=1))

    _next(st, tid, repo, env)

    assert _state(st, tid, DESIGN) == "finished" and _state(st, tid, DETAIL) == "finished"


DEPLOY = "デプロイ"


def _released_values(repo) -> dict:
    values = lanes.values(repo, "sprint/s1")
    return {k: values[k] for k in ("delivery", "product_version", "mvv_gate")}


def test_a_hand_finished_deploy_waits_for_the_release_approval(repo, st, env, capsys):
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    assert _finish(capsys, repo, tid, "Pull Request", {"pr": 8})["status"] == "ok"

    out = _finish(capsys, repo, tid, DEPLOY, _released_values(repo))

    assert out["status"] == "ok" and _state(st, tid, DEPLOY) == "running", out
    assert "finish_held" in _kinds(st, tid, DEPLOY)


def test_a_deploy_approved_in_the_flow_is_not_asked_again_by_finish(repo, st, env, capsys):
    """受け入れ条件 3（デプロイ）。"""
    env.script["release"] = [lambda r: invoke.StageResult(run.result.stopped("plan", "test_failed", "落ちた"), {})]
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    shown = _next(st, tid, repo, env)
    _answer(st, tid, repo, env, shown)
    shown = _next(st, tid, repo, env)
    assert shown["status"] == "gate" and shown["metrics"]["stage"] == DEPLOY, shown
    _answer(st, tid, repo, env, shown, key="k-release")
    _next(st, tid, repo, env)
    assert _state(st, tid, DEPLOY) == "failed"

    out = _finish(capsys, repo, tid, DEPLOY, _released_values(repo))

    assert out["status"] == "ok" and _state(st, tid, DEPLOY) == "finished", out


def test_a_human_approval_in_the_flow_is_asked_again_when_production_changes(repo, st, env, capsys):
    """設計承認で残った懸念 1: 自動の流れで人が承認した後に本番への反映が変われば、承認を取り直す。"""
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    shown = _next(st, tid, repo, env)
    _answer(st, tid, repo, env, shown)
    env.hub.files["main"] = (merge_hub.project_text(merge_hub.strategy(base="main", production="prod")), "c2")
    env.calls.clear()

    out = _next(st, tid, repo, env)

    assert out["status"] == "gate" and out["metrics"]["gate"]["id"] != shown["metrics"]["gate"]["id"], out
    assert "design-merge" not in env.calls


def test_a_rejected_hand_finish_after_a_failed_merge_asks_again_and_drops_the_old_approval(repo, st, env, capsys):
    tid = _approved_in_the_flow(st, capsys, repo, env)
    _merge(env, merge_hub.OTHER)
    assert _finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR})["status"] == "ok"
    assert gate.approved_head(run.read(st, tid), "design") is None
    assert run.approved_seen(run.StageAnswers(run.read(st, tid), DESIGN), st.root) is None
    shown = _next(st, tid, repo, env)
    _answer(st, tid, repo, env, shown, "rejected", key="k-reject")
    _next(st, tid, repo, env)
    finishing.main(["retry", tid, DESIGN, "--reason", "やり直す"])
    capsys.readouterr()
    env.calls.clear()

    out = _next(st, tid, repo, env)

    assert out["status"] == "gate" and out["metrics"]["step"] == "design-gate", out
    assert "design-merge" not in env.calls


def test_a_hand_finished_deploy_does_not_issue_a_release_request(repo, st, env, capsys, monkeypatch):
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    assert _finish(capsys, repo, tid, "Pull Request", {"pr": 8})["status"] == "ok"
    assert _finish(capsys, repo, tid, DEPLOY, _released_values(repo))["status"] == "ok"
    monkeypatch.setattr(release, "_plan_request", lambda *a: pytest.fail("リクエストを出した"))
    q = release.Question("q", tid, DEPLOY, "release-gate", "release", ("approved",), 8)

    assert release.request_release(st, run.read(st, tid), q, {}) is None


def test_a_skipping_mvv_verdict_does_not_replace_a_recorded_approval(repo, st, env, capsys):
    tid = _approved_in_the_flow(st, capsys, repo, env)
    finishing.main(["retry", tid, DESIGN, "--reason", "やり直す"])
    capsys.readouterr()
    # 出力の失敗の後と同じ形にして、判定のステップから初めから流し直す
    for kind, payload in (("started", {"inputs": {}}), ("failed", {"step": None})):
        run._write(st, tid, run.model.EventDraft(kind, "workflow", DESIGN, payload=payload, rev=1))
    finishing.main(["retry", tid, DESIGN, "--reason", "やり直す"])
    capsys.readouterr()
    held = {**lanes.values(repo, "sprint/s1")["mvv_gate"], "skip": True, "holds": [], "head": merge_hub.HEAD}
    report = run.result.result("script", run.result.OK, "省ける", outputs={"mvv_gate": held})
    env.script["design-mvv"] = [lambda r: invoke.finished(r, report)]
    _merge(env)

    _next(st, tid, repo, env)

    answered = [ev.draft.payload["by"] for ev in run.read(st, tid).row_events(DESIGN) if ev.draft.kind == run.ANSWERED]
    assert answered == ["human:reviewer"] and _state(st, tid, DESIGN) == "finished"


def test_reread_is_refused_for_a_hand_finished_wait(repo, st, env, capsys):
    tid = _held_design(st, capsys, repo, env)
    before = _kinds(st, tid, DESIGN)

    out = answering.reread(st, tid, repo, env.env)

    assert out["status"] == "stopped" and "--later reopen" in out["cause"]["detail"], out
    assert _kinds(st, tid, DESIGN) == before


def test_a_hand_finished_deploy_revokes_the_old_approval_and_its_answer_does_not_allow_a_merge(repo, st, env, capsys):
    env.script["release"] = [lambda r: invoke.StageResult(run.result.stopped("plan", "test_failed", "落ちた"), {})]
    post = gate.github_post(repo, hub=lambda: env.hub)
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    _answer(st, tid, repo, env, _next(st, tid, repo, env))
    _answer(st, tid, repo, env, _next(st, tid, repo, env), key="k-release", post=post)
    _next(st, tid, repo, env)

    def judged(head):
        target = merge_approval.MergeTarget(merge_hub.REPO, 8, "main", head)
        return merge_approval.merge_judgment(merge_approval.merge_rows(env.hub, 8, env.hub.viewer()), target)

    assert judged(merge_hub.HEAD) is not None
    env.hub.pulls_of[8] = merge_hub.pull(8, head=merge_hub.OTHER)

    assert _finish(capsys, repo, tid, DEPLOY, _released_values(repo))["status"] == "ok"

    assert judged(merge_hub.HEAD) is None
    _answer(st, tid, repo, env, _next(st, tid, repo, env), key="k-held", post=post)
    assert judged(merge_hub.OTHER) is None
    assert gate.approved_head(run.read(st, tid), "release") is None
    _next(st, tid, repo, env)
    assert _state(st, tid, DEPLOY) == "finished" and judged(merge_hub.OTHER) is None


def test_a_refused_finish_and_a_non_advancing_recheck_revoke_nothing(repo, st, env, capsys):
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    _answer(st, tid, repo, env, _next(st, tid, repo, env))
    _merge(env, merge_hub.OTHER)
    before = _kinds(st, tid, DESIGN)

    assert _finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR})["cause"]["kind"] == "refused"
    env.reader.forget()
    observe = answering.github_observe(repo, st.root, reader=env.reader)
    why = gate.recheck(st, tid, observe, revoke=lambda r: pytest.fail("取り消した"), how=answering.Recheck(advancing=False))

    assert why is not None and "ndf progress next" in why
    assert env.hub.dequeued == [] and _kinds(st, tid, DESIGN) == before


def test_a_hand_finish_waits_for_an_answer_being_written(repo, st, env, capsys, monkeypatch):
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    writing = store.approval_lock(st, tid)
    writing.__enter__()
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        writing.__exit__(None, None, None)  # 回答を書き終える

    monkeypatch.setattr(finishing, "time", SimpleNamespace(sleep=sleep, monotonic=lambda: 0.0))

    out = _finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR})

    assert out["status"] == "ok" and len(waits) == 1, out
    assert "finish_held" in _kinds(st, tid, DESIGN)


def test_a_hand_finish_revokes_an_approval_whose_answer_was_not_recorded(repo, st, env, capsys):
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    shown = _next(st, tid, repo, env)
    write = answering.github_post(repo, hub=lambda: env.hub)

    def post_then_fail(row) -> bool:
        write(row)
        raise model.Refused("確定に失敗")

    with pytest.raises(model.Refused, match="確定に失敗"):
        _answer(st, tid, repo, env, shown, post=post_then_fail)

    def judged():
        target = merge_approval.MergeTarget(merge_hub.REPO, DESIGN_PR, "main", merge_hub.HEAD)
        return merge_approval.merge_judgment(merge_approval.merge_rows(env.hub, DESIGN_PR, env.hub.viewer()), target)

    assert judged() is not None

    out = _finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR})

    assert out["status"] == "ok" and judged() is None, out


def _fail_answer(st, tid, repo, env, shown: dict, write=lambda row: True) -> None:
    """`write` で証跡を扱った後に、回答の確定を失敗させる。"""

    def post_then_fail(row) -> bool:
        write(row)
        raise model.Refused("確定に失敗")

    with pytest.raises(model.Refused, match="確定に失敗"):
        _answer(st, tid, repo, env, shown, post=post_then_fail)


@pytest.mark.parametrize("tried", [False, True])
def test_a_hand_finish_keeps_the_queue_when_no_approval_was_posted(repo, st, env, capsys, tried):
    """提示しただけの質問と、証跡を投稿する前に失敗した回答は、PR をキューから外さない。"""
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    shown = _next(st, tid, repo, env)
    if tried:
        _fail_answer(st, tid, repo, env, shown)

    out = _finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR})

    assert out["status"] == "ok" and env.hub.dequeued == [] and env.hub.comments_of == {}, out


def test_a_hand_finish_revokes_an_unrecorded_approval_after_the_material_was_presented_again(repo, st, env, capsys):
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    shown = _next(st, tid, repo, env)
    _fail_answer(st, tid, repo, env, shown, answering.github_post(repo, hub=lambda: env.hub))
    env.hub.files["develop"] = env.hub.files["main"]
    env.hub.pulls_of[DESIGN_PR] = merge_hub.pull(DESIGN_PR, base="develop")
    _next(st, tid, repo, env)

    out = _finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR})

    target = merge_approval.MergeTarget(merge_hub.REPO, DESIGN_PR, "main", merge_hub.HEAD)
    rows = merge_approval.merge_rows(env.hub, DESIGN_PR, env.hub.viewer())
    assert out["status"] == "ok" and merge_approval.merge_judgment(rows, target) is None, out


def test_a_reopen_revokes_an_approval_whose_answer_was_not_recorded(repo, st, env, capsys, monkeypatch):
    monkeypatch.setattr(finishing, "github_records", lambda cwd: lanes._NoRecords())
    tid = _held_design(st, capsys, repo, env)
    _fail_answer(st, tid, repo, env, _next(st, tid, repo, env), answering.github_post(repo, hub=lambda: env.hub))

    finishing.main(["retry", tid, DESIGN, "--reason", "直す", "--later", "reopen"])

    out = last_result(capsys.readouterr().out)
    target = merge_approval.MergeTarget(merge_hub.REPO, DESIGN_PR, "main", merge_hub.HEAD)
    rows = merge_approval.merge_rows(env.hub, DESIGN_PR, env.hub.viewer())
    assert out["status"] == "ok" and merge_approval.merge_judgment(rows, target) is None, out
    assert env.hub.dequeued == [DESIGN_PR] and approval_decide.stage_revocations(run.read(st, tid), DESIGN) == []


def test_a_recorded_approval_leaves_no_posted_note(repo, st, env, capsys):
    tid = _new(capsys)
    _before_design(capsys, repo, tid)

    _answer(st, tid, repo, env, _next(st, tid, repo, env), post=answering.github_post(repo, hub=lambda: env.hub))

    revocations = approval_decide.stage_revocations(run.read(st, tid), DESIGN)
    assert env.hub.comments_of and [r.unconfirmed for r in revocations] == [False]


def test_a_send_back_revokes_an_approval_whose_answer_was_not_recorded(repo, st, env, capsys):
    """先の提示への未確定の証跡は、後の回答を待ちへ戻すときにも取り消す（対象が元へ戻っても使えない）。"""
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    write = answering.github_post(repo, hub=lambda: env.hub)
    _fail_answer(st, tid, repo, env, _next(st, tid, repo, env), write)
    env.hub.pulls_of[DESIGN_PR] = merge_hub.pull(DESIGN_PR, head=merge_hub.OTHER)
    _answer(st, tid, repo, env, _next(st, tid, repo, env), key="k-2", post=write)
    env.hub.pulls_of[DESIGN_PR] = merge_hub.pull(DESIGN_PR, base="develop")
    env.hub.files["develop"] = env.hub.files["main"]
    env.reader.forget()
    revoked: list = []

    gate.recheck(st, tid, answering.github_observe(repo, st.root, reader=env.reader), revoke=revoked.extend)

    assert {r.target.head for r in revoked} == {merge_hub.HEAD, merge_hub.OTHER}


def test_an_unrecorded_approval_is_found_without_the_checkout_files(repo, st, env, capsys):
    """投稿前の記録はステートマシンの DB にあり、別の保存先から開いた操作も取り消す承認を指せる。"""
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    _fail_answer(st, tid, repo, env, _next(st, tid, repo, env))

    (held,) = approval_decide.stage_revocations(run.read(st, tid), DESIGN)

    assert held.unconfirmed and held.target.number == DESIGN_PR


def test_a_rejection_voids_an_approval_whose_answer_was_not_recorded(repo, st, env, capsys):
    """同じ提示への却下は、投稿済みの未確定の承認を PR で取り消し、取り消し対象にも残す。"""
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    shown = _next(st, tid, repo, env)
    write = answering.github_post(repo, hub=lambda: env.hub)
    _fail_answer(st, tid, repo, env, shown, write)

    _answer(st, tid, repo, env, shown, "rejected", key="k-2", post=write)

    target = merge_approval.MergeTarget(merge_hub.REPO, DESIGN_PR, "main", merge_hub.HEAD)
    rows = merge_approval.merge_rows(env.hub, DESIGN_PR, env.hub.viewer())
    assert merge_approval.merge_judgment(rows, target) is None
    assert [r.unconfirmed for r in approval_decide.stage_revocations(run.read(st, tid), DESIGN)] == [True]


def test_an_approval_is_written_again_after_a_rejection_of_the_same_target(repo, st, env, capsys):
    """未確定の承認の後に別の識別子の却下があれば、打ち直した承認を書き、PR の判定が承認になる。"""
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    shown = _next(st, tid, repo, env)
    write = answering.github_post(repo, hub=lambda: env.hub)
    _fail_answer(st, tid, repo, env, shown, write)
    target = merge_approval.MergeTarget(merge_hub.REPO, DESIGN_PR, "main", merge_hub.HEAD)
    other = merge_approval.MergeRow("rejected", "user", target, True, "other")
    merge_approval.write_merge_row(env.hub, other, env.hub.viewer())

    _answer(st, tid, repo, env, shown, key="k-2", post=write)

    assert merge_approval.merge_judgment(merge_approval.merge_rows(env.hub, DESIGN_PR, env.hub.viewer()), target) is not None


@pytest.mark.parametrize("by", ["conductor", "service:bot"])
def test_a_held_stage_is_approved_only_by_a_person(repo, st, env, capsys, by):
    """本番へ反映されないマージでも、手で終えた工程の承認は人の回答に限る。"""
    env.hub.files["main"] = (merge_hub.project_text(merge_hub.strategy(base="main", production="prod")), "c2")
    tid = _held_design(st, capsys, repo, env)
    shown = _next(st, tid, repo, env)

    with pytest.raises(model.Refused, match="確定しない"):
        _answer(st, tid, repo, env, shown, by=by)


def test_an_answer_is_compared_with_the_target_read_under_the_work_lock(repo, st, env, capsys):
    """作業の排他を取る前に読んだ対象は使わず、排他の中で読み直して承認を照らす。"""
    tid = _new(capsys)
    _before_design(capsys, repo, tid)
    _answer(st, tid, repo, env, _next(st, tid, repo, env))  # 回答の確かめが対象を読み、`env.reader` に残る
    env.hub.files["develop"] = env.hub.files["main"]
    env.hub.pulls_of[DESIGN_PR] = merge_hub.pull(DESIGN_PR, base="develop")
    observe = answering.github_observe(repo, st.root, reader=env.reader)
    verify = answering.verifier(st, observe, answering.never_docs_only, lambda r: None, env.reader.forget)

    lanes.turn(st, tid, repo, replace(env.env, verify=verify))

    m = run.decide(run.read(st, tid))
    assert (m.action, m.stage.name) == (run.Action.GATE, DESIGN)


def test_the_verifier_takes_the_reply_once_after_dropping_the_earlier_reads(st, env, capsys):
    tid = _new(capsys)
    calls: list[str] = []
    verify = answering.verifier(
        st,
        answering.no_observe,
        answering.never_docs_only,
        lambda r: None,
        lambda: calls.append("forget"),
        lambda: calls.append("take") or "書いた",
    )

    assert (verify(tid, None), verify(tid, None)) == ("書いた", None)
    assert calls == ["forget", "take", "forget"]
