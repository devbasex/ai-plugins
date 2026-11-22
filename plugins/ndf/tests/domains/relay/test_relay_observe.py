"""会話の記録の末尾から読む止めてよい時点の事実（`observe.tool_pending`・`turn_done`。#3864 の決定 11）。

記録の形は U1 の確かめ 3（PR #4018）に合わせる: 道具の実行中は会話の行の最後が `stop_reason: tool_use` の
`assistant` の `tool_use` のままで、終わると `user` の `tool_result` が同じ `tool_use_id` で続く。
ファイルの最後の行は会話の行でないことがある。
"""

from __future__ import annotations

import json

from ndf.domains.relay import observe

SIZE = 4096
AT = "2026-11-22T01:02:03.000Z"


def _assistant(stop, *blocks, at=AT):
    return {"type": "assistant", "timestamp": at, "message": {"role": "assistant", "stop_reason": stop, "content": list(blocks)}}


def _tool_use(tid):
    return {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": "sleep 30"}}


def _tool_result(tid):
    return {"type": "user", "timestamp": AT, "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid}]}}


TEXT = {"type": "text", "text": "終わった"}
PROMPT = {"type": "user", "timestamp": AT, "message": {"role": "user", "content": "次をして"}}
TAIL_ROW = {"type": "last-prompt", "lastPrompt": "x"}  # 会話でない行


def _write(tmp_path, rows, tail=""):
    path = tmp_path / "s.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows) + tail, encoding="utf-8")
    return path


def test_tool_pending_is_true_until_the_matching_tool_result(tmp_path):
    running = [PROMPT, _assistant("tool_use", _tool_use("t1")), TAIL_ROW]
    assert observe.tool_pending(_write(tmp_path, running), SIZE) is True
    done = [*running, _tool_result("t1"), TAIL_ROW]
    assert observe.tool_pending(_write(tmp_path, done), SIZE) is False
    # 別の id の結果では閉じない
    assert observe.tool_pending(_write(tmp_path, [*running, _tool_result("t0")]), SIZE) is True
    # 最後が本文だけの応答なら偽
    assert observe.tool_pending(_write(tmp_path, [*done, _assistant("end_turn", TEXT)]), SIZE) is False


def test_tool_pending_skips_a_cut_line_and_reads_none_without_a_record(tmp_path):
    rows = [PROMPT, _assistant("tool_use", _tool_use("t1"))]
    assert observe.tool_pending(_write(tmp_path, rows, tail='{"type": "user", "mess'), SIZE) is True
    assert observe.tool_pending(tmp_path / "missing.jsonl", SIZE) is None
    assert observe.tool_pending(_write(tmp_path, [{"type": "assistant", "message": "x"}]), SIZE) is None


def test_turn_done_is_true_with_the_time_only_after_an_ended_reply(tmp_path):
    ended = [PROMPT, _assistant("tool_use", _tool_use("t1")), _tool_result("t1"), _assistant("end_turn", TEXT), TAIL_ROW]
    done, at = observe.turn_done(_write(tmp_path, ended), SIZE)
    assert done is True and at is not None and at > 0
    assert observe.turn_done(_write(tmp_path, [*ended[:3], _assistant("stop_sequence", TEXT)]), SIZE)[0] is True
    # 最後が利用者の入力・tool_result・stop_reason の無い応答・道具の途中なら偽
    for rows in (
        [*ended, PROMPT],
        ended[:3],
        [*ended[:3], _assistant(None, TEXT)],
        ended[:2],
    ):
        assert observe.turn_done(_write(tmp_path, rows), SIZE) == (False, None)
    assert observe.turn_done(tmp_path / "missing.jsonl", SIZE) is None
    assert observe.turn_done(_write(tmp_path, [TAIL_ROW]), SIZE) is None


def test_a_broken_complete_line_is_unsure_but_a_cut_edge_is_skipped(tmp_path):
    ended = [PROMPT, _assistant("end_turn", TEXT)]
    # 改行で終わる壊れた行は、その前の応答で完了と読まない
    broken = _write(tmp_path, ended, tail='{"type": "user", "mess\n')
    assert observe.turn_done(broken, SIZE) is None and observe.tool_pending(broken, SIZE) is None
    # 範囲の始まりで切れた最初の行は飛ばす
    path = _write(tmp_path, [PROMPT, *ended])
    first = len(json.dumps(PROMPT, ensure_ascii=False).encode()) + 1
    assert observe.turn_done(path, path.stat().st_size - first + 5)[0] is True


def test_reading_stops_at_the_last_assistant(tmp_path):
    # 最後の assistant より前の行は読まない（壊れていても判定に効かない）
    path = _write(tmp_path, [], tail='{"broken\n' + "".join(json.dumps(r) + "\n" for r in [_assistant("end_turn", TEXT), TAIL_ROW]))
    assert observe.turn_done(path, SIZE)[0] is True and observe.tool_pending(path, SIZE) is False


def test_an_unreadable_record_is_none(tmp_path, monkeypatch):
    path = _write(tmp_path, [PROMPT, _assistant("end_turn", TEXT)])
    real = open

    def deny(file, *a, **k):
        if str(file) == str(path):
            raise PermissionError(file)
        return real(file, *a, **k)

    monkeypatch.setattr("builtins.open", deny)
    assert observe.turn_done(path, SIZE) is None and observe.tool_pending(path, SIZE) is None
