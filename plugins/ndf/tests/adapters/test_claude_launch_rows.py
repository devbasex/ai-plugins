"""#1959 の決定 6: `claude -p` の起動 1 回の結果の欄と、使用量ログへ渡す行（`LaunchLog`）。

実プロセスの起動と実時間は使わない（偽の `Popen` と差し替えた時計で縛る）。
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager

import pytest

from ndf.adapters import claude, launch_slot

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
    base = claude.parse_reply(json.dumps(PAYLOAD))
    return base._replace(launched=True, launched_at="2026-11-27T01:00:00+00:00", seconds=3.5, **kw)


@pytest.mark.parametrize("error", [False, True])
def test_the_reply_keeps_the_session_usage_and_cost_on_success_and_on_error(error):
    got = claude.parse_reply(json.dumps({**PAYLOAD, "is_error": error}))
    assert got.ok is not error
    assert (got.session_id, got.usage, got.model_usage, got.session_cost_usd) == ("s-9", USAGE, MODEL_USAGE, 0.00080865)


def test_the_reply_without_the_fields_or_json_has_none():
    bare = claude.parse_reply(json.dumps({"result": "{}"}))
    assert (bare.session_id, bare.usage, bare.model_usage, bare.session_cost_usd) == (None, None, None, None)
    broken = claude.parse_reply("JSON でない")
    assert (broken.ok, broken.usage, broken.launched) == (False, None, False)


def test_main_model_is_the_largest_sum_and_the_smaller_window_on_a_tie():
    small = {"inputTokens": 1, "outputTokens": 1, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0, "contextWindow": 200000}
    big = {**small, "cacheReadInputTokens": 50, "contextWindow": 1000000}
    assert claude.main_model({"a": small, "b": big}) == ("b", big)
    assert claude.main_model({"a": {**small, "contextWindow": 1000000}, "b": small}) == ("b", small)
    assert claude.main_model({}) is None
    assert claude.main_model({"a": "形が違う"}) is None


def test_one_launched_child_hands_one_row(recorder):
    log = claude.LaunchLog({"name": "fix", "repo": "o/r", "session_id": "s-1"})
    log.report(_launched(session_id=None))
    [(usage, row)] = recorder.rows
    assert usage == USAGE
    assert row["name"] == "fix" and row["repo"] == "o/r" and row["session_id"] == "s-1" and row["session_seq"] == 0
    assert row["session_cost_usd"] == 0.00080865 and row["model"] == "claude-haiku-5-5"
    tokens = {"inputTokens": 4, "outputTokens": 22, "cacheReadInputTokens": 6145, "cacheCreationInputTokens": 3679}
    assert row["model_usage"] == {"claude-haiku-5-5": tokens}  # 会話の累計のまま、4 つのトークンの値だけ
    assert row["seconds"] == 3.5 and row["launched_at"] == "2026-11-27T01:00:00+00:00" and row["launch_id"]


def test_the_session_id_of_the_result_is_written_when_the_caller_did_not_name_it(recorder):
    claude.LaunchLog({"name": "review"}).report(_launched())
    assert recorder.rows[0][1]["session_id"] == "s-9"


def test_children_of_one_conversation_count_up_with_their_own_launch_ids(recorder):
    log = claude.LaunchLog({"name": "fix", "session_id": "s-9"})
    log.report(_launched(session_cost_usd=0.1))
    log.report(_launched())
    rows = [row for _, row in recorder.rows]
    assert [r["session_seq"] for r in rows] == [0, 1] and [r["session_cost_usd"] for r in rows] == [0.1, 0.00080865]
    assert rows[0]["launch_id"] != rows[1]["launch_id"]


def test_a_child_in_a_new_conversation_starts_its_number_from_zero(recorder):
    """アカウントを替えて打ち直した子は結果の会話の id が変わるので、その会話の番号は 0 から（費用が不明にならない）。"""
    log = claude.LaunchLog({"name": "design"})
    log.report(_launched(session_id="s-a"))
    log.report(_launched(session_id="s-b"))
    assert [(row["session_id"], row["session_seq"]) for _, row in recorder.rows] == [("s-a", 0), ("s-b", 0)]


def test_a_retry_on_another_account_with_the_same_session_continues_the_number(tmp_path, recorder, monkeypatch):
    """登録済みアカウントへの切り替えは元の `--session-id` をそのまま渡すので、替え先の子の番号は続く（累計を 2 回数えない）。"""
    run = claude.AccountRun(dict(os.environ), ["claude", "--session-id", "s-1"])
    replies = iter([_launched(ok=False, failure=claude.Failure.USAGE_LIMIT, session_id="s-1"), _launched(session_id="s-1")])
    monkeypatch.setattr(claude, "_launch_once", lambda *a: next(replies))
    options = claude.LaunchOptions(log=claude.LaunchLog({"name": "fix", "session_id": "s-1"}))
    got = claude._accounted(FakeChecks(run, run), run, "q", 30, tmp_path, options)
    assert got.ok
    assert [(row["session_id"], row["session_seq"]) for _, row in recorder.rows] == [("s-1", 0), ("s-1", 1)]


def test_a_skill_child_without_a_session_id_is_always_number_zero(recorder):
    """結果に会話の id が無い Skill の子は、どの会話の続きか分からないので常に 0。ほかの会話の番号も進めない。"""
    log = claude.LaunchLog({"name": "design"})
    bare = claude.ClaudeReply.failed("", "期限", failure=claude.Failure.TIMEOUT)._replace(launched=True)
    log.report(_launched(session_id="s-a"))
    log.report(bare)
    log.report(bare)
    log.report(_launched(session_id="s-a"))
    assert [(row["session_id"], row["session_seq"]) for _, row in recorder.rows] == [("s-a", 0), (None, 0), (None, 0), ("s-a", 1)]


def test_a_child_that_did_not_launch_hands_no_row(recorder):
    log = claude.LaunchLog({"name": "fix"})
    log.report(claude.ClaudeReply.failed("", "古い", failure=claude.Failure.REFUSED))
    log.report(_launched())
    assert len(recorder.rows) == 1 and recorder.rows[0][1]["session_seq"] == 0


def test_a_launched_child_without_json_hands_a_row_without_usage(recorder):
    timed = claude.ClaudeReply.failed("", "期限", failure=claude.Failure.TIMEOUT)._replace(launched=True, seconds=1.0)
    claude.LaunchLog({"name": "fix", "session_id": "s-1"}).report(timed)
    usage, row = recorder.rows[0]
    # 値の無い欄は None で渡し、使用量ログへ書く record_cli が欄ごと落とす
    assert usage is None and row["model"] is None and row["session_cost_usd"] is None and row["session_id"] == "s-1"


def test_without_a_recorder_nothing_is_written(monkeypatch):
    monkeypatch.setattr(claude, "launch_recorder", None)
    claude.LaunchLog({"name": "fix"}).report(_launched())  # 書き込み処理が無くても止まらない


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


class FakeProc:
    """管を持たない偽のプロセス（実プロセスを起動しない）。"""

    def __init__(self, stdout: str) -> None:
        self.returncode, self.pid = 0, 0
        self.stdin = self.stdout = self.stderr = None
        self._out = stdout

    def communicate(self, prompt: str = "", timeout: float = 0) -> tuple[str, str]:
        return self._out, ""


@pytest.fixture
def no_slot(monkeypatch):
    """スロットの取得を偽物にし（空きを待たず、環境に値を足さない）、中継の確かめを外す。"""

    @contextmanager
    def gate(agent, *, cwd=None, env=None, wait=None, ensure=True):
        yield {}

    monkeypatch.setattr(launch_slot, "gate", gate)
    monkeypatch.setattr(claude, "relay_checks", None)


def test_a_launched_child_is_marked_with_its_time_and_handed_to_the_log(no_slot, recorder, monkeypatch):
    """起動した時刻とかかった秒は差し替えた時計の値。子が終わったその場で行が渡る。"""
    answer = json.dumps({"result": "{}", "session_id": "s-2", "usage": {"output_tokens": 1}})
    monkeypatch.setattr(claude.subprocess, "Popen", lambda argv, **kw: FakeProc(answer))
    clock = iter([("2026-11-27T01:00:00+00:00", 10.0), ("2026-11-27T01:00:03+00:00", 13.5)])
    monkeypatch.setattr(claude, "_now", lambda: next(clock))
    options = claude.LaunchOptions(cli_floor=False, log=claude.LaunchLog({"name": "fix"}))
    got = claude.call_claude(["claude"], "q", 30, options=options)
    assert got.ok and got.launched and got.session_id == "s-2"
    assert (got.launched_at, got.seconds) == ("2026-11-27T01:00:00+00:00", 3.5)
    assert [(row["session_id"], row["seconds"]) for _, row in recorder.rows] == [("s-2", 3.5)]


def test_a_command_that_cannot_start_is_not_marked_launched(no_slot, recorder, monkeypatch):
    def popen(argv, **kw):
        raise OSError("無い")

    monkeypatch.setattr(claude.subprocess, "Popen", popen)
    options = claude.LaunchOptions(cli_floor=False, log=claude.LaunchLog({"name": "fix"}))
    got = claude.call_claude(["claude"], "q", 30, options=options)
    assert got.failure is claude.Failure.LAUNCH and not got.launched and recorder.rows == []


class FakeChecks:
    def __init__(self, got, again):
        self.got, self.again = got, again

    def begin(self, argv, env, cwd, keys):
        return self.got

    def retry(self, argv, env, cwd, keys):
        return self.again


def test_the_child_before_an_account_switch_is_handed_before_the_retry(tmp_path, recorder, monkeypatch):
    """利用上限で終えた子の行は、替え先の起動より先に渡る（打ち直しの途中で止められても残る）。番号は会話ごとに 0 から。"""
    run = claude.AccountRun(dict(os.environ), ["x"])
    limited = _launched(ok=False, failure=claude.Failure.USAGE_LIMIT, session_id="s-a")
    rows_at_retry: list[int] = []

    def launch_once(run, prompt, timeout, cwd, options):
        if rows_at_retry or recorder.rows:
            rows_at_retry.append(len(recorder.rows))
            return _launched(session_id="s-b")
        return limited

    monkeypatch.setattr(claude, "_launch_once", launch_once)
    options = claude.LaunchOptions(log=claude.LaunchLog({"name": "design"}))
    got = claude._accounted(FakeChecks(run, run), run, "q", 30, tmp_path, options)
    assert got.ok and rows_at_retry == [1]
    assert [(row["session_id"], row["session_seq"]) for _, row in recorder.rows] == [("s-a", 0), ("s-b", 0)]


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
