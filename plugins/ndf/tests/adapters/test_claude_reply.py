"""claude の起動と回答の読み方（縛った回答・本文の JSON・失敗の種類・期限切れ・子へ渡す環境）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ndf.adapters import claude, run_log


def test_call_claude_reads_structured_output(tmp_path):
    fake = tmp_path / "claude"
    payload = {"result": "", "structured_output": {"decision": "fix"}, "total_cost_usd": 0.25}
    fake.write_text(f"#!/bin/sh\ncat > /dev/null\necho '{json.dumps(payload)}'\n")
    fake.chmod(0o755)

    got = claude.call_claude([str(fake)], "質問", 30)

    assert got == claude.ClaudeReply(True, {"decision": "fix"}, "", 0.25)


def fake_claude(tmp_path: Path, stdout: str) -> Path:
    """質問を読み捨て、`stdout` をそのまま標準出力へ書く偽の claude。"""
    out = tmp_path / "claude.stdout"
    out.write_text(stdout)
    fake = tmp_path / "claude"
    fake.write_text(f"#!/bin/sh\ncat > /dev/null\ncat '{out}'\n")
    fake.chmod(0o755)
    return fake


def test_call_claude_missing_command_is_not_ok(tmp_path):
    got = claude.call_claude([str(tmp_path / "missing")], "質問", 30)

    assert not got.ok and got.failure is claude.Failure.LAUNCH


@pytest.mark.parametrize("stdout", ["not json", json.dumps({"is_error": True, "result": "boom"})])
def test_call_claude_failures_are_not_ok(tmp_path, stdout):
    assert not claude.call_claude([str(fake_claude(tmp_path, stdout))], "質問", 30).ok


@pytest.mark.parametrize(
    ("stdout", "answer"),
    [(json.dumps({"result": '{"a": 1}'}), {"a": 1}), (json.dumps({"result": "fix です"}), None)],
)
def test_call_claude_reads_json_in_result_text(tmp_path, stdout, answer):
    assert claude.call_claude([str(fake_claude(tmp_path, stdout))], "質問", 30).answer == answer


# ---------------------------------------------------------------- 現状固定: 子へ渡す環境と失敗の形（I-011・I-012）


ENV_DUMP = (
    "import json, os, sys\n"
    "prompt = sys.stdin.read()\n"
    "seen = {k: os.environ.get(k) for k in ('NDF_RUN_ID', 'NDF_RUN_LOG', 'PIN_PARENT')}\n"
    "print(json.dumps({'result': json.dumps({'prompt': prompt, 'cwd': os.getcwd(), 'env': seen})}))\n"
)


def test_call_claude_passes_parent_env_prompt_and_cwd_without_run_log(tmp_path, no_run_env):
    got = claude.call_claude([sys.executable, "-c", ENV_DUMP], "質問", 30, cwd=tmp_path)

    assert (got.ok, got.cost, got.error) == (True, 0.0, None)
    env = {"NDF_RUN_ID": None, "NDF_RUN_LOG": None, "PIN_PARENT": "kept"}
    assert got.answer == {"prompt": "質問", "cwd": str(tmp_path), "env": env}


def test_call_claude_passes_open_run_log_to_child(no_run_env):
    log = run_log.open_run("entry", run_id="r1", location="repo")

    got = claude.call_claude([sys.executable, "-c", ENV_DUMP], "q", 30)

    assert got.answer["env"] == {"NDF_RUN_ID": "r1", "NDF_RUN_LOG": str(log.path), "PIN_PARENT": "kept"}


def test_call_claude_nonzero_exit_without_stdout_reports_stderr(no_run_env):
    script = "import sys; sys.stdin.read(); sys.stderr.write('boom'); sys.exit(3)"

    got = claude.call_claude([sys.executable, "-c", script], "q", 30)

    assert got == claude.ClaudeReply(False, None, "boom", 0.0, "claude が終了コード 3 で終えた: boom")


LIMIT_JSON = json.dumps({"is_error": True, "api_error_status": 429, "result": "You've hit your limit · resets 3pm"})


@pytest.mark.parametrize(
    "script",
    [
        f"import sys; sys.stdin.read(); print({LIMIT_JSON!r})",
        'import sys; sys.stdin.read(); sys.stderr.write("You\'ve hit your limit · resets 3pm\\n"); sys.exit(1)',
    ],
)
def test_call_claude_tells_usage_limit_apart(no_run_env, script):
    got = claude.call_claude([sys.executable, "-c", script], "q", 30)

    assert not got.ok and got.failure is claude.Failure.USAGE_LIMIT


def test_call_claude_other_error_can_be_retried(tmp_path):
    got = claude.call_claude([str(fake_claude(tmp_path, json.dumps({"is_error": True, "result": "boom"})))], "q", 30)

    assert not got.ok and got.failure is None


def test_call_claude_nonzero_exit_with_stdout_still_parses(no_run_env):
    script = 'import sys; sys.stdin.read(); print(\'{"result": "{}", "total_cost_usd": 1}\'); sys.exit(1)'

    got = claude.call_claude([sys.executable, "-c", script], "q", 30)

    assert got == claude.ClaudeReply(True, {}, "{}", 1.0)


def test_call_claude_timeout_is_not_ok(no_run_env):
    got = claude.call_claude([sys.executable, "-c", "import time; time.sleep(5)"], "q", 0.2)

    assert (got.ok, got.answer, got.raw, got.cost) == (False, None, "", 0.0)
    assert got.error.startswith("claude が期限を超えた: ") and got.failure is claude.Failure.TIMEOUT


# --- #4013: 結果の行の読み取り ------------------------------------------------------------------

# 実測の形（2026-11-21、claude 2.1.330 の修正担当の結果の行から値だけを残した）
USAGE = {"iterations": [{"input_tokens": 2, "cache_read_input_tokens": 130418, "cache_creation_input_tokens": 695}]}
MODEL_USAGE = {
    "claude-opus-5-5": {
        "inputTokens": 10,
        "outputTokens": 20,
        "cacheReadInputTokens": 130418,
        "cacheCreationInputTokens": 695,
        "contextWindow": 1000000,
    }
}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "usage": USAGE, "modelUsage": MODEL_USAGE}


def _lines(*rows: object) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def test_issue_4013_result_line_is_the_last_result_object():
    """#4013: 1 歩ごとの行の後の結果の行と、`--output-format json` の 1 行を返す。末尾の改行・本文の U+2028 で割らない。"""
    steps = _lines({"type": "system", "subtype": "init"}, {"type": "assistant", "message": {}}, RESULT)
    assert claude.result_line(steps) == RESULT
    assert claude.result_line(json.dumps(RESULT)) == RESULT
    assert claude.result_line(steps + "\n\n") == RESULT
    texted = {**RESULT, "result": "一行目\u2028二行目"}
    assert claude.result_line(_lines({"type": "assistant"}) + json.dumps(texted, ensure_ascii=False)) == texted


@pytest.mark.parametrize("text", [_lines({"type": "assistant"}), "途中で止まった", "", _lines([1, 2])])
def test_issue_4013_result_line_without_a_result_is_none(text):
    assert claude.result_line(text) is None


def test_issue_4013_context_tokens_add_the_last_iteration():
    """#4013: `usage.iterations` の最後の要素の入力・キャッシュの読み・キャッシュの作成の和。"""
    assert claude.context_tokens(RESULT) == 131115
    two = {
        "usage": {"iterations": [{"input_tokens": 1, "cache_read_input_tokens": 1, "cache_creation_input_tokens": 1}, *USAGE["iterations"]]}
    }
    assert claude.context_tokens(two) == 131115


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        {"usage": {}},
        {"usage": {"iterations": []}},
        {"usage": {"iterations": [{"input_tokens": "2", "cache_read_input_tokens": 1, "cache_creation_input_tokens": 1}]}},
        {"usage": {"iterations": [{"input_tokens": 2, "cache_read_input_tokens": 1}]}},
    ],
)
def test_issue_4013_context_tokens_unreadable_is_none(result):
    assert claude.context_tokens(result) is None


def test_issue_4013_context_window_is_the_main_models_window():
    """#4013: 主モデル（トークンの和が最も大きい）の窓。和が同じなら窓の小さい方。"""
    assert claude.context_window(RESULT) == 1000000
    small = {"inputTokens": 1, "outputTokens": 1, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0, "contextWindow": 200000}
    assert claude.context_window({"modelUsage": {**MODEL_USAGE, "haiku": small}}) == 1000000
    same = {"a": {"inputTokens": 5, "contextWindow": 1000000}, "b": {"inputTokens": 5, "outputTokens": "x", "contextWindow": 200000}}
    assert claude.context_window({"modelUsage": same}) == 200000


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        {"modelUsage": {}},
        {"modelUsage": {"m": {"inputTokens": 1, "contextWindow": "1000000"}}},
        {"modelUsage": {"m": {"inputTokens": 1, "contextWindow": 0}}},
    ],
)
def test_issue_4013_context_window_unreadable_is_none(result):
    assert claude.context_window(result) is None


def test_issue_4013_compacted_reads_the_whole_output(tmp_path):
    """#4013: 途中の行の自動の要約は、その後に末尾の読み取りの上限を超える行が続いても見つける。"""
    from ndf.adapters import agents

    out = tmp_path / "round1-fix.out"
    filler = json.dumps({"type": "assistant", "message": "x" * (agents.TAIL_BYTES + 10)})
    out.write_text(_lines({"type": "system", "subtype": "compact_boundary"}) + filler + "\n", encoding="utf-8")
    assert claude.compacted(out) is True
    out.write_text(_lines({"type": "assistant"}, {"type": "system", "subtype": "compact_boundary"}), encoding="utf-8")
    assert claude.compacted(out) is True


def test_issue_4013_compacted_is_false_without_the_line(tmp_path):
    out = tmp_path / "round1-fix.out"
    assert claude.compacted(out) is False
    out.write_text("JSON でない\n{壊れた\n", encoding="utf-8")
    assert claude.compacted(out) is False
    out.write_text(_lines({"type": "system", "subtype": "init"}, ["compact_boundary"]), encoding="utf-8")
    assert claude.compacted(out) is False


def test_issue_4013_conversation_missing_reads_the_errors_or_stderr():
    """#4013: 結果の行の `errors` か標準エラーに、会話が見つからない文言（後ろに ID が続く）があるか。"""
    mark = f"{claude.NO_CONVERSATION}: 0f8e"
    assert claude.conversation_missing(_lines({**RESULT, "is_error": True, "errors": [mark]}), "") is True
    assert claude.conversation_missing("", f"Error: {mark}\n") is True
    assert claude.conversation_missing(_lines(RESULT), "別の誤り") is False
    assert claude.conversation_missing(_lines({**RESULT, "errors": mark}), "") is False
    assert claude.conversation_missing("", "") is False
