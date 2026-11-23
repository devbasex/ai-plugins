"""会話の記録の末尾から読む止めてよい時点の事実（`observe.tool_pending`・`turn_done`。#3864 の決定 11）。

記録の形は U1 の確かめ 3（PR #4018）に合わせる: 道具の実行中は会話の行の最後が `stop_reason: tool_use` の
`assistant` の `tool_use` のままで、終わると `user` の `tool_result` が同じ `tool_use_id` で続く。
ファイルの最後の行は会話の行でないことがある。
"""

from __future__ import annotations

import json
import os

import pytest

from ndf.domains.relay import observe

SIZE = 4096
AT = "2026-11-22T01:02:03.000Z"


def _assistant(stop, *blocks, at=AT, uuid=None):
    row = {"type": "assistant", "timestamp": at, "message": {"role": "assistant", "stop_reason": stop, "content": list(blocks)}}
    return row if uuid is None else {**row, "uuid": uuid}


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
    # 開けない記録は確かめた不一致（偽）と分けて None を返す（権限を直してもスタンプは変わらないので、呼ぶ側が読み直す）
    path = _write(tmp_path, [PROMPT, _api_error("authentication_failed", None)])
    path.chmod(0)
    try:
        assert observe.credential_failure(path, SIZE, SINCE) is None
    finally:
        path.chmod(0o600)
    assert observe.credential_failure(path, SIZE, SINCE) is True


# Claude Code 2.1 の TUI が上限で出したバイト列（2026-11-23 に `script -f` で記録した実物の抜粋。#4227）
TUI_LIMIT = (
    b";3H\x1b[?25h\x1b[?25l\x1b[H\r\x1b[7B\x1b[38;5;246m  \xe2\x8e\xbf \xc2\xa0\x1b[38;5;211mYou've hit your 5-hour limit"
    b" \xc2\xb7 resets 5:40am (UTC)\r\x1b[5C\x1b[1B\x1b[38;5;246m/usage-credits to finish what you\xe2\x80\x99re working on."
    b"\r\x1b[35B\x1b[39m\x1b[K\r\x1b[3B\xe2\x9d\xaf\xc2\xa0\r\x1b"
)


def test_4227_the_limit_line_drawn_by_cursor_moves_is_found():
    # 応答の行は改行でなくカーソルの移動で描かれる。生の出力のまま照らしても上限の行が当たる
    assert observe.limit_line(observe.plain(TUI_LIMIT)) == "You've hit your 5-hour limit · resets 5:40am (UTC)"


def test_4227_a_forward_move_keeps_words_apart():
    assert observe.plain(b"a\x1b[2Cb\x1b[3;1Hc") == "a b\nc"


# 背景のサブエージェントの会話の記録と背景のシェル（#4036）。形は 2026-11-23 に Claude Code 2.1.330 で実測した
SNAPSHOT = "/home/u/.claude/shell-snapshots/snapshot-bash-1-x.sh"
SHELL_ARGS = ["/bin/bash", "-c", f"source {SNAPSHOT} 2>/dev/null || true && eval 'sleep 45' < /dev/null"]


def test_4036_the_subagent_transcript_sits_beside_the_session(tmp_path):
    got = observe.subagent_transcript_path(tmp_path / "sid.jsonl", "a150335adf8797c51")
    assert got == tmp_path / "sid" / "subagents" / "agent-a150335adf8797c51.jsonl"
    assert observe.subagent_transcript_path(tmp_path / "sid.jsonl", "a-b_C9") == tmp_path / "sid" / "subagents" / "agent-a-b_C9.jsonl"


@pytest.mark.parametrize("agent_id", ["", "../x", "a/b", "a.b"])
def test_4036_an_unsafe_subagent_id_builds_no_path(tmp_path, agent_id):
    assert observe.subagent_transcript_path(tmp_path / "sid.jsonl", agent_id) is None


def test_4036_the_shell_output_path_follows_the_claude_temp_layout():
    got = observe.shell_output_path({}, observe.Path("/tmp/m.x/work"), "sid", "b7prporwe")
    assert got == observe.Path(f"/tmp/claude-{os.getuid()}/-tmp-m-x-work/sid/tasks/b7prporwe.output")
    moved = observe.shell_output_path({"CLAUDE_CODE_TMPDIR": "/t"}, observe.Path("/w"), "sid", "b1")
    assert moved == observe.Path(f"/t/claude-{os.getuid()}/-w/sid/tasks/b1.output")


def test_4036_an_unsafe_shell_id_builds_no_path():
    assert observe.shell_output_path({}, observe.Path("/w"), "sid", "../x") is None


def _proc(root, pid, args, ppid=1, comm=None):
    (root / str(pid)).mkdir(parents=True)
    (root / str(pid) / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")
    name = comm if comm is not None else observe.Path(args[0]).name
    (root / str(pid) / "stat").write_text(f"{pid} ({name}) S {ppid} {pid} {pid} 0", encoding="utf-8")


def test_4036_live_shells_counts_the_bash_tool_shells_under_claude(tmp_path):
    _proc(tmp_path, 10, ["ndf", "relay"])
    _proc(tmp_path, 11, ["claude", "--session-id", "s"], ppid=10)
    _proc(tmp_path, 12, SHELL_ARGS, ppid=11)
    _proc(tmp_path, 13, ["node", "mcp-server.js"], ppid=11)
    _proc(tmp_path, 14, ["bash", "-c", "echo x"], ppid=11)
    _proc(tmp_path, 15, SHELL_ARGS, ppid=99)  # 別の claude のシェルは数えない
    (tmp_path / "self").mkdir()  # 数字でない項目は読まない
    assert observe.live_shells(10, observe.process_parents(tmp_path), tmp_path) == 1


def test_4036_live_shells_counts_the_shells_of_an_account_config_dir(tmp_path):
    # 登録済みアカウントの子は NDF が渡す CLAUDE_CONFIG_DIR の直下に shell-snapshots を置く
    account = "/home/u/.ndf/accounts/a1/shell-snapshots/snapshot-bash-2-y.sh"
    _proc(tmp_path, 10, ["ndf", "relay"])
    _proc(tmp_path, 11, ["claude"], ppid=10)
    _proc(tmp_path, 12, ["/bin/bash", "-c", f"source {account} 2>/dev/null || true && eval 'sleep 45' < /dev/null"], ppid=11)
    _proc(tmp_path, 13, SHELL_ARGS, ppid=11)
    assert observe.live_shells(10, observe.process_parents(tmp_path), tmp_path) == 2


def test_4036_live_shells_reads_the_parent_past_a_command_name_with_parens(tmp_path):
    _proc(tmp_path, 10, ["ndf", "relay"], comm="a) S 7 (b")
    _proc(tmp_path, 11, ["claude"], ppid=10, comm="cl) 3 (x")
    _proc(tmp_path, 12, SHELL_ARGS, ppid=11)
    assert observe.live_shells(10, observe.process_parents(tmp_path), tmp_path) == 1


def test_4036_live_shells_is_zero_without_children(tmp_path):
    _proc(tmp_path, 10, ["ndf", "relay"])
    _proc(tmp_path, 11, ["claude"], ppid=10)
    assert observe.live_shells(10, observe.process_parents(tmp_path), tmp_path) == 0


def test_4036_live_shells_is_none_without_the_wrapper_or_claude(tmp_path):
    assert observe.live_shells(10, observe.process_parents(tmp_path), tmp_path) is None
    _proc(tmp_path, 10, ["ndf", "relay"])
    _proc(tmp_path, 11, ["tmux"], ppid=10)
    assert observe.live_shells(10, observe.process_parents(tmp_path), tmp_path) is None


def _raising(monkeypatch, method, target, exc):
    real = getattr(observe.Path, method)

    def fake(self, *a, **k):
        if self == target:
            raise exc
        return real(self, *a, **k)

    monkeypatch.setattr(observe.Path, method, fake)


def test_4036_process_parents_skips_a_pid_whose_stat_is_denied(tmp_path, monkeypatch):
    # hidepid=1 では他のユーザーの PID は見えても stat は読めない（proc(5)）。その PID だけを外して続ける
    _proc(tmp_path, 10, ["ndf", "relay"])
    _proc(tmp_path, 11, ["claude"], ppid=10)
    _proc(tmp_path, 12, SHELL_ARGS, ppid=11)
    _proc(tmp_path, 50, ["other"])
    _raising(monkeypatch, "read_text", tmp_path / "50" / "stat", PermissionError("denied"))
    parents = observe.process_parents(tmp_path)
    assert 50 not in parents
    assert observe.live_shells(10, parents, tmp_path) == 1


def test_4036_process_parents_is_none_without_proc(tmp_path):
    assert observe.process_parents(tmp_path / "missing") is None
    assert observe.live_shells(10, None, tmp_path) is None


def test_4036_live_shells_skips_a_shell_that_ends_while_cmdline_is_read(tmp_path, monkeypatch):
    # 開いた後に子が消えると cmdline の読み取りは ESRCH（ProcessLookupError）になる。終わった子として数えない
    _proc(tmp_path, 10, ["ndf", "relay"])
    _proc(tmp_path, 11, ["claude"], ppid=10)
    _proc(tmp_path, 12, SHELL_ARGS, ppid=11)
    _proc(tmp_path, 13, SHELL_ARGS, ppid=11)
    parents = observe.process_parents(tmp_path)
    _raising(monkeypatch, "read_bytes", tmp_path / "12" / "cmdline", ProcessLookupError("gone"))
    assert observe.live_shells(10, parents, tmp_path) == 1


def test_4036_live_shells_is_none_when_cmdline_cannot_be_read(tmp_path, monkeypatch):
    _proc(tmp_path, 10, ["ndf", "relay"])
    _proc(tmp_path, 11, ["claude"], ppid=10)
    _proc(tmp_path, 12, SHELL_ARGS, ppid=11)
    parents = observe.process_parents(tmp_path)
    _raising(monkeypatch, "read_bytes", tmp_path / "12" / "cmdline", PermissionError("denied"))
    assert observe.live_shells(10, parents, tmp_path) is None


# conductor の活動（#4036 の「活動を読む」。`ndf relay ls` の行に載る）。時刻はテストが決め、本物の時間を待たない
SID, CWD = "sid", observe.Path("/w")
T0 = "2026-11-23T00:00:00.500Z"  # conductor の最後の応答（ミリ秒を持つ）
T0_S = observe.datetime.fromisoformat("2026-11-23T00:00:00.500+00:00").timestamp()
LATER = "2026-11-23T00:01:00.000Z"
NOW = T0_S + 3600
REPLY = "r0"  # conductor の最後の応答の `uuid`


class _Lane:
    """1 本のレーンの材料（会話の記録・ラッパーの保存先・/proc）を一時ディレクトリに組む。"""

    def __init__(self, tmp_path):
        self.root = tmp_path
        self.environ = {"CLAUDE_CONFIG_DIR": str(tmp_path / "cfg"), "CLAUDE_CODE_TMPDIR": str(tmp_path / "t")}
        self.transcript = observe.transcript_path(self.environ, CWD, SID)
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_text(json.dumps(_assistant("end_turn", TEXT, at=T0, uuid=REPLY)) + "\n", encoding="utf-8")
        self.wrapper = tmp_path / "wrap"
        self.wrapper.mkdir()
        self.proc = tmp_path / "proc"
        self.proc.mkdir()

    def activity(self, tasks, at="2026-11-23T00:00:00+00:00", session=SID, reply=REPLY):
        record = {"at": at, "session_id": session, "reply": reply, "tasks": tasks}
        (self.wrapper / "activity.json").write_text(json.dumps(record), encoding="utf-8")

    def subagent(self, aid, *rows):
        path = observe.subagent_transcript_path(self.transcript, aid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def shells(self, live):
        _proc(self.proc, 10, ["ndf", "relay"])
        _proc(self.proc, 11, ["claude"], ppid=10)
        for n in range(live):
            _proc(self.proc, 20 + n, SHELL_ARGS, ppid=11)

    def output(self, task_id, mtime):
        path = observe.shell_output_path(self.environ, CWD, SID, task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
        os.utime(path, (mtime, mtime))

    def read(self, pid=10, now=NOW, **row):
        look = observe.Look(now, SIZE, lambda: observe.process_parents(self.proc), self.proc)
        paths = {"transcript": str(self.transcript), "shell_outputs": str(observe.shell_output_dir(self.environ, CWD, SID))}
        return observe.conductor_activity({"session_id": SID, "pid": pid, **paths, **row}, self.wrapper, look)


def _act(turn_done, moved, background):
    return {"turn_done": turn_done, "moved": moved, "background": background}


def test_4036_a_finished_subagent_is_left_out_and_moves_the_time(tmp_path):
    """lracct の形: 背景のサブエージェントが終わって通知を受けた後、conductor は何もしていない（決定 2）。"""
    lane = _Lane(tmp_path)
    lane.shells(0)
    lane.activity([{"id": "a1", "type": "subagent"}])
    lane.subagent("a1", _assistant("end_turn", TEXT, at=LATER))
    later = observe.datetime.fromisoformat("2026-11-23T00:01:00+00:00").timestamp()
    assert lane.read() == _act(True, later, 0)


@pytest.mark.parametrize(
    ("make", "count"),
    [
        (lambda lane: None, 1),  # 記録が無いサブエージェントは走っているとみなす
        (lambda lane: lane.subagent("a1", _assistant("tool_use", _tool_use("t1"), at=LATER)), 1),  # 応答の途中
    ],
    ids=["missing", "midway"],
)
def test_4036_a_subagent_that_cannot_be_read_or_runs_is_counted(tmp_path, make, count):
    lane = _Lane(tmp_path)
    lane.shells(0)
    lane.activity([{"id": "a1", "type": "subagent"}])
    make(lane)
    assert lane.read() == _act(True, T0_S, count)


def test_4036_an_unsafe_subagent_id_and_a_monitor_are_counted(tmp_path):
    lane = _Lane(tmp_path)
    lane.shells(0)
    lane.activity([{"id": "../x", "type": "subagent"}, {"id": "m1", "type": "monitor"}])
    assert lane.read() == _act(True, T0_S, 2)


def test_4036_no_tasks_and_no_live_shell_is_zero(tmp_path):
    lane = _Lane(tmp_path)
    lane.shells(0)
    lane.activity([])
    assert lane.read() == _act(True, T0_S, 0)


@pytest.mark.parametrize(("listed", "live", "count"), [(1, 1, 1), (0, 1, 1)], ids=["listed", "unlisted"])
def test_4036_live_shells_are_counted_even_when_not_listed(tmp_path, listed, live, count):
    lane = _Lane(tmp_path)
    lane.shells(live)
    lane.activity([{"id": "b1", "type": "shell"}] * listed)
    assert lane.read() == _act(True, T0_S, count)


def test_4036_a_finished_shell_moves_the_time_to_its_output(tmp_path):
    lane = _Lane(tmp_path)
    lane.shells(0)
    lane.activity([{"id": "b1", "type": "shell"}])
    lane.output("b1", T0_S + 120)
    assert lane.read() == _act(True, T0_S + 120, 0)


def test_4036_a_finished_shell_without_output_moves_the_time_to_now(tmp_path):
    lane = _Lane(tmp_path)
    lane.shells(0)
    lane.activity([{"id": "b1", "type": "shell"}])
    assert lane.read() == _act(True, NOW, 0)


def test_4036_a_finished_shell_without_output_keeps_the_time_it_was_first_seen(tmp_path):
    """出力のファイルが消えたシェルは、同じ Stop の記録の間は最初に終わりを確かめた時刻のまま（読み直すたびに 0 へ戻さない）。"""
    lane = _Lane(tmp_path)
    lane.shells(0)
    lane.activity([{"id": "b1", "type": "shell"}])
    assert lane.read(now=NOW)["moved"] == NOW
    assert lane.read(now=NOW + 900) == _act(True, NOW, 0)
    lane.activity([{"id": "b1", "type": "shell"}], at="2026-11-23T00:30:00+00:00")  # 次の Stop の記録では確かめ直す
    assert lane.read(now=NOW + 1800)["moved"] == NOW + 1800


def test_4036_a_second_shell_ending_is_seen_afresh(tmp_path):
    """終わったシェルの数が増えたら、前のシェルの終わりの時刻を使わない（長いシェルが終わった直後を止まりと読まない）。"""
    lane = _Lane(tmp_path)
    lane.shells(1)
    lane.activity([{"id": "b1", "type": "shell"}, {"id": "b2", "type": "shell"}])
    assert lane.read(now=NOW) == _act(True, NOW, 1)
    (lane.proc / "20").rename(lane.proc / "gone")
    assert lane.read(now=NOW + 900) == _act(True, NOW + 900, 0)


def test_4036_proc_is_not_scanned_while_the_conductor_is_answering(tmp_path):
    """シェルの生死を確かめる行に届かなければ /proc を読まない。"""
    lane = _Lane(tmp_path)
    lane.transcript.write_text(json.dumps(_assistant("tool_use", _tool_use("t1"), at=T0)) + "\n", encoding="utf-8")
    calls = []
    look = observe.Look(NOW, SIZE, lambda: calls.append(1), lane.proc)
    paths = {"transcript": str(lane.transcript), "shell_outputs": str(tmp_path)}
    assert observe.conductor_activity({"session_id": SID, "pid": 10, **paths}, lane.wrapper, look) == _act(False, None, None)
    assert calls == []


@pytest.mark.parametrize("pid", [None, "10", 99], ids=["no-pid", "not-int", "no-claude"])
def test_4036_shells_that_cannot_be_checked_leave_the_count_unknown(tmp_path, pid):
    """シェルの生死を確かめられなければ、一覧が空でも数を None にする（止まりの根拠にしない）。"""
    lane = _Lane(tmp_path)
    lane.shells(0)
    lane.activity([])
    assert lane.read(pid=pid) == _act(True, T0_S, None)


def test_4036_an_unreadable_proc_leaves_the_count_unknown_but_keeps_finished_subagents(tmp_path):
    lane = _Lane(tmp_path)
    lane.proc.rmdir()
    lane.activity([{"id": "a1", "type": "subagent"}])
    lane.subagent("a1", _assistant("end_turn", TEXT, at=LATER))
    later = observe.datetime.fromisoformat("2026-11-23T00:01:00+00:00").timestamp()
    assert lane.read() == _act(True, later, None)


@pytest.mark.parametrize(
    "write",
    [
        lambda lane: None,
        lambda lane: lane.activity([], session="other"),
        lambda lane: lane.activity([], reply="r-prev"),
        lambda lane: lane.activity([], reply=None),
        lambda lane: lane.activity(None),
    ],
    ids=["none", "other-session", "previous-reply", "no-reply", "tasks-null"],
)
def test_4036_an_activity_record_not_for_this_turn_leaves_the_count_unknown(tmp_path, write):
    """前の Stop の空の一覧・別のセッション・観測できなかった一覧は使わない。"""
    lane = _Lane(tmp_path)
    lane.shells(0)
    write(lane)
    assert lane.read() == _act(True, T0_S, None)


def test_4036_a_previous_stop_in_the_same_second_is_not_used(tmp_path):
    """前の Stop の記録が今の応答と同じ秒に書かれていても、応答の `uuid` が違えば使わない（時刻で照らさない）。"""
    lane = _Lane(tmp_path)
    lane.shells(0)
    lane.activity([], at="2026-11-23T00:00:00+00:00", reply="r-prev")
    assert lane.read()["background"] is None
    lane.activity([], at="2026-11-23T00:00:00+00:00")
    assert lane.read()["background"] == 0


def test_4036_reply_id_is_the_uuid_of_the_last_assistant(tmp_path):
    path = _write(tmp_path, [_assistant("tool_use", _tool_use("t1"), uuid="a1"), _tool_result("t1")])
    assert observe.reply_id(path, SIZE) == "a1"
    assert observe.reply_id(_write(tmp_path, [_assistant("end_turn", TEXT)]), SIZE) is None
    assert observe.reply_id(tmp_path / "none.jsonl", SIZE) is None


@pytest.mark.parametrize(
    "row", [{"session_id": None}, {"transcript": None}, {"shell_outputs": None}], ids=["no-session", "no-transcript", "no-outputs"]
)
def test_4036_a_row_without_its_session_has_no_activity(tmp_path, row):
    lane = _Lane(tmp_path)
    assert lane.read(**row) == _act(None, None, None)


def test_4036_an_unreadable_conductor_transcript_has_no_activity(tmp_path):
    lane = _Lane(tmp_path)
    lane.transcript.unlink()
    lane.activity([])
    assert lane.read() == _act(None, None, None)
