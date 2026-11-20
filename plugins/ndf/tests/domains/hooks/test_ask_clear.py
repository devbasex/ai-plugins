"""質問が閉じたら質問のファイルを消す guard（`ask-answered`・`ask-closed`。#3814 の受け入れ条件 4・I5）。

入力は 2026-11-20 の実測の形（回答の PostToolUse は `tool_name`・`tool_use_id`・`tool_response.answers` を持つ）を
エントリポイント（`ndf.domains.hooks.main`）へ通す。
"""

from __future__ import annotations

import pytest

from ndf.domains.hooks import tables
from ndf.domains.relay import signals

from ._hooks import call_entry, records, sample

QUESTIONS = [
    {"question": "設計を承認するか", "header": "設計", "options": [{"label": "承認", "description": "進める"}], "multiSelect": False}
]


@pytest.fixture
def relay(tmp_path, monkeypatch):
    """中継の作業ディレクトリ。今の起動は L1 で、hook の環境も L1。"""
    directory = tmp_path / "relay"
    directory.mkdir()
    monkeypatch.setenv("NDF_RELAY_DIR", str(directory))
    monkeypatch.setenv("NDF_RELAY_LAUNCH", "L1")
    signals.set_launch(directory, "L1")
    return directory


def _ask(directory, tool_use_id: str) -> None:
    """今の起動 L1 の子が出した質問を書く（`ask-forward` と同じ書き方）。"""
    payload = {"tool_use_id": tool_use_id, "tool_input": {"questions": QUESTIONS}, "cwd": str(directory)}
    assert signals.write_question(directory / signals.QUESTION_FILE, "s1", payload, 0.3, launch="L1")


def _answered(cwd, **override):
    """回答の PostToolUse の入力（実測の形）。"""
    data = {
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": QUESTIONS},
        "tool_response": {"questions": QUESTIONS, "answers": {"設計を承認するか": "承認"}},
        "tool_use_id": "toolu_A",
    }
    return sample("claude", "PostToolUse", cwd, **{**data, **override})


def _closed(event: str, cwd):
    return sample("claude", event, cwd)


def _present(directory) -> bool:
    return signals.question_present(directory)


def test_i5_answer_drops_the_question_with_the_same_tool_use_id(relay, tmp_path):
    # 受け入れ条件 4・I5: 回答の PostToolUse は同じ tool_use_id の質問だけを消す
    _ask(relay, "toolu_A")
    assert call_entry("claude", "PostToolUse", _answered(tmp_path)) is None
    assert not _present(relay)
    assert records()[-1]["notes"]["cleared"] == "dropped"


def test_i5_answer_keeps_another_question(relay, tmp_path):
    # I5: 回答した質問 A の前に同じ子が質問 B へ置き換えていたら、A の tool_use_id で B を消さない
    _ask(relay, "toolu_B")
    call_entry("claude", "PostToolUse", _answered(tmp_path, tool_use_id="toolu_A"))
    assert signals.read_question(relay)["tool_use_id"] == "toolu_B"
    assert records()[-1]["notes"]["cleared"] == "kept"


@pytest.mark.parametrize("event", ["Stop", "UserPromptSubmit"])
def test_i5_closed_question_is_dropped_by_the_current_launch(relay, tmp_path, event):
    # 受け入れ条件 4・I5: 回答されずに閉じた質問（Chat about this の後の Stop・Esc の後の UserPromptSubmit）を消す
    _ask(relay, "toolu_A")
    call_entry("claude", event, _closed(event, tmp_path))
    assert not _present(relay)
    assert records()[-1]["notes"]["cleared"] == "dropped"


@pytest.mark.parametrize("launch", ["L0", None])
@pytest.mark.parametrize("event", ["PostToolUse", "Stop", "UserPromptSubmit"])
def test_i5_hook_of_another_launch_drops_nothing(relay, tmp_path, monkeypatch, event, launch):
    # I5: 起動し直す前の起動の hook が遅れて動いた・起動の ID が無い hook は何も消さない
    if launch is None:
        monkeypatch.delenv("NDF_RELAY_LAUNCH")
    else:
        monkeypatch.setenv("NDF_RELAY_LAUNCH", launch)
    _ask(relay, "toolu_A")
    payload = _answered(tmp_path) if event == "PostToolUse" else _closed(event, tmp_path)
    call_entry("claude", event, payload)
    assert signals.read_question(relay)["tool_use_id"] == "toolu_A"


@pytest.mark.parametrize("event", ["PostToolUse", "Stop", "UserPromptSubmit"])
def test_i5_broken_question_is_dropped_by_any_close(relay, tmp_path, event):
    # I5: 壊れた質問のファイルは、どの閉じる事象でも消える
    (relay / signals.QUESTION_FILE).write_text("{", encoding="utf-8")
    payload = _answered(tmp_path) if event == "PostToolUse" else _closed(event, tmp_path)
    call_entry("claude", event, payload)
    assert not _present(relay)


@pytest.mark.parametrize("tool_use_id", ["", 7, None])
def test_answer_without_tool_use_id_leaves_even_a_broken_question_for_the_stop(relay, tmp_path, tool_use_id):
    # 受け入れ条件 4: 入力の tool_use_id が欠落・空・文字列以外なら消さず、続く Stop で消える
    (relay / signals.QUESTION_FILE).write_text("{", encoding="utf-8")
    payload = _answered(tmp_path, tool_use_id=tool_use_id)
    if tool_use_id is None:
        del payload["tool_use_id"]
    call_entry("claude", "PostToolUse", payload)
    assert _present(relay)
    call_entry("claude", "Stop", _closed("Stop", tmp_path))
    assert not _present(relay)


def test_answer_while_the_lock_is_held_leaves_the_question_for_the_stop(relay, tmp_path):
    # 受け入れ条件 4・I5: 回答の hook がロックを待ちきれなければ消さずに通し、続く Stop で消える
    _ask(relay, "toolu_A")
    with signals.held(relay / f"{signals.QUESTION_FILE}.lock", 0) as got:
        assert got
        assert call_entry("claude", "PostToolUse", _answered(tmp_path)) is None
    assert _present(relay)
    assert records()[-1]["notes"]["cleared"] == "busy"
    call_entry("claude", "Stop", _closed("Stop", tmp_path))
    assert not _present(relay)


def test_question_that_cannot_be_unlinked_is_left_and_noted(relay, tmp_path):
    # 受け入れ条件 4: 消せない（question.json がディレクトリ）なら例外を出さずに通し、notes に failed が残る
    (relay / signals.QUESTION_FILE).mkdir()
    assert call_entry("claude", "Stop", _closed("Stop", tmp_path)) is None
    assert (relay / signals.QUESTION_FILE).is_dir()
    assert records()[-1]["notes"]["cleared"] == "failed"


@pytest.mark.parametrize("event", ["PostToolUse", "Stop", "UserPromptSubmit"])
def test_outside_the_relay_or_without_a_question_nothing_happens(tmp_path, monkeypatch, event):
    # 中継の外・質問のファイルが無いときは何もしない（ロックも取らない）
    payload = _answered(tmp_path) if event == "PostToolUse" else _closed(event, tmp_path)
    call_entry("claude", event, payload)
    assert "cleared" not in records()[-1]["notes"]
    directory = tmp_path / "relay"
    directory.mkdir()
    monkeypatch.setenv("NDF_RELAY_DIR", str(directory))
    monkeypatch.setenv("NDF_RELAY_LAUNCH", "L1")
    signals.set_launch(directory, "L1")
    call_entry("claude", event, payload)
    assert "cleared" not in records()[-1]["notes"]
    assert not (directory / f"{signals.QUESTION_FILE}.lock").exists()


def test_codex_skips_the_close_guards():
    # Codex には AskUserQuestion の Tool が無い（ask-forward と同じ理由で外す）。Claude Code の PostToolUse は tool_use_id を読む
    skipped = tables.runtimes()["codex"]["skipped"]
    assert skipped["ask-answered"] == skipped["ask-forward"] == skipped["ask-closed"]
    assert "tool_use_id" in tables.event_spec("claude", "PostToolUse")["optional"]
