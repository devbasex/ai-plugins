"""会話の記録の末尾から読む止めてよい時点の事実（`observe.tool_pending`・`turn_done`。#3864 の決定 11）。

記録の形は U1 の確かめ 3（PR #4018）に合わせる: 道具の実行中は会話の行の最後が `stop_reason: tool_use` の
`assistant` の `tool_use` のままで、終わると `user` の `tool_result` が同じ `tool_use_id` で続く。
ファイルの最後の行は会話の行でないことがある。
"""

from __future__ import annotations

import json

import pytest

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


def test_the_first_complete_line_after_a_cut_edge_is_read_alone(tmp_path):
    # 切れた断片の直後の完全な行が最後の応答でも、断片と混ぜずに読む
    reply = _assistant("end_turn", TEXT)
    path = _write(tmp_path, [PROMPT, reply, TAIL_ROW])
    tail = len(json.dumps(reply, ensure_ascii=False).encode()) + len(json.dumps(TAIL_ROW).encode()) + 2
    assert observe.turn_done(path, tail + 5)[0] is True
    assert observe.tool_pending(path, tail + 5) is False


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


# ---------------------------------------------------------------- 認証の失敗の確かめ（U10。#3864 の決定 6・判断 13）

SINCE = 1_000.0  # セグメントを起動した時刻（AT より前）
LATER = 4_102_444_800.0  # AT より後（前のセグメントの失敗を読まないかの確かめ）


def _api_error(error, status, text="Failed to authenticate: OAuth session expired and could not be refreshed"):
    """claude が書く API の失敗の記録（実測の形。`error`・`apiErrorStatus` は記録の最上位の欄）。"""
    row = {
        "type": "assistant",
        "timestamp": AT,
        "isApiErrorMessage": True,
        "error": error,
        "message": _assistant(None, {"type": "text", "text": text})["message"],
    }
    if status is not None:
        row["apiErrorStatus"] = status
    return row


RETRY_401 = {"type": "system", "subtype": "api_error", "timestamp": AT, "error": {"status": 401, "message": "authentication_error"}}


@pytest.mark.parametrize(
    "line",
    [
        "⎿  Invalid API key · Fix external API key",
        "● Login expired · Please run /login",
        "Not logged in · run /login",
        "Failed to authenticate: this login reached its time limit and cannot be renewed.",
    ],
)
def test_3864_6_the_four_measured_lines_are_triggers(line):
    # conductor の決定: 引き金は Failed to authenticate・Invalid API key・Login expired・Not logged in の 4 つ（大文字小文字を問わない）
    assert observe.auth_failed(observe.plain(f"\x1b[31m{line}\x1b[0m".encode()))
    assert observe.auth_failed(line.upper())


@pytest.mark.parametrize("line", ["401 invalid x-api-key · retrying in 3s · attempt 3/10", "all good"])
def test_3864_6_the_retry_line_of_an_api_key_is_not_a_trigger(line):
    # API キーの打ち直しの間の行は引き金にしない（打ち直しが尽きた後の Invalid API key を待つ）
    assert not observe.auth_failed(line)


@pytest.mark.parametrize(
    "rows",
    [
        [PROMPT, _api_error("authentication_failed", None), TAIL_ROW],  # OAuth の期限切れ（apiErrorStatus が無い）
        [PROMPT, _api_error("authentication_failed", 401, "Invalid API key · Fix external API key"), TAIL_ROW],  # API キー
        [PROMPT, _api_error("unknown", 401)],  # apiErrorStatus が 401 だけ
        [PROMPT, RETRY_401, _api_error("authentication_failed", None), TAIL_ROW, {"type": "cost-state"}],
    ],
    ids=["oauth", "api-key", "status-401-only", "after-retries"],
)
def test_3864_6_a_credential_failure_is_the_last_conversation_row(tmp_path, rows):
    # 判定は会話の行の最後の assistant の記録だけ。ファイルの最後の会話でない行は飛ばす
    assert observe.credential_failure(_write(tmp_path, rows), SIZE, SINCE) is True


@pytest.mark.parametrize(
    "rows, since",
    [
        ([PROMPT, _api_error("rate_limit", 429)], SINCE),  # 上限は守り 3 が扱う
        ([PROMPT, _api_error("billing_error", 400)], SINCE),  # 残高不足
        ([PROMPT, _api_error("permission_error", 403)], SINCE),  # 権限の制限は替えない
        ([PROMPT, RETRY_401, _api_error("permission_error", 403), TAIL_ROW], SINCE),  # 打ち直しの 401 は読まない
        ([PROMPT, _api_error("authentication_failed", None), PROMPT], SINCE),  # 後ろに利用者の記録がある
        ([PROMPT, _api_error("authentication_failed", None)], LATER),  # セグメントの起動より前の失敗
        ([PROMPT, _assistant("end_turn", {"type": "text", "text": "API Error: 401 · Invalid API key"})], SINCE),  # 応答の引用
        ([{**_api_error("authentication_failed", 401), "isApiErrorMessage": False}], SINCE),  # API の失敗の記録でない
    ],
    ids=["429", "400", "403", "retry-401-then-403", "user-after", "older", "quote", "not-api-error"],
)
def test_3864_6_other_failures_and_quotes_are_not_a_credential_failure(tmp_path, rows, since):
    assert observe.credential_failure(_write(tmp_path, rows), SIZE, since) is False


def test_3864_6_an_unsure_record_is_not_a_credential_failure(tmp_path):
    # 確かめられない（無い・壊れた行・形が違う）ときは何もしない
    assert observe.credential_failure(tmp_path / "missing.jsonl", SIZE, SINCE) is False
    broken = _write(tmp_path, [PROMPT, _api_error("authentication_failed", None)], tail='{"type": "user", "mess\n')
    assert observe.credential_failure(broken, SIZE, SINCE) is False
    assert observe.credential_failure(_write(tmp_path, [{"type": "assistant", "message": "x"}]), SIZE, SINCE) is False


def test_3864_6_an_unreadable_record_is_none_so_it_is_read_again(tmp_path):
    # 開けない記録は照合した不一致（偽）と分けて None を返す（権限を直してもスタンプは変わらないので、呼ぶ側が読み直す）
    path = _write(tmp_path, [PROMPT, _api_error("authentication_failed", None)])
    path.chmod(0)
    try:
        assert observe.credential_failure(path, SIZE, SINCE) is None
    finally:
        path.chmod(0o600)
    assert observe.credential_failure(path, SIZE, SINCE) is True
