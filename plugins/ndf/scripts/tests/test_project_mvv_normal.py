"""プロジェクト MVV を normal・auto の各工程で従わせる（#1400）。

- work のステップの worker へ MVV の節を渡す（AC1・AC7・AC12・I4・I10）
- `mvv-gate.py check --advise`（助言の MVV 判定）: 判定によらず 0・承認ゲートの記録を書かない・行に pace（AC4〜AC7・I1・I3・I5）
- `pace: normal` と `--state` のスプリントのプラン（AC4・AC5・I2・I7）と覆し（AC6・I8）
- スプリント MVV の特定と根拠の項目（AC12・I11・I12・決定 13〜16）

claude と gh は `test_project_mvv` の偽物を使う（呼ばれるたびに calls.txt へ 1 行を足す）。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_project_mvv import BODY, GATE_PY, MVV_PY, STATE_PY, answer, approve, body_file, calls, env, run, sha  # noqa: F401
from test_supervise_pace import assert_transitions_exist, cli, load, steps_of

import project_mvv as pm  # noqa: E402
import project_mvv_signals as pms  # noqa: E402
from supervise_lib import engine, worker_steps  # noqa: E402
from supervise_lib.prompts import FULL_SYSTEM, WORK_SYSTEM  # noqa: E402
from supervise_lib.state import RunState  # noqa: E402
from supervise_lib.steps import JudgeStep  # noqa: E402

SPRINT = "## Mission\n速く届ける\n\n## Vision\n止まらない\n\n## Value\n1. 実測で決める\n2. R1 は人が決める\n"
NOT_FOLLOW = '{"verdict": "not_follow", "reasons": ["Value 2 に反する"], "boundary": [], "basis": ["Value 2"]}'
FOLLOW_GATE = '{"verdict": "follow", "reasons": ["Value 1 に沿う"], "boundary": [], "basis": ["Value 1"]}'


def normal_state(env, *extra: str, name: str = "m.json") -> Path:
    state = env["tmp"] / "ns" / name
    code, _, text = run(env, "init", str(state), "--name", "m", "--root", str(env["root"]), *extra, script=STATE_PY)
    assert code == 0, text
    return state


def advise(env, state: Path, *extra: str) -> tuple[int, dict, list[dict], Path]:
    material = env["tmp"] / "approval.md"
    material.write_text("# 配布\n")
    log, note = env["tmp"] / "gate.jsonl", env["tmp"] / "note.md"
    args = ["check", "--sprint", str(state), "--gate", "release", "--material", str(material), "--log", str(log)]
    code, out, text = run(env, *args, "--root", str(env["root"]), "--note", str(note), "--advise", *extra, script=GATE_PY)
    assert out, text
    return code, out, pms.read_jsonl(log), note


def gates_of(state: Path) -> list[dict]:
    return json.loads(state.read_text()).get("gates", [])


# ---------------------------------------------------------------- 助言の MVV 判定（AC4〜AC7）


@pytest.mark.parametrize(
    ("text", "verdict"),
    [(FOLLOW_GATE, "follow"), (NOT_FOLLOW, "not_follow"), ('{"verdict": "unknown", "reasons": [], "boundary": []}', "unknown")],
)
def test_advise_returns_0_for_every_verdict_and_never_records_the_gate(env, text, verdict):
    assert approve(env) == 0
    state = normal_state(env)
    answer(env, text)
    code, out, rows, note = advise(env, state)
    assert (code, out["status"]) == (0, "ok") and gates_of(state) == []  # I1
    assert rows[-1]["verdict"] == verdict and rows[-1]["passed"] is False and rows[-1]["pace"] == "normal"
    assert rows[-1]["sprint_mvv"] is None
    text = note.read_text()
    assert "助言" in text and f"（{verdict}。" in text and "根拠: " in text and "（MVV 版 1）" in text


def test_advise_writes_the_reason_when_the_answer_is_unreadable(env):
    assert approve(env) == 0
    state = normal_state(env)
    answer(env, "判定できませんでした")
    code, _, rows, note = advise(env, state)
    assert code == 0 and rows[-1]["verdict"] == "unreadable" and "判定を読めない" in note.read_text()


def test_advise_machine_checks_skip_the_llm_and_land_in_the_note(env):
    old = normal_state(env, name="old.json")  # MVV を承認する前の状態（参照を持たない）
    assert approve(env) == 0
    before = calls(env, "claude")
    code, out, rows, note = advise(env, old)
    assert code == 0 and calls(env, "claude") == before and rows[-1]["verdict"] == "machine"
    assert "参照が無い" in note.read_text() and "--pace fast" not in rows[-1]["reasons"][0]
    code, _, rows, _ = advise(env, normal_state(env), "--mode", "operation")
    assert code == 0 and calls(env, "claude") == before and rows[-1]["verdict"] == "machine"


def test_advise_without_an_approved_project_mvv_calls_nothing_and_writes_nothing(env):
    state = normal_state(env)
    code, out, rows, note = advise(env, state)
    assert code == 0 and out["items"][0] == {"verdict": "none", "status": "none"}
    assert rows == [] and not note.exists() and calls(env, "claude") == 0  # I3


def test_advise_treats_a_missing_pace_declaration_as_no_boundary(env):
    assert approve(env) == 0
    state = normal_state(env)
    answer(env, FOLLOW_GATE)
    assert not (env["root"] / ".ndf" / "pace.json").exists()
    code, _, rows, _ = advise(env, state)
    assert code == 0 and rows[-1]["verdict"] == "follow"
    # --advise の無い呼び出しは今どおり宣言の欠如を外れにする
    code, _, text = run(
        env,
        "check",
        *("--sprint", str(state), "--gate", "release", "--material", str(env["tmp"] / "approval.md")),
        *("--log", str(env["tmp"] / "gate.jsonl"), "--root", str(env["root"])),
        script=GATE_PY,
    )
    assert code == 10 and pms.read_jsonl(env["tmp"] / "gate.jsonl")[-1]["pace"] == "normal"
    (env["root"] / ".ndf" / "pace.json").write_text("{")
    assert advise(env, state)[2][-1]["verdict"] == "machine"


# ---------------------------------------------------------------- 覆し（AC6・I8）


def user_gate(env, state: Path, name: str, *extra: str) -> int:
    log = env["tmp"] / "gate.jsonl"
    args = ["gate", str(state), name, "--what", "承認", "--mvv-log", str(log), "--root", str(env["root"]), *extra]
    return run(env, *args, script=STATE_PY)[0]


def test_user_answers_against_the_advice_are_overrides(env):
    assert approve(env) == 0
    state = normal_state(env)
    answer(env, NOT_FOLLOW)
    advise(env, state)
    assert user_gate(env, state, "関門 2", "--outcome", "approved") == 0
    answer(env, FOLLOW_GATE)
    advise(env, state)
    assert user_gate(env, state, "関門 2", "--outcome", "rejected") == 0
    advise(env, state)
    assert user_gate(env, state, "関門 2", "--outcome", "approved") == 0
    rows = pms.read_jsonl(env["tmp"] / "state" / "project-mvv-signals.jsonl")
    assert [r["kind"] for r in rows] == ["override_pass", "override_reject"]


def test_the_override_compares_with_the_verdict_of_the_same_pr(tmp_path):
    state = {"gates": []}
    log = tmp_path / "gate.jsonl"
    sprint = str(tmp_path / "m.json")
    pms.append_jsonl(log, {"at": "2026-09-28T00:00:00Z", "gate": "design", "sprint": sprint, "pr": [1], "verdict": "not_follow"})
    pms.append_jsonl(log, {"at": "2026-09-28T00:01:00Z", "gate": "design", "sprint": sprint, "pr": [2], "verdict": "follow"})
    assert pms.last_mvv_verdict(state, sprint, "関門 1", log, 1) == "not_follow"
    assert pms.last_mvv_verdict(state, sprint, "関門 1", log, 2) == "follow"
    assert pms.last_mvv_verdict(state, sprint, "関門 1", log) == "follow"


# ---------------------------------------------------------------- スプリント MVV（AC12）


def test_normal_init_copies_the_sprint_mvv_without_vetting_it(env):
    assert approve(env) == 0
    before = calls(env, "claude")
    state = normal_state(env, "--mvv", body_file(env, SPRINT, "sprint.md"))
    m = json.loads(state.read_text())
    assert m["project_mvv"] == {"version": 1, "sha256": sha(BODY)} and m["mvv"]["sha256"] == sha(SPRINT)
    assert calls(env, "claude") == before  # I11: 照合（vet）をしない
    code, out, _ = run(env, "init", str(env["tmp"] / "x.json"), "--name", "m", "--mvv", str(env["tmp"] / "no.md"), script=STATE_PY)
    assert code == 3 and "MVV のファイルが無い" in out["summary"]


def test_normal_init_reads_the_milestone_and_goes_on_without_the_headings(env):
    (env["tmp"] / "gh-out.json").write_text(SPRINT)
    m = json.loads(normal_state(env, "--milestone", "26").read_text())
    assert Path(m["mvv"]["path"]).read_text().startswith("## Mission") and "project_mvv" not in m
    (env["tmp"] / "gh-out.json").write_text("## Mission\nだけ\n")
    state = env["tmp"] / "ns" / "short.json"
    code, out, text = run(env, "init", str(state), "--name", "m", "--milestone", "26", "--root", str(env["root"]), script=STATE_PY)
    assert code == 0 and "mvv" not in json.loads(state.read_text()), text
    assert out["items"][-1]["kind"] == "sprint_mvv" and out["items"][-1]["result"] == "none"


def test_advise_reads_the_sprint_mvv_without_its_approval_record(env):
    assert approve(env) == 0
    state = normal_state(env, "--mvv", body_file(env, SPRINT, "sprint.md"))
    answer(env, '{"verdict": "follow", "reasons": ["x"], "boundary": [], "basis": ["スプリント Value 1", "Value 1", "R1"]}')
    code, _, rows, note = advise(env, state)
    assert code == 0 and rows[-1]["verdict"] == "follow" and rows[-1]["sprint_mvv"] == {"sha256": sha(SPRINT)}
    assert rows[-1]["basis"] == ["スプリント Value 1", "Value 1", "R1"]
    assert f"# スプリント MVV（sha256 {sha(SPRINT)[:8]}）" in (env["tmp"] / "prompt.txt").read_text()
    assert f"根拠: Value 1 / スプリント Value 1 / R1（MVV 版 1・スプリント MVV {sha(SPRINT)[:8]}）" in note.read_text()
    # 写しが状態と食い違えば LLM を呼ばず機械のチェックで外れる（I10）
    Path(json.loads(state.read_text())["mvv"]["path"]).write_text(SPRINT + "足した\n")
    before = calls(env, "claude")
    code, _, rows, _ = advise(env, state)
    assert code == 0 and calls(env, "claude") == before and rows[-1]["verdict"] == "machine"


def test_basis_keeps_the_sprint_items_apart_from_the_project_items(env):
    assert approve(env) == 0
    mvv = pm.load_mvv(env["root"])
    items = pm.basis(["スプリント Value 1", "Value 2", "R1", "スプリント R1", "スプリント Value 9"], mvv, sprint=SPRINT)
    assert items == ["スプリント Value 1", "Value 2", "R1"]
    assert pm.basis_phrase(items, mvv, "3f9a1c2e00") == "根拠: Value 2 / スプリント Value 1 / R1（MVV 版 1・スプリント MVV 3f9a1c2e）"
    assert pm.basis_phrase(["Value 1"], mvv) == "根拠: Value 1（MVV 版 1）"  # スプリント MVV が無ければ今の句


def test_context_adds_the_sprint_mvv_only_with_a_source(env):
    base = run(env, "context", "--root", str(env["root"]), "--format", "json")[1]["items"][0]
    assert approve(env) == 0
    mvv = pm.load_mvv(env["root"])
    (env["tmp"] / "gh-out.json").write_text(SPRINT)
    code, out, _ = run(env, "context", "--root", str(env["root"]), "--format", "json", "--milestone", "26")
    item = out["items"][0]
    assert code == 0 and item["block"] == pm.block(mvv, SPRINT) and item["sprint_mvv"]["sha256"] == sha(SPRINT)
    code, out, _ = run(env, "context", "--root", str(env["root"]), "--format", "json")
    assert out["items"][0]["block"] == pm.block(mvv) and "sprint_mvv" not in out["items"][0]
    (env["tmp"] / "gh-out.json").write_text("見出しなし\n")
    out = run(env, "context", "--root", str(env["root"]), "--format", "json", "--milestone", "26")[1]
    assert out["items"][0]["block"] == pm.block(mvv) and "reason" in out["items"][0]["sprint_mvv"]
    assert "sprint_mvv" not in base and base["block"] == pm.block(pm.ProjectMvv("none"))


# ---------------------------------------------------------------- work のステップと judge（AC1・AC7・AC12・I4・I10）


class FakeClaude:
    def __init__(self, texts: list[str]):
        self.texts, self.calls = list(texts), []

    def call(self, system, prompt, tools, cwd, timeout, **kw):
        self.calls.append({"system": system, "prompt": prompt, **kw})
        text = self.texts.pop(0) if self.texts else "## 作業の報告\n- 結果: 完了"
        return {"ok": True, "text": text, "seconds": 0, "session": "s1", "usage": {}}

    def record_usage(self, kind, res):
        pass


def work_ctx(root: Path, tmp: Path, plan: dict | None = None, texts=()) -> SimpleNamespace:
    plan = {"作業場所": str(root), "課題": [], **(plan or {})}
    state = RunState(tmp / "sv", plan)
    state.cur = {"id": "w"}
    return SimpleNamespace(
        plan=plan, cwd=str(root), state=state, claude=FakeClaude(list(texts)), inputs_text=lambda s: "", tick=lambda: None
    )


def sprint_plan(env) -> dict:
    return {"スプリント状態": str(normal_state(env, "--mvv", body_file(env, SPRINT, "sprint.md")))}


def test_worker_gets_the_same_block_in_all_three_calls(env, monkeypatch):
    assert approve(env) == 0
    mvv = pm.load_mvv(env["root"])
    ctx = work_ctx(env["root"], env["tmp"])
    step = worker_steps.WorkStep()
    step.execute(ctx, {"id": "w", "prompt": "直す"})
    assert ctx.claude.calls[-1]["system"] == WORK_SYSTEM + "\n\n" + pm.block(mvv)
    ctx = work_ctx(env["root"], env["tmp"], texts=["途中"])
    step.execute(ctx, {"id": "w", "prompt": "/ndf:design #1", "full": True})
    assert [c["system"] for c in ctx.claude.calls] == [FULL_SYSTEM + "\n\n" + pm.block(mvv)] * 2  # 再開も同じ 1 回分
    monkeypatch.setattr(worker_steps, "run_ticking", lambda *a, **k: SimpleNamespace(stdout='{"status": "ok"}'))
    ctx = work_ctx(env["root"], env["tmp"])
    step.execute(ctx, {"id": "w", "prompt": "直す", "runtime": "codex"})
    assert (ctx.state.dir / "w-prompt.md").read_text().startswith(WORK_SYSTEM + "\n\n" + pm.block(mvv) + "\n\n")


def test_worker_prompt_is_unchanged_without_an_approved_mvv(env):
    ctx = work_ctx(env["root"], env["tmp"], sprint_plan(env))  # スプリント MVV があっても足さない（決定 16）
    worker_steps.WorkStep().execute(ctx, {"id": "w", "prompt": "直す"})
    assert ctx.claude.calls[-1]["system"] == WORK_SYSTEM
    (env["root"] / ".ndf").mkdir(exist_ok=True)
    (env["root"] / ".ndf" / "mvv.md").write_text(BODY)  # 未承認
    ctx = work_ctx(env["root"], env["tmp"])
    worker_steps.WorkStep().execute(ctx, {"id": "w", "prompt": "直す"})
    assert ctx.claude.calls[-1]["system"] == WORK_SYSTEM


def test_worker_and_judge_read_the_sprint_mvv_of_the_state(env):
    assert approve(env) == 0
    mvv = pm.load_mvv(env["root"])
    plan = sprint_plan(env)
    ctx = work_ctx(env["root"], env["tmp"], plan)
    worker_steps.WorkStep().execute(ctx, {"id": "w", "prompt": "直す"})
    assert ctx.claude.calls[-1]["system"] == WORK_SYSTEM + "\n\n" + pm.block(mvv, SPRINT)
    ctx = work_ctx(env["root"], env["tmp"], plan, ['{"decision": "stop", "reason": "x", "basis": ["スプリント Value 1"]}'])
    d = JudgeStep().execute(ctx, {"id": "j", "question": "?", "choices": ["stop"]})
    assert ctx.claude.calls[-1]["prompt"].startswith(pm.block(mvv, SPRINT)) and d["basis"] == ["スプリント Value 1"]


def test_a_changed_sprint_mvv_falls_back_to_the_project_mvv_with_a_note(env):
    assert approve(env) == 0
    plan = sprint_plan(env)
    Path(json.loads(Path(plan["スプリント状態"]).read_text())["mvv"]["path"]).write_text("書き換えた\n")
    ctx = work_ctx(env["root"], env["tmp"], plan)
    worker_steps.WorkStep().execute(ctx, {"id": "w", "prompt": "直す"})
    assert ctx.claude.calls[-1]["system"] == WORK_SYSTEM + "\n\n" + pm.block(pm.load_mvv(env["root"]))
    lines = [json.loads(ln) for ln in ctx.state.progress.read_text().splitlines()]
    assert [ln["reason"] for ln in lines if ln["kind"] == "attention"] == ["スプリント MVV を使えない"]


# ---------------------------------------------------------------- normal のスプリントのプラン（AC4・AC5・I2・I7）


def new_normal_sprint(tmp_path: Path, *extra: str):
    out = tmp_path / "m"
    p = cli(
        "new",
        "sprint",
        *("--name", "m26", "--worktree", str(tmp_path), "--issue", "11", "--design", "11"),
        *("--version", "10.18.0-dev.1", "--out", str(out)),
        *extra,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    return load(out / "sprint.json"), json.loads(p.stdout.splitlines()[-1])


def test_normal_sprint_with_a_state_puts_the_advice_before_both_gates(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"name": "m", "pace": "normal", "gates": []}))
    manifest, out = new_normal_sprint(tmp_path, "--state", str(state))
    assert (manifest["進め方"], manifest["状態"], manifest["ブランチ"]) == ("normal", str(state.resolve()), "sprint/m26")
    waves = {w["name"]: w for w in manifest["ステージ"]}
    assert "--what <要約> --by user --pr <設計 PR>" in waves["関門 1"]["gate"] and "--by user" in waves["配布"]["note"]
    assert "sprint-state.py gate" in out["next"]
    design = load(waves["設計"]["plans"][0])
    ds = steps_of(design)
    assert [s["id"] for s in design["steps"]][-3:] == ["mvv", "mvv-note", "gate"]
    assert ds["push-glossary"]["next"] == "mvv" and ds["glossary-recheck"]["on_fail"] == "push-glossary"
    assert ds["mvv"]["cmd"].endswith("--advise") and "--gate design --pr {pr}" in ds["mvv"]["cmd"]
    assert (ds["mvv"]["on_fail"], ds["mvv-note"]["on_fail"], ds["mvv-note"]["next"]) == ("mvv-note", "gate", "gate")
    assert "mvv" in ds["gate"]["inputs"] and "gate_next" not in ds["mvv"]
    rel = steps_of(load(waves["配布"]["plans"][0]))
    assert rel["explain"]["next"] == "mvv" and rel["mvv"]["next"] == "mvv-note" and rel["mvv-note"]["next"] == "end"
    assert "--gate release" in rel["mvv"]["cmd"] and rel["mvv"]["cmd"].endswith("--advise") and "gate_as_ok" not in rel["facts"]
    for w in manifest["ステージ"]:
        for path in w.get("plans", []):
            plan = load(path)
            assert plan["スプリント状態"] == str(state.resolve())
            assert_transitions_exist(plan)
    assert sum(1 for w in manifest["ステージ"] for p in w.get("plans", []) for s in load(p)["steps"] if s["id"] == "mvv") == 2


def test_normal_sprint_without_a_state_is_unchanged(tmp_path):
    manifest, out = new_normal_sprint(tmp_path)
    assert "状態" not in manifest and "進め方" not in manifest
    for w in manifest["ステージ"]:
        for path in w.get("plans", []):
            plan = load(path)
            assert "スプリント状態" not in plan and not any(s["id"] in ("mvv", "mvv-note") for s in plan["steps"])
    assert "sprint-state.py" not in out["next"]


@pytest.mark.parametrize("code", [0, 1])
def test_the_design_plan_stops_at_gate_1_whatever_the_advice(tmp_path, monkeypatch, code):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"name": "m", "pace": "normal", "gates": []}))
    manifest, _ = new_normal_sprint(tmp_path, "--state", str(state))
    design = load(next(w for w in manifest["ステージ"] if w["name"] == "設計")["plans"][0])
    tail = design["steps"][-3:]
    tail[0]["cmd"], tail[1]["cmd"] = f"exit {code}", "true"
    fake = tmp_path / "judge.sh"
    fake.write_text(
        """#!/bin/sh\ncat > /dev/null\necho '{"result": "{\\"decision\\": \\"gate\\", \\"reason\\": \\"関門 1\\"}", "usage": {}}'\n"""
    )
    fake.chmod(0o755)
    monkeypatch.setenv("NDF_SUPERVISE_CLAUDE", str(fake))
    monkeypatch.setenv("NDF_USAGE_DIR", str(tmp_path / "usage"))
    monkeypatch.setenv("HOME", str(tmp_path))
    plan = {"フェーズ": "設計", "課題": [11], "作業場所": str(tmp_path), "steps": tail}
    engine.Engine(plan, tmp_path / "sv").run()
    assert "- 結果: 関門" in (tmp_path / "sv" / "report.md").read_text()


# ---------------------------------------------------------------- 助言のステップの現状固定（I-010・I-011）


def test_advise_design_steps_keeps_its_current_shape(tmp_path):
    from supervise_lib.sprint_waves import MVV_NOTE, MVV_PY, advise_design_steps

    state = tmp_path / "state.json"
    a = SimpleNamespace(state=str(state), mode="standard")
    note = MVV_NOTE
    assert advise_design_steps(a) == [
        {
            "id": "mvv",
            "type": "run",
            "stage": "設計",
            "timeout": 900,
            "cmd": f"rm -f {note} && {MVV_PY} check --sprint {state.resolve()} --gate design --pr {{pr}} --mode standard --root . "
            f"--note {note} --advise",
            "on_fail": "mvv-note",
            "next": "mvv-note",
        },
        {
            "id": "mvv-note",
            "type": "run",
            "stage": "設計",
            "timeout": 300,
            "cmd": f"sh -c '[ ! -f {note} ] || gh pr comment {{pr}} --body-file {note}'",
            "on_fail": "gate",
            "next": "gate",
        },
    ]
