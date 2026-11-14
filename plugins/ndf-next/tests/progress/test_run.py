"""工程を流す副命令（ndf.progress.run。#3092 の受け入れ条件 4・6・7・8、3364-7・3365-4・3366-1・3377-9、SF8）。

作業（プラン）は偽物（`Fake`）に差し替え、`next` が起こす作業は同じプロセスで `run.work` として流す。
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ndf import identity
from ndf.adapters.github import GitHubError
from ndf.contracts import causes, result
from ndf.core import durable
from ndf.progress import command, definition, invoke, model, run, store

ISSUE = 3092


@pytest.fixture
def repo(tmp_path, monkeypatch):
    path = tmp_path / "work"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    monkeypatch.chdir(path)
    yield path
    durable.release_store(_root(path))


def _root(cwd: Path) -> Path:
    return durable.store_root(identity.load().name, cwd)


def _main(capsys, fn, *argv: str) -> tuple[int, dict]:
    code = fn(list(argv))
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _new(capsys, mode: str = "light", *extra: str) -> str:
    code, out = _main(capsys, command.main, "new", "s1", "--mode", mode, "--issues", str(ISSUE), *extra)
    assert code == 0, out
    return out["metrics"]["table_id"]


@pytest.fixture
def st(repo):
    root = durable.prepare_store(_root(repo)).root  # 置き場を用意してから開く（`ndf progress` の副命令と同じ順）
    with store.open_store(store.default_url(root), root) as opened:
        yield opened


class Fake:
    """作業の偽物。`script[ステップ]` に積んだ結果を順に返し、無ければ宣言した出力を持つ ok を返す。"""

    def __init__(self, repo: Path) -> None:
        self.values = {"worktree": str(repo), "branch": "sprint/s1", "design_pr": 7, "pr": 8, "delivery": {"kind": "plugin"}}
        self.values["version"] = "11.0.0"
        self.calls: list[SimpleNamespace] = []
        self.script: dict[str, list] = {}

    def ok(self, r: invoke.StageRun) -> invoke.StageResult:
        outputs = {k: self.values[k] for k in r.outputs}
        report = result.result("plan", "ok", f"{r.step} を終えた", metrics={"version": "11.0.0"}, outputs=outputs or None)
        return invoke.finished(r, report)

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


def _turn(st, tid, repo, env) -> dict:
    """`next` を打ち、作業を起こしたらその作業を同じプロセスで流す。"""
    out = run.advance(st, tid, repo, env.env)
    if out["status"] == "ok" and "next" in out and out["next"]["argv"][2] == "wait":
        out = run.work(st, tid, repo, env.env)
    return out


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
    out = _turn(st, tid, repo, env)
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
    assert _turn(st, tid, repo, env)["metrics"]["stage"] == "設計"
    _approve(st, tid, "設計")
    gate = _turn(st, tid, repo, env)
    assert gate["status"] == "gate" and gate["metrics"]["stage"] == "デプロイ"
    _approve(st, tid, "デプロイ")
    done = _turn(st, tid, repo, env)
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
    _turn(st, tid, repo, env)
    before = len(env.fake.calls)
    _approve(st, tid, "設計")
    _turn(st, tid, repo, env)
    assert [c.step for c in env.fake.calls[before:]][:2] == ["design-merge", "implement"]


def test_rejected_gate_fails_the_row_and_returns_the_conductor_routes(repo, st, env, capsys):
    tid = _new(capsys)
    _turn(st, tid, repo, env)
    _approve(st, tid, "設計", "rejected")
    out = _turn(st, tid, repo, env)
    assert out["status"] == "stopped" and out["cause"]["kind"] == "refused"
    assert set(out["routes"]) == {"retry", "finish", "cancel"}
    assert out["routes"]["finish"]["ask"] == ["output", "reason"]
    assert result.code_for(out) == causes.exit_code("refused")


def test_revise_needs_a_notes_file_and_reruns_the_stage_with_the_notes(repo, st, env, capsys, tmp_path):
    tid = _new(capsys)
    _turn(st, tid, repo, env)
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
    out = _turn(st, tid, repo, env)
    assert out["status"] == "gate" and out["metrics"]["stage"] == "設計"
    rerun = env.fake.calls[before:]
    assert [c.step for c in rerun] == ["design", "design-review"] and {c.notes for c in rerun} == {"節 2 の根拠が足りない"}
    assert rerun[0].work_dir.name == "design-g1"


def test_answer_is_refused_where_nothing_waits(repo, st, env, capsys):
    tid = _new(capsys)
    with pytest.raises(model.Refused):
        _approve(st, tid, "設計")
    _turn(st, tid, repo, env)
    with pytest.raises(model.Refused):
        _approve(st, tid, "設計", "retry")


def test_plan_gate_waits_and_the_answer_is_passed_back_to_the_plan(repo, st, env, capsys):
    tid = _new(capsys)
    routes = {"approved": result.command("plan", "next", "x"), "rejected": None}
    env.fake.script["design-review"] = [lambda r: invoke.StageResult(result.result("plan", "gate", "判断を待つ", routes=routes), {})]
    out = _turn(st, tid, repo, env)
    assert out["status"] == "gate" and out["metrics"]["step"] == "design-review" and set(out["routes"]) == {"approved", "rejected"}
    _approve(st, tid, "設計")
    out = _turn(st, tid, repo, env)
    review = [c for c in env.fake.calls if c.step == "design-review"]
    assert [c.answer for c in review] == [None, "approved"]
    assert out["metrics"]["step"] == "design-gate"


def test_retryable_causes_are_retried_here_with_the_declared_backoff(repo, st, env, capsys):
    tid = _new(capsys)
    flaky = lambda r: invoke.StageResult(result.stopped("plan", "transient", "5xx"), {})  # noqa: E731
    env.fake.script["worktree"] = [flaky, flaky]
    out = _turn(st, tid, repo, env)
    assert out["status"] == "gate"
    assert env.slept == [2.0, 4.0]


def test_failures_that_need_a_decision_return_routes_and_the_exit_code_of_the_cause(repo, st, env, capsys):
    tid = _new(capsys)
    env.fake.script["worktree"] = [lambda r: invoke.StageResult(result.stopped("plan", "test_failed", "落ちた"), {})]
    out = _turn(st, tid, repo, env)
    assert out["cause"]["kind"] == "test_failed" and set(out["routes"]) == {"retry", "finish", "cancel"}
    assert result.code_for(out) == causes.exit_code("test_failed") != 6
    assert _labels(st, tid)["作業場所の用意"] == "failed"
    # 失敗の行は conductor が retry で戻すまで流さない。戻せば失敗したステップから流し直す
    assert run.advance(st, tid, repo, env.env)["cause"]["kind"] == "test_failed"
    _main(capsys, command.main, "retry", tid, "作業場所の用意", "--reason", "直した")
    assert _turn(st, tid, repo, env)["status"] == "gate"


def test_stage_output_that_breaks_the_contract_gets_no_finish_mark(repo, st, env, capsys):
    # 受け入れ条件 8（出力の照合）
    tid = _new(capsys)
    env.fake.script["worktree"] = [lambda r: invoke.finished(r, result.result("plan", "ok", "出力なし"))]
    out = _turn(st, tid, repo, env)
    assert out["cause"]["kind"] == "bad_report" and "worktree" in out["cause"]["detail"]
    assert _labels(st, tid)["作業場所の用意"] == "failed"


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
    recorded = [e for e in v.events if e.draft.kind == invoke.OUTPUTS_EVENT]
    assert [e.draft.actor for e in recorded] == ["conductor"]


def test_badly_shaped_outputs_are_not_passed_on(repo, st, env, capsys):
    # 3364-7: 次の工程の引数になる値は形を確かめてから渡す
    tid = _new(capsys)
    bad = {"worktree": str(repo), "branch": "--exec=x"}
    env.fake.script["worktree"] = [lambda r: invoke.finished(r, result.result("plan", "ok", "済んだ", outputs=bad))]
    out = _turn(st, tid, repo, env)
    assert out["cause"]["kind"] == "bad_report"
    assert "branch" not in run.read(st, tid).context()


def test_false_condition_disables_the_row_with_the_word(repo, st, env, capsys):
    tid = _new(capsys, "standard")
    env.env = run.Env(**{**vars(env.env), "facts": lambda words, ctx, cwd: {w: w != "code_in_diff" for w in words}})
    _turn(st, tid, repo, env)
    _approve(st, tid, "設計")
    _turn(st, tid, repo, env)
    row = run.read(st, tid).rows["リファクタリング"]
    assert row.state == "disabled" and row.reason == "条件 code_in_diff が偽"


def test_conditions_that_cannot_be_decided_stop_before_the_row(repo, st, env, capsys):
    tid = _new(capsys, "standard")
    env.env = run.Env(**{**vars(env.env), "facts": run.facts_of})
    _turn(st, tid, repo, env)
    _approve(st, tid, "設計")
    out = _turn(st, tid, repo, env)
    assert out["cause"]["kind"] == "precondition" and "code_in_diff" in out["cause"]["detail"]
    assert _labels(st, tid)["リファクタリング"] == "pending"


def test_facts_of_reads_the_declarations_and_the_context(repo):
    words = ("ci_declared", "design_pr", "prod_branch", "release_record")
    assert run.facts_of(words, {}, repo) == dict.fromkeys(words, False)
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "project.json").write_text(json.dumps({"test": {"ci": {"only": True}}}))
    (repo / ".ndf" / "worktree.json").write_text(json.dumps({"base_branch": "develop", "production_branch": "main"}))
    assert run.facts_of(words, {"design_pr": 3, "version": "11.0.0"}, repo) == dict.fromkeys(words, True)


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
    assert tuple(run.facts_of(words, ctx, repo)[w] for w in words) == kinds


def test_diff_words_follow_the_docs_declaration(repo):
    _sprint_branch(repo, "a.txt", "rules/b.txt")
    words = ("code_in_diff", "docs_only_diff", "behavior_docs_in_diff")
    ctx = {"worktree": str(repo), "branch": "sprint/s1"}
    assert run.facts_of(words, ctx, repo) == {"code_in_diff": True, "docs_only_diff": False, "behavior_docs_in_diff": False}
    docs = {"files": ["**/*.md", "**/*.txt"], "behavior": ["rules/*.txt"]}
    (repo / ".ndf" / "project.json").write_text(json.dumps({"docs": docs}))
    assert run.facts_of(words, ctx, repo) == {"code_in_diff": False, "docs_only_diff": False, "behavior_docs_in_diff": True}
    docs["files"] = ["**/*.txt", "!rules/**"]
    (repo / ".ndf" / "project.json").write_text(json.dumps({"docs": docs}))
    assert run.facts_of(words, ctx, repo)["code_in_diff"] is True


def test_diff_words_without_a_base_branch_cannot_be_decided(repo):
    with pytest.raises(run.FactUnknown, match="code_in_diff"):
        run.facts_of(("code_in_diff",), {}, repo)


def test_next_flows_the_refactor_row_from_the_sprint_diff(repo, st, env, capsys):
    _sprint_branch(repo, "README.md")
    tid = _new(capsys, "standard")
    env.env = run.Env(**{**vars(env.env), "facts": run.facts_of})
    _turn(st, tid, repo, env)
    _approve(st, tid, "設計")
    _turn(st, tid, repo, env)
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
    out = _turn(st, tid, repo, env)
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
    out = _turn(st, tid, repo, env)
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
    assert (run.work_root(_root(repo), tid) / "work.log").is_file()


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
