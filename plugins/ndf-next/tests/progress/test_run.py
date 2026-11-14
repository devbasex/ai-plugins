"""工程を流す副命令（ndf.progress.run。#3092 の受け入れ条件 4・6・7・8、3364-7・3365-4・3366-1・3377-9、SF8）。

作業（プラン）は偽物（`Fake`）に差し替え、`next` が起こす作業は同じプロセスで `run.work` として流す。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ndf import contracts
from ndf.adapters.github import GitHubError
from ndf.contracts import causes, result
from ndf.core import durable
from ndf.progress import command, definition, invoke, model, run, stages, store

ISSUE = 3092


def _load_lanes():
    """同じ置き場のレーンの部品（lanes.py。偽の作業の結果と 1 手の打ち方）。"""
    spec = importlib.util.spec_from_file_location("lanes", Path(__file__).with_name("lanes.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lanes = _load_lanes()


def _main(capsys, fn, *argv: str) -> tuple[int, dict]:
    code = fn(list(argv))
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _new(capsys, mode: str = "light", *extra: str) -> str:
    code, out = _main(capsys, command.main, "new", "s1", "--mode", mode, "--issues", str(ISSUE), *extra)
    assert code == 0, out
    return out["metrics"]["table_id"]


@pytest.fixture
def st(repo):
    root = durable.prepare_store(command.store_root_of(repo)).root  # 置き場を用意してから開く（`ndf progress` の副命令と同じ順）
    with store.open_store(store.default_url(root), root) as opened:
        yield opened


class Fake:
    """作業の偽物。`script[ステップ]` に積んだ結果を順に返し、無ければ宣言した出力を持つ ok を返す。"""

    def __init__(self, repo: Path) -> None:
        self.values = lanes.values(repo, "sprint/s1")
        self.calls: list[SimpleNamespace] = []
        self.script: dict[str, list] = {}

    def ok(self, r: invoke.StageRun) -> invoke.StageResult:
        return lanes.ok_result(r, self.values)

    def __call__(self, r, cwd, work_dir, answer):
        self.calls.append(SimpleNamespace(step=r.step, answer=answer, notes=r.notes, inputs=r.inputs, work_dir=work_dir))
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
        records=lambda cwd: recs,
        sleep=slept.append,
    )
    return SimpleNamespace(env=e, fake=fake, records=recs, launched=launched, slept=slept)


def _labels(st, tid) -> dict[str, str]:
    v = run.read(st, tid)
    return {s.name: (v.rows[s.name].state if s.name in v.rows else None) for s in v.snap.stages}


def _approve(st, tid, stage, value="approved", notes=None):
    return run.answer(st, tid, stage, value, "人が答えた", notes)


def test_next_starts_the_first_pending_row_and_returns_wait(repo, st, env, capsys):
    tid = _new(capsys)
    out = run.advance(st, tid, repo, env.env)
    assert out["status"] == "ok" and out["next"]["argv"] == ["ndf", "progress", "wait", tid]
    assert env.launched == [tid]
    assert _labels(st, tid)["要求と受け入れ条件"] == "running"
    started = [e for e in run.read(st, tid).events if e.draft.kind == "started"][-1]
    assert started.draft.payload == {"inputs": {"issues": [ISSUE]}}


def test_work_flows_rows_until_the_design_gate_and_returns_routes(repo, st, env, capsys):
    tid = _new(capsys)
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate" and set(out["routes"]) == {"approved", "rejected", "revise"}
    assert out["routes"]["revise"]["ask"] == ["notes", "reason"]
    assert result.code_for(out) == 10
    labels = _labels(st, tid)
    assert labels["要求と受け入れ条件"] == labels["作業場所の用意"] == "finished" and labels["設計"] == "running"
    assert [c.step for c in env.fake.calls] == ["requirements", "worktree", "design", "design-review"]
    # 作業場所の用意の出力が文脈に入り、設計の入力に届く
    assert env.fake.calls[2].inputs == {"issues": [ISSUE], "worktree": str(repo)}


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
    assert body.startswith("本文\n\n## 進行\n\nモード: light\n")
    assert body.count("- [x] ") == 10 and "- [ ] " not in body
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
    assert [c.step for c in env.fake.calls[before:]][:2] == ["design-merge", "implement"]


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
        _approve(st, tid, "設計", "revise")
    empty = tmp_path / "empty.md"
    empty.write_text("  \n")
    with pytest.raises(model.Refused):
        _approve(st, tid, "設計", "revise", empty)
    notes = tmp_path / "notes.md"
    notes.write_text("節 2 の根拠が足りない")
    _approve(st, tid, "設計", "revise", notes)
    answered = [e for e in run.read(st, tid).events if e.draft.kind == "answered"][-1]
    assert answered.draft.payload["notes"] == "節 2 の根拠が足りない"
    before = len(env.fake.calls)
    out = lanes.turn(st, tid, repo, env.env)
    assert out["status"] == "gate" and out["metrics"]["stage"] == "設計"
    rerun = env.fake.calls[before:]
    assert [c.step for c in rerun] == ["design", "design-review"] and {c.notes for c in rerun} == {"節 2 の根拠が足りない"}
    assert rerun[0].work_dir.name == "design-g1"


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


def test_a_plan_that_returns_the_same_round_again_is_not_retried_further(repo, st, env, capsys):
    # PR 3464 の指摘: 打ち直しても控えの結果しか返らない回で、待ちと起動だけを繰り返さない
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


def test_stage_output_that_breaks_the_contract_gets_no_finish_mark(repo, st, env, capsys):
    # 受け入れ条件 8（出力の照合）
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
    # 終わった回の控えの結果を受け取らないよう、プランは前と違う置き場で流し直す
    first = [c for c in env.fake.calls[:before] if c.step == "worktree"][-1]
    assert first.work_dir.name == "worktree-g0" and again[0].work_dir.name == "worktree-g0-r1"
    assert _labels(st, tid)["作業場所の用意"] == "finished"


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
    run.advance(st, tid, repo, env.env)  # 行を始め直す（作業は起こすだけ）
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


def test_conditions_that_cannot_be_decided_stop_before_the_row(repo, st, env, capsys):
    tid = _new(capsys, "standard")
    env.env = run.Env(**{**vars(env.env), "facts": command.facts_of})
    lanes.turn(st, tid, repo, env.env)
    _approve(st, tid, "設計")
    out = lanes.turn(st, tid, repo, env.env)
    assert out["cause"]["kind"] == "precondition" and "code_in_diff" in out["cause"]["detail"]
    assert _labels(st, tid)["リファクタリング"] == "pending"


def test_facts_of_reads_the_declarations_and_the_context(repo):
    words = ("ci_declared", "design_pr", "prod_branch", "release_record")
    assert command.facts_of(words, {}, repo) == dict.fromkeys(words, False)
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "project.json").write_text(json.dumps({"test": {"ci": {"only": True}}}))
    (repo / ".ndf" / "worktree.json").write_text(json.dumps({"base_branch": "develop", "production_branch": "main"}))
    assert command.facts_of(words, {"design_pr": 3, "version": "11.0.0"}, repo) == dict.fromkeys(words, True)


def test_facts_of_reads_a_declaration_that_is_not_an_object_as_empty_and_refuses_broken_json(repo):
    # 現状固定（I-018）: 宣言の読み方は command._declared と同じ（オブジェクトでなければ空、読めなければ ContractError）
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "project.json").write_text("[1]")
    assert command.facts_of(("ci_declared",), {}, repo) == {"ci_declared": False}
    (repo / ".ndf" / "project.json").write_text("{")
    with pytest.raises(contracts.ContractError, match="を読めない"):
        command.facts_of(("ci_declared",), {}, repo)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=t", *args], check=True, capture_output=True)


def _sprint_branch(repo: Path, *changed: str) -> None:
    """ベースブランチ develop から sprint/s1 を切り、`changed` のファイルを足すコミットを積む。"""
    _git(repo, "checkout", "-q", "-b", "develop")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "sprint/s1")
    for name in changed:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("x\n")
    if changed:
        _git(repo, "add", "--", *changed)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "change")
    (repo / ".ndf").mkdir(exist_ok=True)
    (repo / ".ndf" / "worktree.json").write_text(json.dumps({"base_branch": "develop"}))


@pytest.mark.parametrize(
    ("changed", "kinds"),
    [
        (["src/a.py", "README.md"], (True, False, False)),
        (["docs/a.md"], (False, True, False)),
        (["plugins/x/skills/y/SKILL.md", "docs/a.md"], (False, False, True)),
        ([], (False, False, False)),
    ],
)
def test_diff_words_split_the_sprint_diff_into_code_and_docs(repo, changed, kinds):
    _sprint_branch(repo, *changed)
    words = ("code_in_diff", "docs_only_diff", "behavior_docs_in_diff")
    ctx = {"worktree": str(repo), "branch": "sprint/s1"}
    assert tuple(command.facts_of(words, ctx, repo)[w] for w in words) == kinds


def test_diff_words_follow_the_docs_declaration(repo):
    _sprint_branch(repo, "a.txt", "rules/b.txt")
    words = ("code_in_diff", "docs_only_diff", "behavior_docs_in_diff")
    ctx = {"worktree": str(repo), "branch": "sprint/s1"}
    assert command.facts_of(words, ctx, repo) == {"code_in_diff": True, "docs_only_diff": False, "behavior_docs_in_diff": False}
    docs = {"files": ["**/*.md", "**/*.txt"], "behavior": ["rules/*.txt"]}
    (repo / ".ndf" / "project.json").write_text(json.dumps({"docs": docs}))
    assert command.facts_of(words, ctx, repo) == {"code_in_diff": False, "docs_only_diff": False, "behavior_docs_in_diff": True}
    docs["files"] = ["**/*.txt", "!rules/**"]
    (repo / ".ndf" / "project.json").write_text(json.dumps({"docs": docs}))
    assert command.facts_of(words, ctx, repo)["code_in_diff"] is True


def test_diff_words_without_a_base_branch_cannot_be_decided(repo):
    with pytest.raises(command.FactUnknown, match="code_in_diff"):
        command.facts_of(("code_in_diff",), {}, repo)
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "worktree.json").write_text(json.dumps({"base_branch": "--output=x"}))
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


def test_missing_input_does_not_start_the_row(repo, st, env, capsys):
    tid = _new(capsys, "light", "--disable", "作業場所の用意=手で用意した")
    out = lanes.turn(st, tid, repo, env.env)
    # 設計の入力 worktree を書く工程が無効なので、設計は始まらない
    assert out["cause"]["kind"] == "input_missing" and out["items"] == [{"key": "worktree"}]
    assert out["metrics"]["stage"] == "設計" and _labels(st, tid)["設計"] == "pending"


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
    once = run.replace_section(body, run.ISSUE_SECTION, "新しい")
    assert once == "前\n\n## 進行\n\n新しい\n<!-- ndf:section-end -->\n\n## 後\n"
    assert run.replace_section(once, run.ISSUE_SECTION, "新しい") == once
    assert run.replace_section("", run.PR_SECTION, "x") == "## 通した工程\n\nx\n<!-- ndf:section-end -->\n"


def test_retry_delay_follows_the_cause_table():
    assert run.retry_delay(causes.cause("transient", "x"), 0) == 2.0
    assert run.retry_delay(causes.cause("transient", "x"), 5) is None
    assert run.retry_delay(causes.cause("test_failed", "x"), 0) is None
    assert run.retry_delay(causes.cause("usage_limit", "x"), 0) is None
    assert run.retry_delay(causes.cause("usage_limit", "x", retry_after=30), 0) == 30.0
    assert run.retry_delay(None, 0) is None


def test_cli_next_launches_the_work_in_another_process(repo, capsys):
    tid = _new(capsys)
    code, out = _main(capsys, run.main, "next", tid)
    assert code == 0 and out["next"]["argv"][2] == "wait"
    # 起こした作業が排他を取り、要求と受け入れ条件の行へ結果を書くまで待つ（作業の起動の前に wait が戻りうる）
    for _ in range(120):
        _, waited = _main(capsys, run.main, "wait", tid, "--timeout", "60")
        _, shown = _main(capsys, run.main, "show", tid)
        kinds = [h["kind"] for h in shown["metrics"]["history"]]
        if kinds[kinds.index("started") + 1 :]:
            break
        time.sleep(0.5)
    assert kinds[kinds.index("started") + 1 :]
    assert waited.get("cause", {}).get("kind") != "wait_timeout"
    assert (run.work_root(command.store_root_of(repo), tid) / "work.log").is_file()


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
    assert len(seen) == 1 and seen[0].table_id == tid and seen[0].rev == before.rev
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


def test_append_rebuilds_after_a_conflict_and_raises_when_the_attempts_run_out(repo, st, capsys, monkeypatch):
    tid = _new(capsys)
    real, calls = store.append_events, {"n": 0}

    def conflict_once(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise store.Conflict("ぶつかった")
        return real(*a, **k)

    builds: list[run.View] = []
    monkeypatch.setattr(store, "append_events", conflict_once)
    seqs = run.append(st, tid, lambda v: builds.append(v) or _started(v))
    assert len(builds) == 2 and calls["n"] == 2 and run.read(st, tid).events[-1].seq == seqs[0]

    def always(*a, **k):
        calls["n"] += 1
        raise store.Conflict("ぶつかった")

    calls["n"], builds[:] = 0, []
    monkeypatch.setattr(store, "append_events", always)
    with pytest.raises(store.Conflict):
        run.append(st, tid, lambda v: builds.append(v) or _started(v))
    assert calls["n"] == run.APPEND_ATTEMPTS and len(builds) == run.APPEND_ATTEMPTS


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


# --- 現状固定: run.wait（I-026。作業が動く間だけ宣言の間隔で待ち、上限で同じ wait を返す） -------------


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
        run.TOOL, "wait_timeout", f"進行表 {tid} を流す作業がまだ動いている（0 秒待った）", next=result.command(run.TOOL, "wait", tid)
    )
    assert later["cause"] == {"kind": "wait_timeout", "detail": f"進行表 {tid} を流す作業がまだ動いている（0.05 秒待った）"}
    assert later["next"] == result.command(run.TOOL, "wait", tid) and len(slept) >= 1


def _session_leader() -> subprocess.Popen:
    """起こした作業の代わり（別のプロセスの組の頭。`launch_worker` と同じ起こし方）。"""
    return subprocess.Popen(["sleep", "60"], start_new_session=True)


def test_work_launched_but_not_yet_holding_the_lock_counts_as_live(repo, st, env, capsys):
    # PR 3464 の指摘: next が排他を放してから作業が排他を取るまでの間に、wait・next が 2 本目を起こさない
    tid = _new(capsys)
    proc = _session_leader()
    try:
        env.env = run.Env(**{**vars(env.env), "launch": lambda st_, t, cwd: proc.pid})
        assert run.advance(st, tid, repo, env.env)["next"]["argv"][2] == "wait"
        assert run.launch_path(st.root, tid).is_file() and run.is_live(st, tid)
        assert run.wait(st, tid, 0, sleep=lambda s: None)["cause"]["kind"] == "wait_timeout"
        again = run.advance(st, tid, repo, env.env)
        assert again["next"]["argv"][2] == "wait" and "動いている" in again["summary"]
        assert len([e for e in run.read(st, tid).events if e.draft.kind == "started"]) == 1
        assert run.hand(st, run.read(st, tid))["argv"][2] == "wait"
        run.work(st, tid, repo, env.env)  # 作業が排他を取れば印は消える
        assert not run.launch_path(st.root, tid).exists() and not run.is_live(st, tid)
    finally:
        proc.kill()
        proc.wait()


def test_a_launch_mark_of_work_that_is_gone_is_not_live(repo, st, capsys):
    tid = _new(capsys)
    proc = _session_leader()
    proc.kill()
    proc.wait()
    mark = run.launch_path(st.root, tid)
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.write_text(json.dumps({"pid": proc.pid}))
    assert not run.is_live(st, tid)
    mark.write_text("{")  # 書きかけの印も起こした作業として数えない
    assert not run.is_live(st, tid)


def test_wait_on_a_missing_table_is_refused(repo, st):
    with pytest.raises(model.Refused, match="無い"):
        run.wait(st, "no-such-table", 0, sleep=lambda s: None)
