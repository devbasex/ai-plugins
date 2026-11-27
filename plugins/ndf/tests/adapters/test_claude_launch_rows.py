"""#1959 の決定 6: `claude -p` の起動 1 回の結果の欄と、使用量ログへ渡す行（`report_launch`）。"""

from __future__ import annotations

import json
import os
import sys

import pytest

from ndf.adapters import claude

# 実測の形（2026-11-27、Claude Code 2.1.332 の haiku の結果から値だけを残した）。`usage` は起動 1 回分、`modelUsage` と `total_cost_usd` は会話の累計
MODEL_USAGE = {
    "claude-haiku-5-5": {
        "inputTokens": 4,
        "outputTokens": 22,
        "cacheReadInputTokens": 6145,
        "cacheCreationInputTokens": 3679,
        "webSearchRequests": 0,
        "costUSD": 0.00080865,
        "contextWindow": 1000000,
    }
}
USAGE = {"input_tokens": 2, "output_tokens": 11, "cache_read_input_tokens": 4475, "cache_creation_input_tokens": 466}
PAYLOAD = {
    "type": "result",
    "is_error": False,
    "result": "{}",
    "session_id": "s-9",
    "total_cost_usd": 0.00080865,
    "usage": USAGE,
    "modelUsage": MODEL_USAGE,
}


class Recorder:
    """差し込む偽の書き込み処理。渡された使用量と欄を順に持つ。"""

    def __init__(self) -> None:
        self.rows: list[tuple[object, dict]] = []

    def record_cli(self, usage, meta):
        self.rows.append((usage, dict(meta)))


@pytest.fixture
def recorder(monkeypatch) -> Recorder:
    got = Recorder()
    monkeypatch.setattr(claude, "launch_recorder", got)
    return got


def _launched(**kw) -> claude.ClaudeReply:
    base = claude._parse_reply(json.dumps(PAYLOAD))
    return base._replace(launched=True, launched_at="2026-11-27T01:00:00+00:00", seconds=3.5, **kw)


@pytest.mark.parametrize("error", [False, True])
def test_the_reply_keeps_the_session_usage_and_cost_on_success_and_on_error(error):
    got = claude._parse_reply(json.dumps({**PAYLOAD, "is_error": error}))
    assert got.ok is not error
    assert (got.session_id, got.usage, got.model_usage, got.session_cost_usd) == ("s-9", USAGE, MODEL_USAGE, 0.00080865)


def test_the_reply_without_the_fields_or_json_has_none():
    bare = claude._parse_reply(json.dumps({"result": "{}"}))
    assert (bare.session_id, bare.usage, bare.model_usage, bare.session_cost_usd) == (None, None, None, None)
    broken = claude._parse_reply("JSON でない")
    assert (broken.ok, broken.usage, broken.launched) == (False, None, False)


def test_main_model_is_the_largest_sum_and_the_smaller_window_on_a_tie():
    small = {"inputTokens": 1, "outputTokens": 1, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0, "contextWindow": 200000}
    big = {**small, "cacheReadInputTokens": 50, "contextWindow": 1000000}
    assert claude.main_model({"a": small, "b": big}) == ("b", big)
    assert claude.main_model({"a": {**small, "contextWindow": 1000000}, "b": small}) == ("b", small)
    assert claude.main_model({}) is None
    assert claude.main_model({"a": "形が違う"}) is None


def test_one_launched_child_hands_one_row(recorder):
    meta = {"name": "fix", "repo": "o/r", "session_id": "s-1"}
    assert claude.report_launch(_launched(), meta, 2) == 1
    [(usage, row)] = recorder.rows
    assert usage == USAGE
    assert row["name"] == "fix" and row["repo"] == "o/r" and row["session_id"] == "s-1" and row["session_seq"] == 2
    assert row["session_cost_usd"] == 0.00080865 and row["model"] == "claude-haiku-5-5"
    tokens = {"inputTokens": 4, "outputTokens": 22, "cacheReadInputTokens": 6145, "cacheCreationInputTokens": 3679}
    assert row["model_usage"] == {"claude-haiku-5-5": tokens}  # 会話の累計のまま、4 つのトークンの値だけ
    assert row["seconds"] == 3.5 and row["launched_at"] == "2026-11-27T01:00:00+00:00" and row["launch_id"]


def test_the_session_id_of_the_result_is_written_when_the_caller_did_not_name_it(recorder):
    claude.report_launch(_launched(), {"name": "review"}, 0)
    assert recorder.rows[0][1]["session_id"] == "s-9"


def test_a_retried_child_hands_two_rows_with_their_own_launch_ids(recorder):
    first = _launched(session_cost_usd=0.1)
    second = _launched(earlier=(first,))
    assert claude.report_launch(second, {"name": "fix"}, 0) == 2
    rows = [row for _, row in recorder.rows]
    assert [r["session_seq"] for r in rows] == [0, 1] and [r["session_cost_usd"] for r in rows] == [0.1, 0.00080865]
    assert rows[0]["launch_id"] != rows[1]["launch_id"]


def test_a_child_that_did_not_launch_hands_no_row(recorder):
    stopped = claude.ClaudeReply.failed("", "古い", failure=claude.Failure.REFUSED)
    assert claude.report_launch(stopped, {"name": "fix"}, 0) == 0
    assert claude.report_launch(_launched()._replace(earlier=(stopped,)), {"name": "fix"}, 0) == 1
    assert len(recorder.rows) == 1 and recorder.rows[0][1]["session_seq"] == 0


def test_a_launched_child_without_json_hands_a_row_without_usage(recorder):
    timed = claude.ClaudeReply.failed("", "期限", failure=claude.Failure.TIMEOUT)._replace(launched=True, seconds=1.0)
    assert claude.report_launch(timed, {"name": "fix", "session_id": "s-1"}, 0) == 1
    usage, row = recorder.rows[0]
    assert usage is None and "model" not in row and row["session_cost_usd"] is None


def test_without_a_recorder_nothing_is_written(monkeypatch):
    monkeypatch.setattr(claude, "launch_recorder", None)
    assert claude.report_launch(_launched(), {"name": "fix"}, 0) == 1


def test_set_launch_recorder_replaces_the_recorder(monkeypatch):
    monkeypatch.setattr(claude, "launch_recorder", None)
    rec = Recorder()
    claude.set_launch_recorder(rec)
    assert claude.launch_recorder is rec


def test_launch_meta_reads_the_plan_values_from_the_environment(monkeypatch):
    monkeypatch.setenv("NDF_PLAN_RUN_ID", "run-1")
    monkeypatch.setenv("NDF_STEP_ID", "implement")
    monkeypatch.setenv("NDF_STEP_ATTEMPT", "2")
    assert claude.launch_meta("fix", "o/r") == {"name": "fix", "repo": "o/r", "run_id": "run-1", "step": "implement", "attempt": 2}


def test_launch_meta_outside_a_plan_has_only_the_name(monkeypatch):
    for name in ("NDF_PLAN_RUN_ID", "NDF_STEP_ID", "NDF_STEP_ATTEMPT"):
        monkeypatch.delenv(name, raising=False)
    assert claude.launch_meta("fix", None) == {"name": "fix"}
    monkeypatch.setenv("NDF_STEP_ATTEMPT", "二")
    assert "attempt" not in claude.launch_meta("fix", None)


ANSWER = "import json, sys; sys.stdin.read(); print(json.dumps({'result': '{}', 'session_id': 's-2', 'usage': {'output_tokens': 1}}))"


def test_a_launched_child_is_marked_with_its_time(tmp_path):
    got = claude.call_claude([sys.executable, "-c", ANSWER], "q", 30, options=claude.LaunchOptions(cli_floor=False))
    assert got.ok and got.launched and got.session_id == "s-2"
    assert got.seconds is not None and got.seconds >= 0 and got.launched_at is not None and got.launched_at.endswith("+00:00")


def test_a_command_that_cannot_start_is_not_marked_launched(tmp_path):
    got = claude.call_claude([str(tmp_path / "missing")], "q", 30, options=claude.LaunchOptions(cli_floor=False))
    assert got.failure is claude.Failure.LAUNCH and not got.launched


class FakeChecks:
    def __init__(self, got, again):
        self.got, self.again = got, again

    def begin(self, argv, env, cwd, keys):
        return self.got

    def retry(self, argv, env, cwd, keys):
        return self.again


def test_the_child_before_an_account_switch_is_kept_as_earlier(tmp_path, monkeypatch):
    run = claude.AccountRun(dict(os.environ), ["x"])
    limited = _launched(ok=False, failure=claude.Failure.USAGE_LIMIT)
    replies = iter([limited, _launched()])
    monkeypatch.setattr(claude, "_prefaced", lambda *a: next(replies))
    got = claude._accounted(FakeChecks(run, run), run, "q", 30, tmp_path, claude.DEFAULT_LAUNCH)
    assert got.ok and got.earlier == (limited,)


def test_the_entry_point_injects_the_usage_log(monkeypatch):
    import runpy

    from ndf import cli
    from ndf.domains.records import usage

    monkeypatch.setattr(claude, "launch_recorder", None)
    monkeypatch.setattr(claude, "relay_checks", None)
    monkeypatch.setattr(cli, "main", lambda: 0)
    monkeypatch.setattr(sys, "exit", lambda code: None)

    runpy.run_module("ndf", run_name="__main__")

    assert claude.launch_recorder is usage
