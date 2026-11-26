"""承認の質問の回答を記録する guard（`launch-answer`。#1959 の受け入れ条件 9・14）。入力は回答の PostToolUse の実測の形。"""

from __future__ import annotations

import pytest

from ndf.adapters import launch_approval as la

from ._hooks import call_entry, sample

LABEL = la.APPROVE_LABEL.format("人")
OPTIONS = [{"label": LABEL, "description": ""}, {"label": la.DENY_LABEL, "description": ""}]
QUESTION = {
    "questions": [{"question": "起動の設定を承認するか（リクエスト q1）", "header": "起動の承認", "options": OPTIONS, "multiSelect": False}]
}


def _answered(cwd, questions=QUESTION["questions"], label=LABEL, session="s1"):
    data = {
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": questions},
        "tool_response": {"questions": questions, "answers": {questions[0]["question"]: label}},
        "tool_use_id": "toolu_A",
        "session_id": session,
    }
    return sample("claude", "PostToolUse", cwd, **data)


@pytest.fixture
def asked(tmp_path):
    target = tmp_path / "wt" / ".ndf" / "launch.json"
    la.put_question({"request_id": "q1", "repo": "/r", "sha256": "a" * 64, "target": str(target), "session": "s1"}, QUESTION)
    return target


def test_the_answer_to_the_same_question_in_the_same_session_is_recorded(asked, tmp_path):
    assert call_entry("claude", "PostToolUse", _answered(tmp_path)) is None
    assert la.request_answer("q1") == ("approve", "人")
    call_entry("claude", "PostToolUse", _answered(tmp_path, label=la.DENY_LABEL))
    assert la.request_answer("q1") == ("deny", None)


@pytest.mark.parametrize("case", ["no_request", "other_session", "changed_text", "free_text", "shared_questions"])
def test_an_answer_that_does_not_match_a_request_is_not_recorded(asked, tmp_path, case):
    if case == "no_request":
        la.state_file(la.QUESTIONS).unlink()
    if case == "shared_questions":
        la.state_file(la.QUESTIONS).chmod(0o644)  # 広い権限で復元した記録は、承認のコマンドと同じく無いと扱う
    questions = [{**QUESTION["questions"][0], "question": "文を足した質問"}] if case == "changed_text" else QUESTION["questions"]
    label = "承認する。ただし MCP は外して" if case == "free_text" else LABEL
    call_entry(
        "claude", "PostToolUse", _answered(tmp_path, questions=questions, label=label, session="s2" if case == "other_session" else "s1")
    )
    assert not la.state_file(la.ANSWERS).exists()


@pytest.mark.parametrize("name", ["NDF_AI_CHILD", "NDF_FACTORY_RUN", "NDF_RELAY_PLAN", "NDF_ORGANIZER_DIR"])
def test_answers_that_an_ai_may_have_given_are_not_recorded(asked, tmp_path, monkeypatch, name):
    monkeypatch.setenv(name, "1")
    call_entry("claude", "PostToolUse", _answered(tmp_path))
    assert la.request_answer("q1") == (None, None)
