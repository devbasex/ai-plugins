"""単位・引き上げの理由・修正の効果指標・モード（#2944 の詳細設計「プランの実行」）。

テンプレート `impl`・`check` の遷移は判断だけで回し、子プロセスも実時間の待ちも使わない。
"""

from __future__ import annotations

import fcntl
from pathlib import Path

import pytest

from ndf import identity
from ndf.adapters import claude_choice
from ndf.contracts.exit_codes import OK, STOPPED
from ndf.core import loop, route
from ndf.core.plan import files, model, records, runner, steps, transition_decide
from ndf.core.plan.transition_decide import SLEPT, Outcome, RunStep, Sleep, retry_reason

UNIT, CHARGE, RETRY, MODE = (identity.env_name(k) for k in ("unit-id", "charge-to", "retry-cause", "workflow-mode"))
DEFAULT_WORKER = ["ndf", "answer", "work", "--state-dir", "{state_dir}"]


@pytest.fixture(autouse=True)
def _chooser(launch_chooser):
    """一番上のエントリポイントと同じく、値を選ぶ処理を差し込む。"""


def _template(purpose: str) -> model.Plan:
    t = route.choose(purpose)
    defs = route.params()
    values = {n: ([f"<{n}>"] if defs[n].is_list else f"<{n}>") for n in t.params}
    return model.parse(route.render(t, {**values, "worker": DEFAULT_WORKER}))


def _runs(plan: model.Plan, script: dict) -> list[RunStep]:
    """ステップの結果を `script` の順に返し（尽きれば ok）、打った手を返す。"""
    queues = {k: list(v) for k, v in script.items()}
    acts: list[RunStep] = []

    def perform(act):
        if isinstance(act, Sleep):
            return Outcome(SLEPT)
        acts.append(act)
        queue = queues.get(act.step)
        return queue.pop(0) if queue else Outcome(OK)

    loop.run(lambda s, d: transition_decide.decide(plan, s, d), perform, transition_decide.start_state(plan, None))
    return acts


def _retries(acts: list[RunStep], step: str) -> list:
    return [a.retry for a in acts if a.step == step]


@pytest.mark.parametrize(
    ("work", "kind", "expected"),
    [
        ("fix", "test_failed", "test_failed"),
        ("ci-fix", "check_failed", "check_failed"),
        ("fix", "base_conflict", "base_conflict"),
        ("fix-push", None, "push_failed"),
        ("fix-base", "test_failed", "base_conflict"),
        ("fix", "precondition", None),
        ("ci-fix", None, None),
        ("impl", "test_failed", None),
        (None, "test_failed", None),
    ],
)
def test_retry_reason_names_the_cause_of_a_fix(work, kind, expected):
    assert retry_reason(work, kind) == expected


def test_the_work_name_is_the_last_word_of_the_default_worker():
    plan = _template("implement")
    assert (plan.step("work").work, plan.step("ci-fix").work, plan.step("push").work) == ("impl", "ci-fix", None)


def test_the_check_steps_name_the_fixes_they_verify():
    plan = _template("implement")
    fixes = ("fix", "ci-fix", "fix-base")
    assert [plan.step(s).verifies for s in ("test", "ci", "ci-local")] == [fixes] * 3
    assert [plan.step(s).verifies for s in ("push", "push-reviewed", "push-fixed")] == [("fix-push",)] * 3
    assert plan.step("work").verifies == ()


def test_the_second_entry_of_a_fix_through_the_test_counter_has_the_reason():
    failed = Outcome(STOPPED, "test_failed")
    acts = _runs(_template("implement"), {"test": [failed, failed, failed]})
    assert _retries(acts, "fix") == [None, "test_failed", "test_failed"]


def test_the_second_entry_of_a_ci_fix_through_the_check_counter_has_the_reason():
    failed = Outcome(STOPPED, "test_failed")
    acts = _runs(_template("check"), {"ci": [failed], "ci-local": [failed]})
    assert _retries(acts, "ci-fix") == [None, "test_failed"]


def test_the_second_entry_of_fix_push_has_the_reason():
    rejected = Outcome(STOPPED, "push_rejected")
    # 1 回目と打ち直しの 2 回とも拒まれると fix-push へ進む
    acts = _runs(_template("check"), {"push": [rejected] * 3})
    assert _retries(acts, "fix-push") == [None, "push_failed"]


def test_a_rerun_after_a_wait_keeps_the_reason_of_the_entry():
    failed = Outcome(STOPPED, "test_failed")
    limited = Outcome(STOPPED, "usage_limit", retry_after=1.0)
    acts = _runs(_template("implement"), {"test": [failed, failed], "fix": [Outcome(OK), limited]})
    assert _retries(acts, "fix") == [None, "test_failed", "test_failed"]


# --- ステップの子の環境 ---------------------------------------------------------------


def _step(step_id: str, work: str | None = None, **raw) -> model.Step:
    run = [*DEFAULT_WORKER, work] if work else ["true"]
    return model.parse({"name": "p", "steps": [{"id": step_id, "run": run, **raw}]}).steps[0]


def _spec(state_dir: Path, **values) -> dict:
    return {"run_id": "run-1", "state_dir": str(state_dir), "values": values}


def _env(state_dir: Path, step: model.Step, retry=None, **values) -> dict:
    return runner._step_env(_spec(state_dir, **values), step, RunStep(step.id, 0, 1, retry))


def _assigned(state_dir: Path, run: str = "run-1", **assigns) -> None:
    records.write_units(state_dir, {"run": run, "assigns": assigns})


@pytest.mark.parametrize(
    ("step_id", "work", "unit"),
    [
        ("design", "design-detail", "run-1:design"),
        ("work", "impl", "run-1:impl"),
        ("ci-fix", "ci-fix", "run-1:ci-fix"),
        ("work", "worktree", "run-1:work"),
        ("work", "release", None),
        ("push", None, None),
    ],
)
def test_step_env_names_the_unit(tmp_path, step_id, work, unit):
    assert _env(tmp_path, _step(step_id, work))[UNIT] == unit


def test_step_env_charges_the_rework_to_the_implementation(tmp_path):
    _assigned(tmp_path, design="d1", impl="i1")
    charged = [_env(tmp_path, _step("s", w))[CHARGE] for w in (None, "fix", "ci-fix", "fix-push", "fix-base", "impl")]
    assert charged == ["i1", "i1", "i1", None, None, None]


def test_step_env_without_an_implementation_charges_the_design(tmp_path):
    _assigned(tmp_path, design="d1")
    assert [_env(tmp_path, _step("s", w))[CHARGE] for w in (None, "fix")] == ["d1", None]


def test_step_env_ignores_the_assignments_of_another_run(tmp_path):
    _assigned(tmp_path, run="run-0", impl="i0")
    assert _env(tmp_path, _step("push"))[CHARGE] is None


def test_step_env_passes_the_retry_cause_and_the_mode_or_removes_them(tmp_path):
    step = _step("fix", "fix")
    given = _env(tmp_path, step, retry="test_failed", mode="standard")
    assert (given[RETRY], given[MODE], given["NDF_STEP_ID"]) == ("test_failed", "standard", "fix")
    none = _env(tmp_path, step)
    assert (none[RETRY], none[MODE]) == (None, None)


def test_values_of_none_remove_the_inherited_variables(tmp_path, monkeypatch):
    for name in (UNIT, CHARGE, RETRY, MODE):
        monkeypatch.setenv(name, "親の値")
    seen: dict = {}
    monkeypatch.setattr(steps.subprocess, "Popen", lambda args, **kw: seen.update(kw))
    plan = model.parse({"name": "p", "steps": [{"id": "s", "run": ["true"]}]})
    env = {UNIT: "u", CHARGE: None, RETRY: None, MODE: None}
    steps._launch(steps.StepContext(plan, tmp_path, {}, env), plan.steps[0])
    assert seen["env"][UNIT] == "u"
    assert not {CHARGE, RETRY, MODE} & set(seen["env"])


# --- 割り付け ID と効果指標 -----------------------------------------------------------


@pytest.fixture
def outcomes(monkeypatch) -> list:
    got: list = []
    monkeypatch.setattr(claude_choice, "report_outcome", lambda assign, quality, detail: got.append((assign, quality)))
    return got


def _after(state_dir: Path, step: model.Step, status: str = OK, kind=None, assign=None, nth: int = 1) -> None:
    out = {"status": status, "kind": kind, "run": "run-1", "nth": nth}
    if assign:
        out["launch"] = {"assign_id": assign}
    runner._after_unit(state_dir, step, out)


def _verify_step(sid: str = "test", *fixes: str) -> model.Step:
    return _step(sid, verifies=list(fixes or ("fix", "ci-fix", "fix-base")))


def test_the_assignments_of_the_run_are_kept_and_the_last_wins(tmp_path, outcomes):
    _after(tmp_path, _step("design", "design"), assign="d1")
    _after(tmp_path, _step("work", "impl"), assign="i1")
    _after(tmp_path, _step("work", "impl"), assign="i2")
    assert records.read_units(tmp_path)["assigns"] == {"design": "d1", "impl": "i2"}
    assert outcomes == []


@pytest.mark.parametrize(
    ("status", "kind", "quality"),
    [(OK, None, 1.0), (STOPPED, "test_failed", 0.0), (STOPPED, "check_failed", 0.0), (STOPPED, "base_conflict", 0.0)],
)
def test_the_first_check_after_a_fix_writes_its_outcome(tmp_path, outcomes, status, kind, quality):
    _after(tmp_path, _step("fix", "fix"), assign="f1")
    _after(tmp_path, _step("push"))  # 検査の前の push は指標に使わない
    assert outcomes == []
    _after(tmp_path, _verify_step(), status, kind)
    _after(tmp_path, _verify_step())
    assert outcomes == [("f1", quality)]


def test_a_check_that_could_not_judge_writes_nothing(tmp_path, outcomes):
    _after(tmp_path, _step("fix", "fix"), assign="f1")
    _after(tmp_path, _verify_step(), STOPPED, "precondition")
    assert outcomes == []


def test_a_rejected_push_judges_fix_push_only(tmp_path, outcomes):
    _after(tmp_path, _step("fix-push", "fix-push"), assign="p1")
    _after(tmp_path, _verify_step())  # fix-push を確かめない検査
    _after(tmp_path, _verify_step("push", "fix-push"), STOPPED, "push_rejected")
    assert outcomes == [("p1", 0.0)]


def test_a_stopped_fix_is_zero_and_only_its_first_run_counts(tmp_path, outcomes):
    _after(tmp_path, _step("fix", "fix"), STOPPED, "work_stopped", assign="f1")
    _after(tmp_path, _step("fix", "fix"), assign="f1", nth=2)
    _after(tmp_path, _verify_step())
    assert outcomes == [("f1", 0.0)]


def test_a_recovered_fix_leaves_one_pending_entry(tmp_path, outcomes):
    _after(tmp_path, _step("fix", "fix"), assign="f1")
    first = records.read_units(tmp_path)
    _after(tmp_path, _step("fix", "fix"), assign="f1")
    assert records.read_units(tmp_path) == first
    _after(tmp_path, _verify_step())
    assert outcomes == [("f1", 1.0)]


def test_preparing_the_worktree_is_judged_by_itself(tmp_path, outcomes):
    _after(tmp_path, _step("work", "worktree"), assign="m1")
    _after(tmp_path, _step("work", "worktree"), STOPPED, "work_stopped", assign="m2")
    assert outcomes == [("m1", 1.0), ("m2", 0.0)]


def test_a_pending_fix_of_another_run_is_not_judged(tmp_path, outcomes):
    records.write_units(tmp_path, {"run": "run-0", "pending": [{"step": "fix", "assign_id": "old", "work": "fix"}]})
    _after(tmp_path, _verify_step())
    assert outcomes == []


def test_a_record_that_cannot_be_written_only_warns(tmp_path, outcomes, caplog):
    blocked = tmp_path / "file"
    blocked.write_text("")
    _after(blocked, _step("work", "impl"), assign="i1")
    assert "割り付け" in caplog.text


def test_a_broken_units_file_reads_as_empty(tmp_path, caplog):
    (tmp_path / records.UNITS_FILE).write_text("{", encoding="utf-8")
    assert records.read_units(tmp_path) == {}
    assert records.UNITS_FILE in caplog.text


# --- 結果の metrics ---------------------------------------------------------------------


def test_outcome_of_picks_the_launch_of_the_worker():
    step = _step("work", "impl")
    line = '{"status": "ok", "summary": "s", "metrics": {"launch": {"assign_id": "a", "model": "m"}}}'
    assert steps.outcome_of(step, 0, line)["launch"] == {"assign_id": "a", "model": "m"}
    for broken in ('{"status": "ok", "metrics": {"launch": "x"}}', '{"status": "ok"}'):
        assert steps.outcome_of(step, 0, broken)["launch"] is None


def test_the_models_of_the_steps_are_kept_by_step(tmp_path):
    records._add_models(
        tmp_path, "run-1", "work", {"name": "impl", "requested_model": "sonnet", "model": "claude-sonnet", "assign_id": "a"}
    )
    records._add_models(tmp_path, "run-1", "fix", {"name": "fix", "effort": "high"})
    assert records.recorded_models(tmp_path, "run-1") == {
        "work": {"name": "impl", "requested_model": "sonnet", "model": "claude-sonnet"},
        "fix": {"name": "fix", "effort": "high"},
    }


def test_the_models_of_another_run_are_not_reported(tmp_path):
    records._add_models(tmp_path, "run-0", "work", {"name": "impl"})
    assert records.recorded_models(tmp_path, "run-1") == {}
    records._add_models(tmp_path, "run-1", "fix", {"name": "fix"})
    assert records.recorded_models(tmp_path, "run-1") == {"fix": {"name": "fix"}}


def _temporary(state_dir: Path, digit: str) -> Path:
    return state_dir / files.WRITING_DIR / f"{records.MODELS_FILE}.{digit * 32}.ndf-tmp"


def test_writing_a_record_keeps_the_files_it_did_not_make(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep", encoding="utf-8")
    # 一時ファイルと同じ形の名前でも、専用の領域の外のファイルは利用者のもの
    outside = tmp_path / f".{records.MODELS_FILE}.{'d' * 32}.tmp"
    outside.write_text("user", encoding="utf-8")
    _temporary(tmp_path, "a").parent.mkdir()
    _temporary(tmp_path, "a").symlink_to(victim)
    inside = _temporary(tmp_path, "a").parent / "notes.txt"  # 領域の中でも、名前の形が違うものは消さない
    inside.write_text("user", encoding="utf-8")
    records._add_models(tmp_path, "run-1", "work", {"name": "impl"})
    assert (victim.read_text(encoding="utf-8"), outside.read_text(encoding="utf-8")) == ("keep", "user")
    assert _temporary(tmp_path, "a").is_symlink()
    assert inside.read_text(encoding="utf-8") == "user"


def test_the_next_write_removes_only_a_left_temporary_file_nobody_writes(tmp_path):
    left, writing = _temporary(tmp_path, "b"), _temporary(tmp_path, "c")
    left.parent.mkdir()
    left.write_text("{", encoding="utf-8")  # 書き込みの途中で強制終了した残り
    with writing.open("x", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)  # 別の書き込みが書いている途中
        records._add_models(tmp_path, "run-1", "work", {"name": "impl"})
        assert (left.exists(), writing.exists()) == (False, True)
