"""#2944: `claude -p` の起動へ選んだ値を当てる部品（`--effort`・拒否の判定・子の環境・効果指標の口）と判定の起動。

実際の子プロセスと待ちは使わない（`claude` の呼び出しは偽に差し替える）。拒否の判定は実際の出力の形の見本で縛る。
"""

from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path

import pytest

from ndf import identity
from ndf.adapters import claude, claude_choice, llm, model_choice, model_chooser, run_log

# 実測の形（2026-11-27、Claude Code 2.1.332 を `--model no-such-model-xyz` で起動した結果。終了コード 1）。判定に要らない欄の一部は省いた
REFUSED = {
    "type": "result",
    "subtype": "success",
    "is_error": True,
    "api_error_status": 404,
    "api_error_type": "not_found_error",
    "terminal_reason": "api_error",
    "duration_api_ms": 0,
    "num_turns": 1,
    "result": "There's an issue with the selected model (no-such-model-xyz). It may not exist or you may not have access to it.",
    "session_id": "00000000-0000-4000-8000-000000000000",  # 作り物の値（実測の値は残さない）
    "total_cost_usd": 0,
    "usage": {
        "input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
        "service_tier": "standard",
        "iterations": [],
    },
    "modelUsage": {},
}
EFFORT_ENV = identity.env_name("claude-effort-level")


def _parsed(payload: dict) -> claude.ClaudeReply:
    return claude._parse_reply(json.dumps(payload))


def test_the_measured_refusal_is_a_model_refusal_that_names_where_to_fix() -> None:
    got = _parsed(REFUSED)
    assert got.failure is claude.Failure.MODEL_REFUSED and "launch.json" in got.error


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p["usage"].pop("output_tokens"),  # 欄の欠けを 0 とみなさない
        lambda p: p["usage"].update(input_tokens=3),
        lambda p: p.pop("modelUsage"),
        lambda p: p.update(modelUsage={"claude-sonnet-5-5": {}}),
        lambda p: p.update(api_error_status=500),
    ],
)
def test_a_refusal_needs_all_four_conditions(change) -> None:
    payload = copy.deepcopy(REFUSED)
    change(payload)
    assert _parsed(payload).failure is None


def test_a_usage_limit_is_not_a_model_refusal() -> None:
    assert _parsed({**REFUSED, "api_error_status": 429}).failure is claude.Failure.USAGE_LIMIT


def test_effort_goes_to_the_arguments_only_when_chosen() -> None:
    call = claude.ClaudeCall("規則", claude.Permission.JUDGE, model="sonnet", effort="high")
    assert claude.claude_args(call)[-4:] == ["--model", "sonnet", "--effort", "high"]
    assert "--effort" not in claude.claude_args(claude.ClaudeCall("規則"))


def test_child_env_drops_names_given_as_none(monkeypatch) -> None:
    monkeypatch.setenv("NDF_TEST_DROP", "1")
    env = run_log.child_process_env({"NDF_TEST_DROP": None, "NDF_TEST_ABSENT": None, "NDF_TEST_SET": "v"})
    assert "NDF_TEST_DROP" not in env and "NDF_TEST_ABSENT" not in env and env["NDF_TEST_SET"] == "v"


def test_overlay_env_restores_the_environment_after_an_error(monkeypatch) -> None:
    monkeypatch.setenv("NDF_TEST_KEEP", "前")
    monkeypatch.delenv("NDF_TEST_NEW", raising=False)
    with pytest.raises(RuntimeError), run_log.overlay_env({"NDF_TEST_KEEP": None, "NDF_TEST_NEW": "後"}):
        assert "NDF_TEST_KEEP" not in os.environ and os.environ["NDF_TEST_NEW"] == "後"
        raise RuntimeError
    assert os.environ["NDF_TEST_KEEP"] == "前" and "NDF_TEST_NEW" not in os.environ


class Recorder:
    """差し込む偽の書き込み処理。使用量ログの行と効果指標を持つ。"""

    def __init__(self, fail: bool = False) -> None:
        self.rows: list[dict] = []
        self.outcomes: list[tuple] = []
        self.fail = fail

    def record_cli(self, usage, meta):
        self.rows.append(dict(meta))

    def record_outcome(self, assign_id, quality, detail):
        if self.fail:
            raise OSError("書けない")
        self.outcomes.append((assign_id, quality, dict(detail)))


class RowsOnly:
    def record_cli(self, usage, meta):
        return None


@pytest.mark.parametrize("recorder", [None, RowsOnly(), Recorder(fail=True)])
def test_report_outcome_warns_and_goes_on_without_a_receiver(monkeypatch, caplog, recorder) -> None:
    monkeypatch.setattr(claude, "launch_recorder", recorder)
    with caplog.at_level(logging.WARNING):
        claude_choice.report_outcome("a-1", 1.0, {})
    assert "a-1" in caplog.text


# --- 判定の起動 ----------------------------------------------------------------------------------------


@pytest.fixture
def judge_repo(tmp_path: Path, monkeypatch, launch_chooser) -> Path:
    """判定の起動の作業場所（設定なし）。値を選ぶ処理を差し込み、記録は一時の保存先に書き、抽選は最後の組（sonnet・medium）を引く。"""
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    monkeypatch.setenv("NDF_USAGE_DIR", str(tmp_path / "store"))
    monkeypatch.setattr(model_choice, "_random_value", lambda: 0.99)
    for key in ("launch-model", "launch-effort", "claude-effort-level"):
        monkeypatch.delenv(identity.env_name(key), raising=False)
    return root


@pytest.fixture
def recorder(monkeypatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(claude, "launch_recorder", rec)
    return rec


def _answer(answer, launched: bool = True, **kw) -> claude.ClaudeReply:
    models = {"claude-sonnet-5-5": {"inputTokens": 1, "outputTokens": 1}}
    return claude.ClaudeReply(True, answer, json.dumps(answer), 0.1, launched=launched, model_usage=models, seconds=1.0, **kw)


class Calls:
    def __init__(self, *replies: claude.ClaudeReply) -> None:
        self.replies, self.argv = list(replies), []

    def __call__(self, argv, prompt, timeout):
        self.argv.append(list(argv))
        return self.replies.pop(0)


def _value(argv: list[str], flag: str):
    return argv[argv.index(flag) + 1] if flag in argv else None


SCHEMA = llm.answer_schema(choices=["fix", "stop"])
GOOD = {"decision": "fix", "reason": "r", "basis": []}


def _experiments(store: Path, kind: str) -> list[dict]:
    rows = [r for p in sorted(store.glob("launch-experiments-*.jsonl")) for r in model_choice.launch_records.whole_rows(p)]
    return [r for r in rows if r["kind"] == kind]


def test_judge_launches_with_the_assigned_arm_and_writes_a_row_and_the_metric(judge_repo, recorder, tmp_path) -> None:
    calls = Calls(_answer(GOOD))
    got = llm.ask("質問", "材料", SCHEMA, 30, call=calls)
    (argv,) = calls.argv
    (row,) = recorder.rows
    assert got.ok and (_value(argv, "--model"), _value(argv, "--effort")) == ("sonnet", "medium")
    assert row["layer"] == "judge" and row["outcome"] == "valid" and row["request_try"] == 1 and row["model_source"] == "experiment"
    assert recorder.outcomes == [(row["assign_id"], 1.0, {"outcome": "valid", "attempts": 1})]
    assert [r["model"] for r in _experiments(tmp_path / "store", "observed")] == ["claude-sonnet-5-5"]


def test_the_second_judge_launch_is_raised_and_the_metric_stays_zero(judge_repo, recorder) -> None:
    calls = Calls(_answer({"decision": "?"}), _answer(GOOD))
    got = llm.ask("質問", "材料", SCHEMA, 30, call=calls)
    assert got.ok and [_value(a, "--effort") for a in calls.argv] == ["medium", "high"]
    assert [(r["outcome"], r["request_try"], r["raised"]) for r in recorder.rows] == [("invalid", 1, False), ("valid", 2, True)]
    assert recorder.outcomes[0][1] == 0.0


@pytest.mark.parametrize("failure,quality", [(claude.Failure.TIMEOUT, 0.0), (claude.Failure.LAUNCH, None)])
def test_a_failed_first_launch_gives_zero_after_a_timeout_and_no_value_otherwise(judge_repo, recorder, failure, quality) -> None:
    llm.ask("質問", "材料", SCHEMA, 30, call=Calls(claude.ClaudeReply.failed("", "落ちた", failure=failure)))
    assert recorder.outcomes[0][1] == quality


def test_a_refused_model_switches_to_the_base_once_without_counting_a_retry(judge_repo, recorder, tmp_path) -> None:
    refused = _parsed(REFUSED)._replace(launched=True, seconds=2.0)
    calls = Calls(refused, _answer(GOOD))
    got = llm.ask("質問", "材料", SCHEMA, 30, call=calls)
    assert got.ok and got.attempts == 1 and _value(calls.argv[1], "--model") is None
    assert [(r["request_try"], "assign_id" in r) for r in recorder.rows] == [(1, True), (2, False)]
    assert recorder.outcomes == []  # 切り替えた単位は数えない
    assert [(r["switched"], r["reason"]) for r in _experiments(tmp_path / "store", "launch_error")] == [(True, "model_refused")]


def test_the_switch_checks_the_material_before_launching_again(judge_repo, recorder) -> None:
    refused = _parsed(REFUSED)._replace(launched=True, seconds=2.0)
    checked: list[int] = []
    assert llm.ask("質問", "材料", SCHEMA, 30, call=Calls(refused, _answer(GOOD)), before_retry=lambda: checked.append(1)).ok
    assert checked == [1]


def _refused_only(store: Path) -> bool:
    """切り替えの行が無いか（切り替えないと決めた拒否は記録しない）。"""
    return _experiments(store, "launch_error") == []


def test_a_stale_material_stops_the_switch_without_a_switch_row(judge_repo, recorder, tmp_path) -> None:
    def moved() -> None:
        raise RuntimeError("材料が古い")

    calls = Calls(_parsed(REFUSED)._replace(launched=True, seconds=2.0))
    with pytest.raises(RuntimeError):
        llm.ask("質問", "材料", SCHEMA, 30, call=calls, before_retry=moved)
    assert len(calls.argv) == 1 and _refused_only(tmp_path / "store")


def test_a_refusal_with_no_time_left_does_not_switch_nor_check_the_material(judge_repo, recorder, tmp_path) -> None:
    refused = _parsed(REFUSED)._replace(launched=True, seconds=30.0)
    calls = Calls(refused)
    got = llm.ask("質問", "材料", SCHEMA, 30, call=calls, before_retry=lambda: pytest.fail("期限切れで材料を確かめた"))
    assert not got.ok and len(calls.argv) == 1
    assert _refused_only(tmp_path / "store") and recorder.outcomes[0][1] is None


def test_a_check_that_uses_up_the_time_stops_the_switch(judge_repo, recorder, tmp_path, monkeypatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(llm, "_clock", lambda: clock[0])
    calls = Calls(_parsed(REFUSED)._replace(launched=True, seconds=2.0), _answer(GOOD))
    got = llm.ask("質問", "材料", SCHEMA, 30, call=calls, before_retry=lambda: clock.__setitem__(0, 30.0))
    assert not got.ok and len(calls.argv) == 1 and _refused_only(tmp_path / "store")


def test_no_time_left_after_writing_the_switch_row_stops_the_base_launch(judge_repo, recorder, tmp_path, monkeypatch) -> None:
    """r7-4: 切り替えの記録を書く間に期限を使い切れば、基準の組を起動しない。"""
    clock = [0.0]
    monkeypatch.setattr(llm, "_clock", lambda: clock[0])
    real = model_choice.base_fallback

    def slow(choice, reason):
        clock[0] = 30.0  # 記録の読み取りと錠の待ちで期限を使い切った
        return real(choice, reason)

    monkeypatch.setattr(model_choice, "base_fallback", slow)
    monkeypatch.setattr(model_chooser, "base_fallback", slow)
    calls = Calls(_parsed(REFUSED)._replace(launched=True, seconds=2.0), _answer(GOOD))
    got = llm.ask("質問", "材料", SCHEMA, 30, call=calls)
    assert not got.ok and len(calls.argv) == 1


def test_the_switch_row_is_written_before_the_base_launch(judge_repo, recorder, tmp_path) -> None:
    """限界: 切り替えの行は基準の組の値を受け取るときに書かれ、基準の組の起動より前にある（B1b の base_fallback の形）。"""
    store = tmp_path / "store"
    seen: list[int] = []
    calls = Calls(_parsed(REFUSED)._replace(launched=True, seconds=2.0), _answer(GOOD))

    def launch(argv, prompt, timeout):
        seen.append(len(_experiments(store, "launch_error")))
        return calls(argv, prompt, timeout)

    assert llm.ask("質問", "材料", SCHEMA, 30, call=launch).ok and seen == [0, 1]


def test_the_answer_is_checked_once_per_launch(judge_repo, recorder, monkeypatch) -> None:
    checks: list[object] = []
    real = llm.check_answer
    monkeypatch.setattr(llm, "check_answer", lambda answer, schema: checks.append(answer) or real(answer, schema))
    assert llm.ask("質問", "材料", SCHEMA, 30, call=Calls(_answer({"decision": "?"}), _answer(GOOD))).ok
    assert len(checks) == 2


def test_a_broken_effort_stops_the_judge_before_any_launch(judge_repo, monkeypatch) -> None:
    monkeypatch.setenv(identity.env_name("launch-effort"), "huge")
    calls = Calls()
    got = llm.ask("質問", "材料", SCHEMA, 30, call=calls)
    assert not got.ok and got.cause["kind"] == "precondition" and calls.argv == []


def test_without_a_chooser_the_judge_launches_without_values(recorder) -> None:
    calls = Calls(_answer(GOOD))
    assert llm.ask("質問", "材料", SCHEMA, 30, call=calls).ok
    assert not {"--model", "--effort"} & set(calls.argv[0]) and "assign_id" not in recorder.rows[0]
