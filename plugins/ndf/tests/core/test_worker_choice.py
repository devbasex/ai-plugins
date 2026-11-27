"""#2944: worker の起動が選んだモデルと effort で動き、再開で同じ値を使い、拒否では基準の組へ 1 回だけ切り替える。

`claude` の起動は偽に差し替え、子プロセスと実際の待ちを使わない。値を選ぶ処理はエントリポイントと同じく差し込む（`launch_chooser`）。
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from ndf import identity
from ndf.adapters import claude, model_choice, model_chooser, run_log
from ndf.contracts import internal, worker_paths
from ndf.core import worker, worker_choice

EFFORT_ENV = identity.env_name("claude-effort-level")
CHARGE_ENV = identity.env_name("charge-to")
# 実測した拒否の結果の最小の欄（tests/adapters/test_claude_choice.py の見本と同じ形）
REFUSED = {"is_error": True, "api_error_status": 404, "result": "There's an issue with the selected model", "modelUsage": {}}
REFUSED["usage"] = dict.fromkeys(claude.REFUSED_TOKENS, 0)
MODELS = {"claude-sonnet-5-5": {"inputTokens": 1, "outputTokens": 1}}


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch, launch_chooser):
    monkeypatch.setattr(claude, "list_plugins", lambda **_: [])
    monkeypatch.setenv("NDF_USAGE_DIR", str(tmp_path / "store"))
    monkeypatch.setattr(model_choice, "_random_value", lambda: 0.99)  # 抽選は最後の組（sonnet・high）
    for key in ("launch-model", "launch-effort", "claude-effort-level", "charge-to", "unit-id", "retry-cause"):
        monkeypatch.delenv(identity.env_name(key), raising=False)


class Recorder:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record_cli(self, usage, meta):
        self.rows.append(dict(meta))


@pytest.fixture
def rows(monkeypatch) -> list[dict]:
    rec = Recorder()
    monkeypatch.setattr(claude, "launch_recorder", rec)
    return rec.rows


def _refused(seconds: float = 0.0) -> claude.ClaudeReply:
    return claude._parse_reply(json.dumps(REFUSED))._replace(launched=True, seconds=seconds)


class Fake:
    """回答を順に返し、渡された引数と子の環境の変数を残す。`write_on` 回目の起動で結果ファイルを書く。"""

    def __init__(self, h: worker.Handoff, replies: list[claude.ClaudeReply], write_on: int | None = None) -> None:
        self.h, self.replies, self.write_on = h, replies, write_on
        self.argv: list[list[str]] = []
        self.timeouts: list[float] = []
        self.effort_env: list[str | None] = []
        self.charge_env: list[str | None] = []

    def __call__(self, argv, prompt, timeout, cwd, options):
        self.argv.append(list(argv))
        self.timeouts.append(timeout)
        env = run_log.child_process_env()
        self.effort_env.append(env.get(EFFORT_ENV))
        self.charge_env.append(env.get(CHARGE_ENV))
        if len(self.argv) == self.write_on:
            self.h.result_file.write_text('{"status": "ok"}', encoding="utf-8")
        reply = self.replies.pop(0) if self.replies else claude.ClaudeReply(True, None, "", 0.0, launched=True, model_usage=MODELS)
        options.log.report(reply)
        return reply


def _handoff(tmp_path: Path, kind: str = "impl", **kw) -> worker.Handoff:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True, exist_ok=True)
    return worker.Handoff(kind, "作業", tmp_path / "result.json", root, tmp_path / "state", **kw)


def _delegate(h: worker.Handoff, fake: Fake, tmp_path: Path) -> worker.Delivery:
    with worker_paths.open_workdir(tmp_path) as wd:
        return worker.delegate(h, "規則", root=tmp_path, wd=wd, call=fake, session="s-1")


def _ledger() -> list[dict]:
    return model_choice.read_experiments(model_choice.utc_now() - timedelta(days=1))


def _value(argv: list[str], flag: str):
    return argv[argv.index(flag) + 1] if flag in argv else None


def test_a_unit_launches_with_its_arm_and_the_resume_keeps_the_values(tmp_path, rows) -> None:
    h = _handoff(tmp_path, unit="run-1:impl", mode="standard")
    fake = Fake(h, [], write_on=2)
    done = _delegate(h, fake, tmp_path)
    assert done.cause is None and [(_value(a, "--model"), _value(a, "--effort")) for a in fake.argv] == [("sonnet", "high")] * 2
    assert _value(fake.argv[1], "--resume") == "s-1"
    assert [r["outcome"] for r in rows] == ["no_result", "ok"] and {r["unit"] for r in rows} == {"run-1:impl"}
    assert {r["assign_id"] for r in rows} == {done.launch["assign_id"]} and rows[0]["mode"] == "standard"
    assert done.launch["model"] == "claude-sonnet-5-5" and done.launch["requested_model"] == "sonnet"


def test_the_first_real_model_is_observed_once_for_the_unit(tmp_path, rows) -> None:
    h = _handoff(tmp_path, unit="run-1:impl")
    _delegate(h, Fake(h, [], write_on=3), tmp_path)
    seen = [r for r in _ledger() if r["kind"] == "observed"]
    assert [(r["assign_id"], r["model"]) for r in seen] == [(rows[0]["assign_id"], "claude-sonnet-5-5")]


def test_without_a_unit_the_worker_is_not_assigned(tmp_path, rows) -> None:
    h = _handoff(tmp_path)
    fake = Fake(h, [], write_on=1)
    done = _delegate(h, fake, tmp_path)
    assert "--model" not in fake.argv[0] and "assign_id" not in rows[0] and "assign_id" not in done.launch
    assert done.launch["model_source"] == "default"


def test_the_inherited_effort_variable_is_kept_for_the_resume(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(EFFORT_ENV, "max")
    h = _handoff(tmp_path, unit="run-1:impl")
    fake = Fake(h, [], write_on=2)
    _delegate(h, fake, tmp_path)
    assert fake.effort_env == ["max", "max"] and not any("--effort" in a for a in fake.argv)


def test_a_manual_effort_removes_the_variable_from_the_child(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(EFFORT_ENV, "max")
    monkeypatch.setenv(identity.env_name("launch-effort"), "low")
    h = _handoff(tmp_path)
    fake = Fake(h, [], write_on=1)
    _delegate(h, fake, tmp_path)
    assert fake.effort_env == [None] and _value(fake.argv[0], "--effort") == "low"


def test_a_conversation_started_without_effort_does_not_pass_the_variable_to_the_resume(tmp_path, monkeypatch) -> None:
    """会話の最初の値を決めたときに変数が無ければ、親の環境に値があっても最初の起動と再開の子へ渡さない。"""
    picked = model_choice.launch_choice
    monkeypatch.setattr(model_chooser, "launch_choice", lambda *a, **kw: picked(*a, environ={}, **kw))
    monkeypatch.setenv(EFFORT_ENV, "max")
    h = _handoff(tmp_path)
    fake = Fake(h, [], write_on=2)
    _delegate(h, fake, tmp_path)
    assert fake.effort_env == [None, None]


def test_a_retry_cause_raises_the_value_and_the_row_keeps_it(tmp_path, rows) -> None:
    h = _handoff(tmp_path, "fix", unit="run-1:fix", retry="test_failed")
    fake = Fake(h, [], write_on=1)
    done = _delegate(h, fake, tmp_path)
    assert (_value(fake.argv[0], "--model"), _value(fake.argv[0], "--effort")) == ("opus", "high")
    assert rows[0]["raised"] is True and done.launch["raised"] is True


def test_a_refused_arm_switches_to_the_base_in_a_new_conversation_once(tmp_path, rows) -> None:
    h = _handoff(tmp_path, unit="run-1:impl", timeout=30)
    fake = Fake(h, [_refused(seconds=10.0)], write_on=2)
    done = _delegate(h, fake, tmp_path)
    assert done.cause is None and done.attempts == 2 and fake.timeouts == [30, 20]
    assert _value(fake.argv[1], "--model") is None and _value(fake.argv[1], "--session-id") not in (None, "s-1")
    assert done.session == _value(fake.argv[1], "--session-id")
    assert [(r["request_try"], r["outcome"]) for r in rows] == [(1, "unknown"), (2, "ok")]
    assert "assign_id" not in rows[1] and "assign_id" not in done.launch


def test_a_second_refusal_stops_with_the_place_to_fix(tmp_path) -> None:
    h = _handoff(tmp_path, unit="run-1:impl")
    done = _delegate(h, Fake(h, [_refused(), _refused()]), tmp_path)
    assert done.cause == "precondition" and "launch.json" in done.detail


def test_a_refused_base_stops_without_switching(tmp_path) -> None:
    h = _handoff(tmp_path)  # 単位なし: 基準の組で起動する
    fake = Fake(h, [_refused()])
    done = _delegate(h, fake, tmp_path)
    assert done.cause == "precondition" and len(fake.argv) == 1


def test_no_time_left_after_a_refusal_stops_as_interrupted_without_the_switch_row(tmp_path) -> None:
    h = _handoff(tmp_path, unit="run-1:impl", timeout=5)
    fake = Fake(h, [_refused(seconds=5.0)])
    done = _delegate(h, fake, tmp_path)
    assert done.cause == "interrupted" and len(fake.argv) == 1
    assert not any(r["kind"] == "launch_error" for r in _ledger())


def test_a_broken_effort_stops_before_any_launch(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(identity.env_name("launch-effort"), "huge")
    h = _handoff(tmp_path)
    fake = Fake(h, [])
    with pytest.raises(internal.InternalError) as got:
        _delegate(h, fake, tmp_path)
    assert got.value.kind == "precondition" and fake.argv == []


@pytest.mark.parametrize("kind,kept", [("fix", "a-9"), ("impl", None)])
def test_only_the_fix_work_keeps_the_charge_to_value(tmp_path, monkeypatch, rows, kind, kept) -> None:
    monkeypatch.setenv(CHARGE_ENV, "a-9")
    monkeypatch.setenv(identity.env_name("unit-id"), "run-1:x")
    monkeypatch.setenv(identity.env_name("retry-cause"), "test_failed")
    a = worker._parser().parse_args(["run", "--kind", kind, "--prompt-file", "p", "--result-file", "r", "--cwd", ".", "--state-dir", "s"])
    got = worker_choice.choice_inputs(a.kind, a.unit, a.mode)
    assert got == {"unit": "run-1:x", "mode": None, "retry": "test_failed", "charge_to": kept}
    h = _handoff(tmp_path, kind, charge_to=got["charge_to"])
    fake = Fake(h, [], write_on=1)
    _delegate(h, fake, tmp_path)
    assert fake.charge_env == [kept] and rows[0].get("charge_to") == kept


def test_the_unit_argument_wins_over_the_variable(monkeypatch) -> None:
    monkeypatch.setenv(identity.env_name("unit-id"), "from-env")
    assert worker_choice.choice_inputs("impl", "from-arg", "light")["unit"] == "from-arg"
    monkeypatch.setenv(identity.env_name("unit-id"), "")
    assert worker_choice.choice_inputs("impl", None, None)["unit"] is None


def test_run_reports_the_launch_values_in_its_metrics(tmp_path, monkeypatch, capsys) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("作業", encoding="utf-8")
    h = _handoff(tmp_path)
    seen: list[worker.Handoff] = []

    def fake_deliver(handoff, system, root, wd):
        seen.append(handoff)
        launch = {"layer": "worker", "name": "impl", "assign_id": "a-1", "model": "claude-sonnet-5-5"}
        return worker.Delivery(attempts=1, launch=launch), {"status": "ok", "summary": "済"}

    monkeypatch.setattr(worker, "_deliver", fake_deliver)
    argv = ["run", "--kind", "impl", "--prompt-file", str(prompt), "--result-file", str(h.result_file), "--cwd", str(h.cwd)]
    worker.main([*argv, "--state-dir", str(h.state_dir), "--unit", "u-1", "--mode", "light"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert (seen[0].unit, seen[0].mode) == ("u-1", "light")
    assert out["metrics"]["launch"] == {"layer": "worker", "name": "impl", "assign_id": "a-1", "model": "claude-sonnet-5-5"}
