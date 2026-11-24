"""締め切りの割り込みのときの `ask-forward`（`on_deadline`）の、質問の片付けと拒否（#3814 の I7）。

既に試している「残りが無いときの拒否」「中継の外では通す」は test_guards にある。
"""

from __future__ import annotations

import os

import pytest

from ndf.domains.hooks.guards import ask
from ndf.domains.relay import signals

from ._hooks import sample

QUESTIONS = [{"question": "設計を承認するか", "header": "設計", "options": [], "multiSelect": False}]


def _payload(cwd, tool_use_id="toolu_A"):
    return sample("claude", "PreToolUse", cwd, tool_name="AskUserQuestion", tool_input={"questions": QUESTIONS}, tool_use_id=tool_use_id)


def _write(directory, payload, launch="L1"):
    assert signals.write_question(directory / signals.QUESTION_FILE, "s1", payload, 0.3, launch=launch)


def _lock(directory):
    return directory / f"{signals.QUESTION_FILE}.lock"


def test_the_question_of_the_interrupted_call_is_dropped_and_denied(relay, tmp_path):
    payload = _payload(tmp_path)
    _write(relay, payload)
    assert ask.on_deadline(os.environ, payload) == ask.QUESTION_NOT_RECORDED
    assert not signals.question_present(relay)


@pytest.mark.parametrize("tool_use_id", ["toolu_B", "", None, 1], ids=["another", "empty", "none", "not-a-string"])
def test_a_question_that_is_not_of_the_call_is_kept_and_denied(relay, tmp_path, tool_use_id):
    _write(relay, _payload(tmp_path))
    assert ask.on_deadline(os.environ, _payload(tmp_path, tool_use_id)) == ask.QUESTION_NOT_RECORDED
    assert signals.read_question(relay)["tool_use_id"] == "toolu_A"


def test_a_question_of_the_next_launch_is_kept_and_denied(relay, tmp_path):
    payload = _payload(tmp_path)
    signals.set_launch(relay, "L2")
    _write(relay, payload, "L2")
    assert ask.on_deadline(os.environ, payload) == ask.QUESTION_NOT_RECORDED
    assert signals.read_question(relay)["launch"] == "L2"


def test_a_question_whose_lock_is_taken_is_kept_and_denied(relay, tmp_path):
    payload = _payload(tmp_path)
    _write(relay, payload)
    with signals.held(_lock(relay), 0) as got:
        assert got and ask.on_deadline(os.environ, payload) == ask.QUESTION_NOT_RECORDED
    assert signals.question_present(relay)


def test_a_failure_to_open_the_lock_is_still_denied_for_the_reason(relay, tmp_path):
    # 片付けの OSError（錠のファイルを開けない）は外へ出さず、片付いたときと同じ理由で拒否する
    payload = _payload(tmp_path)
    _write(relay, payload)
    _lock(relay).unlink()
    _lock(relay).mkdir()
    with pytest.raises(OSError):
        signals.drop_question(relay, {"tool_use_id": "toolu_A"}, 0, launch="L1")
    assert ask.on_deadline(os.environ, payload) == ask.QUESTION_NOT_RECORDED
    assert signals.question_present(relay)


def test_an_error_other_than_oserror_is_not_swallowed(relay, tmp_path, monkeypatch):
    # OSError のほかはエントリポイントまで上げる（そこで fail_closed の拒否になる）
    def boom(*args, **kwargs):
        raise RuntimeError("壊れた")

    monkeypatch.setattr(signals, "drop_question", boom)
    with pytest.raises(RuntimeError):
        ask.on_deadline(os.environ, _payload(tmp_path))


def test_only_the_payload_already_recorded_passes_and_keeps_the_question(relay, tmp_path, monkeypatch):
    # 記録し終えた後の割り込みは記録を残して通す。見分けるのは payload の同一性で、等しいだけの別の payload は拒否する
    payload = _payload(tmp_path)
    _write(relay, payload)
    monkeypatch.setattr(ask, "_recorded", payload)
    assert ask.on_deadline(os.environ, payload) is None
    assert signals.question_present(relay)
    assert ask.on_deadline(os.environ, dict(payload)) == ask.QUESTION_NOT_RECORDED
    assert not signals.question_present(relay)
