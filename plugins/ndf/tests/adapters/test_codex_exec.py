"""`codex exec --json` の出力の読み方と、相手役の起動への値の足し方（#2944）。

出力の見本は codex-cli 0.172.0 を 2026-11-27 に打った結果で、スレッド ID だけを作り物へ置き換えた。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ndf.adapters import agents, model_choice
from ndf.core.converge import participants

THREAD = '{"type":"thread.started","thread_id":"00000000-0000-7000-8000-000000000000"}\n{"type":"turn.started"}\n'
USAGE = (
    '{"type":"turn.completed","usage":{"input_tokens":32578,"cached_input_tokens":13952,'
    '"cache_write_input_tokens":0,"output_tokens":5,"reasoning_output_tokens":0}}\n'
)
OK = THREAD + '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"OK"}}\n' + USAGE
BWRAP = "bwrap: No permissions to create a new namespace, likely because the kernel does not allow non-privileged user namespaces.\n"


def _command(output: str, exit_code: int, status: str) -> str:
    item = {"id": "item_1", "type": "command_execution", "command": "/bin/bash -c ls", "aggregated_output": output}
    return json.dumps({"type": "item.completed", "item": {**item, "exit_code": exit_code, "status": status}}) + "\n"


def _failed(message: str) -> str:
    error = json.dumps({"type": "error", "message": message})
    return THREAD + error + "\n" + json.dumps({"type": "turn.failed", "error": {"message": message}}) + "\n"


MODEL_REFUSED = _failed("The 'no-such-model-xyz' model is not supported when using Codex with a ChatGPT account.")
EFFORT_REFUSED = _failed(
    json.dumps(
        {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "code": None,
                "message": "[ReasoningEffortParam] [reasoning.effort] [invalid_enum_value] Invalid value: 'bogus'.",
                "param": None,
            },
            "status": 400,
        },
        indent=2,
    )
)
UNAUTHORIZED = _failed("unexpected status 401 Unauthorized: Missing bearer or basic authentication in header")


def _paths(tmp_path: Path, out: str, err: str = "") -> agents.AgentPaths:
    paths = agents.AgentPaths.at(tmp_path / "round1-codex")
    paths.out.write_text(out, encoding="utf-8")
    paths.err.write_text(err, encoding="utf-8")
    return paths


def test_the_usage_of_every_completed_turn_is_added_up(tmp_path):
    got = agents.codex_usage(_paths(tmp_path, OK + USAGE).out)
    assert got == agents.CodexUsage(input_tokens=65156, cached_input_tokens=27904, cache_write_input_tokens=0, output_tokens=10)


@pytest.mark.parametrize("out", [MODEL_REFUSED, "ただの回答の本文\n" + USAGE, ""])
def test_a_run_without_a_completed_turn_has_no_usage(tmp_path, out):
    """失敗した起動と、`--json` でない出力（cross-refactoring の起動）は使用量を持たない。"""
    assert agents.codex_usage(_paths(tmp_path, out).out) is None


def test_a_sandbox_failure_is_found_in_the_command_output_of_the_stream(tmp_path):
    """`--json` では bwrap の失敗は標準エラーに出ず、コマンドの出力の行に入る。返す値は公開されるので、出力の行を写さない。"""
    out = THREAD + _command("bwrap: Can't mkdir /home/someone/secret\n", 1, "failed") + USAGE
    assert agents.sandbox_failed(_paths(tmp_path, out)) == agents.SANDBOX_FAILED_IN_COMMAND


@pytest.mark.parametrize(
    "out",
    [
        THREAD + _command(BWRAP, 0, "completed"),  # 読んだファイルの中身が bwrap: で始まるだけ
        THREAD + _command("notes.md:1:" + BWRAP, 1, "failed"),
        "本文\n" + _command(BWRAP, 1, "failed"),  # --json の出力でない
    ],
)
def test_a_quoted_sandbox_line_is_not_a_sandbox_failure(tmp_path, out):
    assert agents.sandbox_failed(_paths(tmp_path, out)) is None


def _limit(tmp_path, out: str) -> object:
    spec = agents.Launch("codex", ("codex",), tmp_path, _paths(tmp_path, out), 60)
    return agents._out_usage_limit(spec, agents.CodexTail(), ended=False)


@pytest.mark.parametrize(
    "message",
    ["You've hit your usage limit. To get more access now, send a request to your admin", "Quota exceeded. Check your plan."],
)
def test_a_usage_limit_is_found_in_the_error_events(tmp_path, message):
    assert _limit(tmp_path, _failed(message)) == message


@pytest.mark.parametrize(
    "out",
    [
        THREAD + _command("rate limit exceeded\n", 0, "completed"),
        THREAD + json.dumps({"type": "error", "message": "Reconnecting... 2/5 (unexpected status 429)"}) + "\n",
        "rate limit exceeded\n",
        UNAUTHORIZED,
    ],
)
def test_a_quoted_or_transient_line_is_not_a_usage_limit(tmp_path, out):
    assert _limit(tmp_path, out) is None


def test_the_watch_reads_only_the_complete_lines_added_since_the_last_check(tmp_path):
    out = _paths(tmp_path, THREAD + '{"type":').out
    seen = agents.CodexTail()
    assert [e["type"] for e in seen.new(out, ended=False)] == ["thread.started", "turn.started"]
    with out.open("a", encoding="utf-8") as fh:
        fh.write('"error"}\n{"type":"turn.failed"}')
    assert [e["type"] for e in seen.new(out, ended=False)] == ["error"]
    assert [e["type"] for e in seen.new(out, ended=True)] == ["turn.failed"]


def test_the_watch_stops_reading_an_output_that_is_not_json(tmp_path):
    out = _paths(tmp_path, "本文\n").out
    seen = agents.CodexTail()
    assert seen.new(out, ended=False) == [] and seen.other


@pytest.mark.parametrize("out", [MODEL_REFUSED, EFFORT_REFUSED])
def test_a_refusal_of_the_model_or_effort_is_recognized(tmp_path, out):
    assert agents.codex_refused(_paths(tmp_path, out).out)


@pytest.mark.parametrize(
    "out",
    [OK, UNAUTHORIZED, MODEL_REFUSED + USAGE, EFFORT_REFUSED + '{"type":"item.completed","item":{"type":"agent_message"}}\n'],
)
def test_other_failures_and_runs_that_answered_are_not_refusals(tmp_path, out):
    assert not agents.codex_refused(_paths(tmp_path, out).out)


class _Proc:
    pid = 0


def test_the_answer_left_by_a_previous_launch_is_removed_before_launching(tmp_path, monkeypatch):
    """codex は失敗すると `-o` の前の中身を残すので、起動の前に消す。"""
    paths = _paths(tmp_path, "")
    agents.codex_answer_path(paths.out).write_text('{"findings": []}', encoding="utf-8")
    monkeypatch.setattr(agents.subprocess, "Popen", lambda *a, **k: _Proc())
    argv = ("codex", "-o", str(agents.codex_answer_path(paths.out)), "質問")
    agents._start(agents.Launch("codex", argv, tmp_path, paths, 60))
    assert not agents.codex_answer_path(paths.out).exists()


@pytest.mark.parametrize("runtime", ["claude", "codex", "kiro", "agy"])
def test_a_launch_that_does_not_write_the_answer_file_keeps_it(tmp_path, monkeypatch, runtime):
    """回答先に指定していないファイルは、その起動が作ったものではないので消さない。"""
    paths = _paths(tmp_path, "")
    agents.codex_answer_path(paths.out).write_text("残す", encoding="utf-8")
    monkeypatch.setattr(agents.subprocess, "Popen", lambda *a, **k: _Proc())
    agents._start(agents.Launch(runtime, (runtime, "質問"), tmp_path, paths, 60))
    assert agents.codex_answer_path(paths.out).read_text(encoding="utf-8") == "残す"


def test_the_seconds_of_a_launched_child_are_kept(tmp_path, monkeypatch):
    ticks = iter([100.0, 107.5])
    monkeypatch.setattr(agents, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    monkeypatch.setattr(agents, "_start", lambda spec: _Proc())
    monkeypatch.setattr(agents, "watch", lambda proc, spec, poll: agents.AgentStatus("codex", "done", 0, "", ()))
    spec = agents.Launch("codex", ("codex",), tmp_path, agents.AgentPaths.at(tmp_path / "x"), 60)
    assert agents._start_and_watch(spec, None).seconds == 7.5


def test_a_child_that_never_started_has_no_seconds(tmp_path, monkeypatch):
    def missing(spec):
        raise FileNotFoundError(2, "No such file", "codex")

    monkeypatch.setattr(agents, "_start", missing)
    spec = agents.Launch("codex", ("codex",), tmp_path, agents.AgentPaths.at(tmp_path / "x"), 60)
    assert agents._start_and_watch(spec, None).seconds is None


# --- 値を足す ----------------------------------------------------------------------------------------


@pytest.fixture
def no_sandbox_check(monkeypatch):
    from ndf.adapters import launch_settings

    monkeypatch.setattr(participants, "_sandbox_args", lambda a, w: launch_settings.SafetyArgs(["-s", "read-only"]))


def _choice(runtime: str, model=None, effort=None) -> model_choice.Choice:
    source = model_choice.Source.EXPERIMENT
    return model_choice.Choice("seat", "review", runtime, "review", None, model, effort, source, source)


def test_a_chosen_codex_seat_gets_the_values_and_the_usage_output(tmp_path, no_sandbox_check):
    stem = tmp_path / "round1-codex"
    plain = participants.review_launch("codex", tmp_path, "質問", stem)
    spec = participants.with_choice(plain, _choice("codex", "gpt-6-luna", "high"))
    answer = str(agents.codex_answer_path(spec.paths.out))
    added = ["-c", "model_reasoning_effort=high", "-m", "gpt-6-luna", "--json", "-o", answer]
    assert list(spec.argv) == [*plain.argv[:-1], *added, "質問"]
    assert "--ephemeral" in spec.argv and answer.endswith(".out.answer")


def test_a_codex_seat_of_the_unspecified_arm_only_gets_the_usage_output(tmp_path, no_sandbox_check):
    plain = participants.review_launch("codex", tmp_path, "質問", tmp_path / "x")
    spec = participants.with_choice(plain, _choice("codex"))
    assert list(spec.argv[len(plain.argv) - 1 : -1]) == ["--json", "-o", str(agents.codex_answer_path(spec.paths.out))]


def test_a_claude_seat_gets_the_model_and_effort(tmp_path):
    plain = participants.read_only_launch("役", tmp_path, "質問", tmp_path / "x")
    spec = participants.with_choice(plain, _choice("claude", "sonnet", "high"))
    assert list(spec.argv) == [*plain.argv[:-1], "--model", "sonnet", "--effort", "high", "質問"]


def test_cross_refactoring_launches_are_unchanged(tmp_path, no_sandbox_check):
    """値を選ばない起動（cross-refactoring の相手役）は、今の引数と環境のままである。"""
    reader = participants.ClaudeReader("役")
    for seat in ("codex", "claude"):
        spec = participants.reading_launch(seat, reader, tmp_path, "質問", tmp_path / "x")
        assert participants.with_choice(spec, None) is spec
        assert not {"--json", "-o", "--model", "--effort", "-m"} & set(spec.argv) and spec.env is None


def test_the_answer_file_is_read_instead_of_the_stream(tmp_path):
    paths = _paths(tmp_path, OK)
    agents.codex_answer_path(paths.out).write_text('{"findings": [{"text": "指摘"}]}\n', encoding="utf-8")
    assert participants.findings_of(participants.answer_text("codex", paths.out)) == [{"text": "指摘"}]


@pytest.mark.parametrize("runtime, out", [("kiro", OK), ("agy", OK), ("kiro", "本文\n"), ("codex", "本文\n")])
def test_an_old_answer_file_is_read_only_for_a_codex_stream(tmp_path, runtime, out):
    """前の起動の回答が残っていても、回答のファイルを書かない起動は自分の出力を読む。"""
    paths = _paths(tmp_path, out)
    agents.codex_answer_path(paths.out).write_text('{"findings": [{"text": "古い"}]}\n', encoding="utf-8")
    assert participants.answer_text(runtime, paths.out) == agents.tail(paths.out)


def test_a_stream_without_an_answer_file_has_no_findings(tmp_path):
    out = _paths(tmp_path, THREAD + _command('{"findings": []}\n', 0, "completed")).out
    assert participants.findings_of(participants.answer_text("codex", out)) is None
