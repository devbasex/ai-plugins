"""工程から作業を呼ぶ層（ndf.progress.invoke。#1142 の設計の「工程から作業を呼ぶ層」・3364-7）。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from ndf.contracts import result
from ndf.progress import definition, invoke, model


def _stage(name: str, mode: str = "light"):
    return definition.load_definition(mode).stage(name)


def _run(stage_name: str, step_index: int = 0, inputs: dict | None = None) -> invoke.StageRun:
    s = _stage(stage_name)
    return invoke.stage_run(s, s.steps[step_index], inputs if inputs is not None else {})


def test_resolve_inputs_passes_only_the_declared_keys():
    s = _stage("作業場所の用意")
    assert invoke.resolve_inputs(s, {"issues": [3092], "mode": "light", "pace": "normal"}) == {"issues": [3092]}


def test_resolve_inputs_names_every_missing_key():
    with pytest.raises(invoke.StageInputMissing) as caught:
        invoke.resolve_inputs(_stage("コードレビュー"), {"issues": [1]})
    assert caught.value.keys == ("worktree", "branch")


@pytest.mark.parametrize(
    "values",
    [
        {"worktree": "relative/path", "branch": "sprint/s1"},
        {"worktree": "/work/a", "branch": "-rf"},
        {"worktree": "/work/a", "branch": "a..b"},
        {"worktree": "--upload-pack=x", "branch": "sprint/s1"},
    ],
)
def test_resolve_inputs_refuses_values_of_the_wrong_shape(values):
    # 3364-7: 次の工程の引数・作業ディレクトリになる値は、鍵があるかだけでなく形を確かめてから渡す
    with pytest.raises(invoke.StageInputInvalid):
        invoke.resolve_inputs(_stage("コードレビュー"), values)


def test_delivery_must_not_be_empty():
    assert invoke.value_errors({"delivery": {}})
    assert not invoke.value_errors({"delivery": {"kind": "plugin"}})


def test_stage_run_refuses_an_unknown_call_and_a_missing_name():
    s = _stage("設計")
    bad = definition.stages.StepDef("x", s.name, "ndf.unknown", "work", "none")
    with pytest.raises(model.Refused):
        invoke.stage_run(s, bad, {})
    nameless = definition.stages.StepDef("y", s.name, "ndf.plan", "work", "none")
    with pytest.raises(model.Refused):
        invoke.stage_run(s, nameless, {})


def test_listen_step_is_not_invoked():
    run = _run("設計", 2)
    assert run.kind == invoke.LISTEN
    with pytest.raises(model.Refused):
        invoke.invoke(run, Path("/"), Path("/tmp"))


def test_read_report_takes_the_last_line_and_refuses_bad_shapes():
    good = result.result("plan", "ok", "済んだ")
    assert invoke.read_report(f"途中の行\n{json.dumps(good)}\n") == good
    assert invoke.read_report("") is None
    assert invoke.read_report("JSON でない") is None
    assert invoke.read_report(json.dumps({"status": "ok"})) is None


def test_finished_keeps_only_declared_outputs_and_warns_about_the_rest():
    run = _run("作業場所の用意", inputs={"issues": [1]})
    report = result.result("plan", "ok", "済んだ", outputs={"worktree": "/w", "branch": "sprint/s", "pr": 3})
    got = invoke.finished(run, report)
    assert got.outputs == {"worktree": "/w", "branch": "sprint/s"}
    assert got.warnings and "pr" in got.warnings[0]


def test_finished_turns_a_badly_shaped_output_into_bad_report():
    run = _run("作業場所の用意", inputs={"issues": [1]})
    got = invoke.finished(run, result.result("plan", "ok", "済んだ", outputs={"worktree": "/w", "branch": "-x"}))
    assert got.status == "stopped" and got.cause["kind"] == "bad_report"


def test_record_outputs_writes_one_event_with_the_declared_keys_only():
    s = _stage("作業場所の用意")
    draft, warnings = invoke.record_outputs(s, 1, {"worktree": "/w", "branch": "sprint/s", "pr": 9})
    assert draft is not None and draft.kind == "outputs_recorded" and draft.actor == "workflow"
    assert draft.payload == {"outputs": {"worktree": "/w", "branch": "sprint/s"}}
    assert warnings and "pr" in warnings[0]
    assert invoke.record_outputs(s, 1, {"pr": 9})[0] is None


def test_plan_argv_passes_inputs_and_the_answer():
    run = _run("作業場所の用意", inputs={"issues": [1, 2]})
    argv = invoke.plan_argv(run, Path("/tmp/w"), "approved")
    assert argv[argv.index("plan") + 1] == "next" and argv[argv.index("next") + 1].endswith("templates/worktree.json")
    assert argv[argv.index("--state-dir") + 1] == "/tmp/w"
    assert argv[argv.index("--set") + 1] == "issues=[1, 2]"
    assert argv[-2:] == ["--answer", "approved"]
    assert argv[1] == "-P"


def test_plan_without_a_template_stops_as_precondition(tmp_path, monkeypatch):
    monkeypatch.setattr(invoke, "template_path", lambda name: tmp_path / f"{name}.json")
    got = invoke.invoke(_run("作業場所の用意", inputs={"issues": [1]}), tmp_path, tmp_path / "w")
    assert got.status == "stopped" and got.cause["kind"] == "precondition"


def test_plan_is_run_with_the_package_on_the_import_path_and_the_notes(tmp_path, monkeypatch):
    template = tmp_path / "worktree.json"
    template.write_text("{}")
    monkeypatch.setattr(invoke, "template_path", lambda name: template)
    seen = {}

    def runner(argv, cwd, env):
        seen.update(argv=argv, env=env)
        out = result.result("plan", "ok", "済んだ", outputs={"worktree": "/w", "branch": "sprint/s"})
        return subprocess.CompletedProcess(argv, 0, json.dumps(out) + "\n", "")

    run = invoke.StageRun(**{**vars(_run("作業場所の用意", inputs={"issues": [1]})), "notes": "直す所"})
    got = invoke.invoke(run, tmp_path, tmp_path / "w", runner=runner)
    assert got.outputs == {"worktree": "/w", "branch": "sprint/s"}
    assert str(invoke.SRC) in seen["env"]["PYTHONPATH"].split(os.pathsep)
    assert seen["env"][invoke.NOTES_ENV] == "直す所"
    assert (tmp_path / "w").is_dir()


def test_plan_that_prints_no_result_is_bad_report(tmp_path, monkeypatch):
    template = tmp_path / "worktree.json"
    template.write_text("{}")
    monkeypatch.setattr(invoke, "template_path", lambda name: template)
    got = invoke.invoke(
        _run("作業場所の用意", inputs={"issues": [1]}),
        tmp_path,
        tmp_path / "w",
        runner=lambda argv, cwd, env: subprocess.CompletedProcess(argv, 1, "落ちた\n", ""),
    )
    assert got.cause["kind"] == "bad_report"


def test_script_receives_the_inputs_as_json(tmp_path):
    run = invoke.StageRun("script", "x", {"issues": [5]}, (), "none", "s", {"argv": ["x", "--flag"]})
    seen = {}

    def runner(argv, cwd, env):
        seen.update(argv=argv, input=json.loads(env[invoke.INPUT_ENV]))
        return subprocess.CompletedProcess(argv, 0, json.dumps(result.result("script", "ok", "済んだ")), "")

    assert invoke.invoke(run, tmp_path, tmp_path, runner=runner).status == "ok"
    assert seen == {"argv": ["x", "--flag"], "input": {"issues": [5]}}


def test_skill_prompt_names_the_skill_and_carries_the_notes():
    run = invoke.StageRun("skill", "design", {"issues": [5]}, (), "none", "s", {"skill": "design"}, notes="ここを直す")
    prompt = invoke.skill_prompt(run)
    assert prompt.startswith("/") and ":design" in prompt.splitlines()[0]
    assert '"issues": [5]' in prompt and prompt.endswith("ここを直す")


@pytest.mark.parametrize(
    ("failure", "kind"),
    [(None, "transient"), ("usage_limit", "usage_limit"), ("timeout", "interrupted"), ("refused", "precondition")],
)
def test_skill_failures_map_to_causes(failure, kind):
    from ndf.adapters import claude

    assert invoke._skill_cause(claude.Failure(failure) if failure else None) == kind


# --- 現状固定: _call_skill（I-025。`claude -p` の呼び出しと、答えから結果への写し） ---------------------


def _skill_run() -> invoke.StageRun:
    return invoke.StageRun("skill", "design", {"issues": [5]}, ("pr",), "none", "s", {"skill": "design"})


def _reply(**kw):
    from ndf.adapters import claude

    return claude.ClaudeReply(**{"ok": True, "answer": None, "raw": "", "cost": 0.0, **kw})


@pytest.fixture
def calls(monkeypatch):
    """`claude.call_claude` を差し替え、受けた引数を残す。答えは `calls.reply` に置く。"""
    from ndf.adapters import claude

    seen: list[dict] = []
    box = {"reply": _reply()}

    def fake(argv, prompt, timeout, cwd=None):
        seen.append({"argv": list(argv), "prompt": prompt, "timeout": timeout, "cwd": cwd})
        return box["reply"]

    monkeypatch.setattr(claude, "call_claude", fake)
    return box, seen


def test_skill_is_called_with_full_permission_the_result_schema_and_the_implement_limit(tmp_path, calls):
    from ndf import contracts
    from ndf.adapters import claude
    from ndf.contracts import limits

    box, seen = calls
    run = _skill_run()
    box["reply"] = _reply(answer=result.result("skill", "ok", "済んだ", outputs={"pr": 3}))
    got = invoke.invoke(run, tmp_path, tmp_path / "w")
    schema = contracts.load_json(contracts.schema_path("result"))
    want = claude.claude_args(claude.ClaudeCall(system=invoke.SKILL_SYSTEM, permission=claude.Permission.FULL, json_schema=schema))
    assert seen == [{"argv": want, "prompt": invoke.skill_prompt(run), "timeout": limits.seconds(invoke.SKILL_LIMIT), "cwd": tmp_path}]
    assert got.status == "ok" and got.outputs == {"pr": 3} and got.warnings == ()
    # Skill の作業はプランの置き場を作らない
    assert not (tmp_path / "w").exists()


@pytest.mark.parametrize(
    ("failure", "kind"),
    [(None, "transient"), ("usage_limit", "usage_limit"), ("timeout", "interrupted"), ("no_slot", "precondition")],
)
def test_skill_that_cannot_answer_stops_with_the_cause_of_the_failure(tmp_path, calls, failure, kind):
    from ndf.adapters import claude

    box, _ = calls
    box["reply"] = _reply(ok=False, error="落ちた", failure=claude.Failure(failure) if failure else None)
    got = invoke.invoke(_skill_run(), tmp_path, tmp_path)
    assert got.status == "stopped" and got.outputs == {}
    assert got.report["tool"] == "skill"
    assert got.cause == {"kind": kind, "detail": "Skill design を回せない: 落ちた"}


@pytest.mark.parametrize("answer", [None, "文字列", [1], {"status": "ok"}])
def test_skill_answer_that_is_not_a_result_is_bad_report(tmp_path, calls, answer):
    box, _ = calls
    box["reply"] = _reply(answer=answer)
    got = invoke.invoke(_skill_run(), tmp_path, tmp_path)
    assert got.status == "stopped" and got.cause["kind"] == "bad_report" and got.outputs == {}
    assert got.cause["detail"] == "作業 skill design が結果 JSON を返さない: 答えが結果 JSON の形に合わない"


def test_skill_answer_keeps_only_declared_outputs(tmp_path, calls):
    box, _ = calls
    box["reply"] = _reply(answer=result.result("skill", "ok", "済んだ", outputs={"pr": 3, "extra": 1}))
    got = invoke.invoke(_skill_run(), tmp_path, tmp_path)
    assert got.outputs == {"pr": 3} and got.warnings and "extra" in got.warnings[0]
