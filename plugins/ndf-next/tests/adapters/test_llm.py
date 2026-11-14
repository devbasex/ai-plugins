from __future__ import annotations

import json

import pytest

from ndf.adapters import claude, llm, run_log


class FakeClaude:
    """答えを順に返す偽の呼び出し。受けた引数と問いを残す。"""

    def __init__(self, *replies: claude.ClaudeReply) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[list[str], str, float]] = []

    def __call__(self, argv, prompt, timeout):
        self.calls.append((list(argv), prompt, timeout))
        return self.replies.pop(0)


def reply(answer, cost=0.5):
    return claude.ClaudeReply(True, answer, json.dumps(answer, ensure_ascii=False), cost)


def good(decision="fix"):
    return {"decision": decision, "reason": "テストが落ちた", "basis": ["test_failed"]}


@pytest.fixture
def int_schema(tmp_path) -> dict:
    """整数 n を必須にするスキーマのファイルを書き、answer_schema で読んだもの。"""
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}))
    return llm.answer_schema(schema=str(path))


def test_answer_schema_fills_choices_into_decision_enum():
    schema = llm.answer_schema(choices=["fix", "stop"])

    assert schema["properties"]["decision"]["enum"] == ["fix", "stop"]
    assert llm.JUDGE_ANSWER["properties"]["decision"]["enum"] == []


def test_answer_schema_accepts_single_choice():
    assert llm.check_answer(good("stop"), llm.answer_schema(choices=["stop"])) == []


@pytest.mark.parametrize("kwargs", [{}, {"choices": ["a"], "schema": "x.json"}, {"choices": []}])
def test_answer_schema_needs_exactly_one_mode(kwargs):
    with pytest.raises(llm.AnswerSchemaError):
        llm.answer_schema(**kwargs)


def test_answer_schema_reads_schema_file(int_schema):
    assert llm.check_answer({"n": 1}, int_schema) == []
    assert llm.check_answer({"decision": "fix"}, int_schema) != []


def test_answer_schema_refuses_unreadable_or_invalid_schema(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"type": 3}))

    for path in (tmp_path / "missing.json", bad):
        with pytest.raises(llm.AnswerSchemaError):
            llm.answer_schema(schema=str(path))


def test_check_answer_rejects_choice_outside_and_missing_reason():
    schema = llm.answer_schema(choices=["fix", "stop"])

    assert llm.check_answer(good("wait"), schema) != []
    assert llm.check_answer({"decision": "fix", "basis": []}, schema) != []


def test_ask_passes_schema_to_claude_and_accepts_first_answer():
    schema = llm.answer_schema(choices=["fix", "stop"])
    fake = FakeClaude(reply(good()))

    got = llm.ask("どうするか", "材料", schema, 30, call=fake)

    assert got == llm.JudgeAnswer(True, good(), None, 1, 0.5)
    argv = fake.calls[0][0]
    assert json.loads(argv[argv.index("--json-schema") + 1]) == schema
    assert "--setting-sources" in argv


def test_ask_retries_once_and_tells_the_errors():
    schema = llm.answer_schema(choices=["fix", "stop"])
    fake = FakeClaude(reply(good("wait")), reply(good("stop")))

    got = llm.ask("どうするか", "材料", schema, 30, call=fake)

    assert got.ok and got.answer == good("stop") and got.attempts == 2 and got.cost == 1.0
    assert "形に合わなかった" in fake.calls[1][1]


def test_ask_returns_needs_human_after_two_misses_without_picking_words():
    schema = llm.answer_schema(choices=["fix", "stop"])
    text = claude.ClaudeReply(True, None, "fix にするのがよい", 0.1)
    fake = FakeClaude(text, claude.ClaudeReply(False, None, "", 0.0, "claude を起動できない"))

    got = llm.ask("どうするか", "材料", schema, 30, call=fake)

    assert not got.ok and got.answer is None and got.attempts == llm.ATTEMPTS
    assert got.cause["kind"] == "needs_human"
    assert "fix にするのがよい" in got.cause["detail"] and "claude を起動できない" in got.cause["detail"]


def test_ask_in_json_mode_does_not_require_decision(int_schema):
    assert llm.ask("q", "m", int_schema, 30, call=FakeClaude(reply({"n": 3}))).answer == {"n": 3}
    missed = llm.ask("q", "m", int_schema, 30, call=FakeClaude(reply({"x": 1}), reply({"n": "a"})))
    assert missed.cause["kind"] == "needs_human"


def test_ask_records_attempts_and_validity_in_run_log(tmp_path):
    log = run_log.open_run("judge-test", location="repo")
    llm.ask("q", "m", llm.answer_schema(choices=["fix"]), 30, call=FakeClaude(reply(good("x")), reply(good())))

    lines = [json.loads(line) for line in log.path.read_text().splitlines()]
    judge = [line for line in lines if line["event"] == "judge"]
    assert judge[0]["fields"] == {"attempts": 2, "valid": True, "cost": 1.0}


def test_ask_launches_with_judge_permission_and_no_tools():
    fake = FakeClaude(reply(good()))

    llm.ask("q", "m", llm.answer_schema(choices=["fix"]), 30, call=fake)

    argv = fake.calls[0][0]
    assert argv[argv.index("--tools") + 1] == "" and "--permission-mode" not in argv


@pytest.mark.parametrize(
    ("failure", "kind"),
    [(claude.Failure.LAUNCH, "precondition"), (claude.Failure.TIMEOUT, "interrupted"), (claude.Failure.USAGE_LIMIT, "usage_limit")],
)
def test_ask_stops_at_once_on_failures_retrying_cannot_fix(failure, kind):
    """起動できない・期限切れ・利用上限は needs_human にまとめず、打ち直さない（#3394）。"""
    fake = FakeClaude(claude.ClaudeReply.failed("", "claude の失敗", 0.2, failure), reply(good()))

    got = llm.ask("q", "m", llm.answer_schema(choices=["fix"]), 30, call=fake)

    assert not got.ok and got.attempts == 1 and len(fake.calls) == 1
    assert got.cause == {"kind": kind, "detail": "claude の失敗"}
