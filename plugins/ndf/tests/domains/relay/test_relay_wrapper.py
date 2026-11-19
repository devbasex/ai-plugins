"""ラッパーの見回り: セグメントの切り替え・上限でのアカウントの替え・認証の失敗・バージョンの混ざり（#2438・#3337・#3243・3341-2）。

子は擬似端末の代わりにパイプを持つ偽物で、時計は呼ぶたびに 1 秒進む偽物である（擬似端末も壁時計も待たない。#2962）。
"""

from __future__ import annotations

import fcntl
import io
import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from ndf.domains.relay import accounts, install, markers, registry, signals, stop_signal, wrapper
from ndf.domains.relay.account_decide import Usage
from ndf.domains.relay.plugin_update import Target, Updated

VERSION = "11.0.0-dev.4"


class FakeChild:
    """`outputs` を順に画面へ出し、出し切ったら `exit_code` で終わる（None なら生き続ける）。"""

    def __init__(self, outputs: list[bytes], exit_code: int | None):
        self.read_fd, self.write_fd = os.pipe()
        self.fd = self.read_fd
        self.outputs = list(outputs)
        self.exit_code = exit_code
        self.alive = True
        self.exitstatus = None
        self.written: list[bytes] = []

    def isalive(self):
        if self.outputs:
            os.write(self.write_fd, self.outputs.pop(0))
        elif self.alive and self.exit_code is not None:
            self.alive, self.exitstatus = False, self.exit_code
        return self.alive

    def write(self, data):
        self.written.append(data)

    def terminate(self, force=False):
        self.alive, self.exitstatus = False, -9

    def close(self):
        os.close(self.read_fd)
        os.close(self.write_fd)


class Spawner:
    """`markers` は起動の順番 → その子のセッションが Stop の hook で書く次のセグメントのブロック。"""

    def __init__(self, children, markers=None):
        self.children = list(children)
        self.markers = dict(markers or {})
        self.calls: list[tuple[list[str], dict]] = []
        self.made: list[FakeChild] = []

    def __call__(self, argv, env, cwd):
        self.calls.append((argv, dict(env)))
        child = self.children.pop(0)
        block = self.markers.get(len(self.made))
        if block is not None:
            signals.write_next(Path(env["NDF_RELAY_DIR"]), argv[2], block, True, 0.1)
        self.made.append(child)
        return child


def _clock():
    now = [0.0]

    def tick():
        now[0] += 1.0
        return now[0]

    return tick


def _settings(tmp_path: Path, **changes) -> wrapper.Settings:
    values = dict(
        state_dir=tmp_path / "state",
        version=VERSION,
        name="t1",
        cwd=tmp_path,
        # 偽の設定の保存先（メジャーバージョンで止まるときに本物の ~/.claude/ndf の複製を向け直さない）
        environ={"NDF_STATE_HOME": str(tmp_path / "old"), "CLAUDE_CONFIG_DIR": str(tmp_path / "claude")},
        args=("--model", "x"),
        tick=0.0,
        quiet=2.0,
        stall=1000.0,
        max_starts=3,
        context_limit=800_000,
        usage_threshold=90.0,
        transcript_bytes=4096,
    )
    values.update(changes)
    return wrapper.Settings(**values)


def _run(tmp_path, children, prepare=None, markers=None, wall=None, **changes):
    s = _settings(tmp_path, **changes)
    spawn = Spawner(children, markers)
    w = wrapper.Wrapper(s, spawn=spawn, clock=_clock(), wall=wall)
    if prepare:
        prepare(w.dir, s.state_dir)
    try:
        ended = w.run("最初の 1 行")
    finally:
        for c in spawn.made + spawn.children:
            c.close()
    return ended, spawn, w


def _log(w) -> list[dict]:
    return [json.loads(line) for line in (w.dir / registry.LOG_FILE).read_text(encoding="utf-8").splitlines()]


def test_next_block_starts_a_new_session_with_the_block_and_a_relay_mark(tmp_path):
    def prepare(d, state):
        accounts.write_usages(state, [Usage("team", 95.0, False), Usage("main", 10.0, False)])
        accounts.use(state, "team")

    ended, spawn, w = _run(tmp_path, [FakeChild([b"working\r\n"], None), FakeChild([], 0)], prepare, markers={0: "/goal 次のセグメント"})
    assert ended.cause == "" and "書かずに終わった" in ended.reason
    (first, first_env), (second, second_env) = spawn.calls
    assert first[:2] == [wrapper.CLAUDE, "--session-id"] and first[3:] == ["--model", "x", "最初の 1 行"]
    assert second[0:2] == [wrapper.CLAUDE, "--session-id"] and second[-1] == "/goal 次のセグメント" and second[2] != first[2]
    # カットポイントで使用率が閾値を超えていれば、空いたアカウントで次のセグメントを始める（#3336）
    assert (first_env["NDF_CLAUDE_ACCOUNT"], second_env["NDF_CLAUDE_ACCOUNT"]) == ("team", "main")
    assert first_env["NDF_RELAY_DIR"] == str(w.dir) and first_env["CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION"] == "false"
    assert markers.take(tmp_path / "state", second[2]) is True
    assert signals.read_next(w.dir) is None
    assert spawn.made[0].alive is False
    kinds = [row["kind"] for row in _log(w)]
    assert kinds.count("section") == 2 and "account" in kinds and kinds[-1] == "end"
    assert registry.read_state(w.dir)["state"] == "stopped"


def test_usage_limit_resumes_on_another_account_even_with_background_waits(tmp_path):
    # 背景の待ちを残したまま上限に当たっても、替える先で --resume して続ける（#3337）
    def prepare(d, state):
        accounts.write_usages(state, [Usage("team", 100.0, True), Usage("main", 5.0, False)])
        accounts.use(state, "team")

    limited = FakeChild([b"  \xe2\x8e\xbf  You've hit your individual spend limit \xc2\xb7 resets 10:40pm\r\n"], None)
    ended, spawn, w = _run(tmp_path, [limited, FakeChild([], 0)], prepare)
    (first, _), (second, env) = spawn.calls
    assert second[:3] == [wrapper.CLAUDE, "--resume", first[2]] and second[-1] == "利用上限でアカウントを替えた。中断したところから続ける"
    assert env["NDF_CLAUDE_ACCOUNT"] == "main"
    assert ended.cause == ""


def test_auth_failure_marks_the_lane_stalled(tmp_path, monkeypatch):
    # 認証の失敗で /goal が外れたら、状態を stalled にして理由を残す（#3243）
    child = FakeChild([b"Goal cleared after an unrecoverable error (authentication failed)\r\n", b"", b""], 0)
    seen = []
    real = registry.write_state
    monkeypatch.setattr(registry, "write_state", lambda d, data: (seen.append(data["state"]), real(d, data)))
    ended, _, _ = _run(tmp_path, [child])
    assert "stalled" in seen and ended.cause == ""


def test_a_second_wrapper_with_the_same_name_is_refused(tmp_path):
    d = registry.wrapper_dir(tmp_path / "state", "t1")
    d.mkdir(parents=True)
    fd = os.open(d / registry.LOCK_FILE, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        ended, spawn, _ = _run(tmp_path, [])
    finally:
        os.close(fd)
    assert ended.cause == "refused" and spawn.calls == []


def test_old_ndf_wrapper_running_stops_the_new_wrapper(tmp_path):
    # 旧いバージョンのラッパーが動いていれば、新しいバージョンのラッパーは動かずに止まる（3341-2）
    old = tmp_path / "old" / "relay" / "20261113T1"
    old.mkdir(parents=True)
    fd = os.open(old / registry.LOCK_FILE, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        ended, spawn, _ = _run(tmp_path, [])
    finally:
        os.close(fd)
    assert ended.cause == "precondition" and "旧い NDF" in ended.reason and spawn.calls == []


def test_crashed_child_is_resumed_until_the_daily_limit(tmp_path):
    children = [FakeChild([], 1), FakeChild([], 1), FakeChild([], 1)]
    ended, spawn, _ = _run(tmp_path, children, max_starts=2)
    assert ended.cause == "work_stopped"
    assert [argv[1] for argv, _ in spawn.calls] == ["--session-id", "--resume", "--resume"]


class StopOnSpawn(Spawner):
    """子を起動したすぐ後に停止シグナルファイルを置く（`ndf relay stop --all` が別の端末から置いた形）。"""

    def __call__(self, argv, env, cwd):
        child = super().__call__(argv, env, cwd)
        stop_signal.location_stop(Path(env["NDF_RELAY_DIR"]))
        return child


def test_stop_signal_lets_the_session_finish_and_starts_no_next_section(tmp_path):
    stale = registry.wrapper_dir(tmp_path / "state", "t1")
    stale.mkdir(parents=True)
    stop_signal.location_stop(stale)  # 前の実行の残りは始めたばかりの実行を止めない
    s = _settings(tmp_path)
    # 出力の後に黙る見回り（b""）を置き、子が生きたまま静かな時間に入って次のセグメントを保留する形にする
    child = FakeChild([b"a\r\n", b"", b"", b"", b"", b"", b""], 0)
    spawn = StopOnSpawn([child], {0: "/goal 次のセグメント"})
    out = io.BytesIO()
    w = wrapper.Wrapper(s, spawn=spawn, clock=_clock(), out=out)
    try:
        ended = w.run("最初の 1 行")
    finally:
        child.close()
    assert (ended.cause, ended.reason) == ("", wrapper.STOP_REASON)
    assert len(spawn.calls) == 1 and child.exitstatus == 0 and child.written == []  # 替えず・入力も送らず・殺さない
    assert out.getvalue().decode().count(wrapper.HALT_LINE) == 1  # 案内の 1 行は保留の間に一度だけ出る
    assert not stop_signal.has_stop(w.dir)


def test_broken_next_mark_stops_with_the_reason(tmp_path):
    def prepare(d, state):
        d.mkdir(parents=True)
        (d / signals.NEXT_FILE).write_text('{"block": "/goal"}', encoding="utf-8")

    ended, spawn, _ = _run(tmp_path, [FakeChild([], None)], prepare)
    assert ended.cause == "config_broken" and spawn.made[0].alive is False


# 現状固定（I-004）: その日の起動回数は日付が変わると 0 回から数え直し、状態に今日の日付で書く
def test_starts_from_another_day_are_counted_from_zero(tmp_path):
    s = _settings(tmp_path, max_starts=2)
    spawn = Spawner([FakeChild([], 1), FakeChild([], 1), FakeChild([], 1)])
    w = wrapper.Wrapper(s, spawn=spawn, clock=_clock())
    assert w.starts == {"date": date.today().isoformat(), "count": 0}
    w.starts = {"date": "2000-01-01", "count": 5}
    try:
        ended = w.run("最初の 1 行")
    finally:
        for c in spawn.made + spawn.children:
            c.close()
    assert ended.cause == "work_stopped"
    assert [argv[1] for argv, _ in spawn.calls] == ["--session-id", "--resume", "--resume"]
    assert registry.read_state(w.dir)["starts"] == {"date": date.today().isoformat(), "count": 2}


# 現状固定（I-013）: ラッパーが自分で読む事実（プロセスの番号・壁時計・使用率のキャッシュの時刻）
LIMIT_LINE = b"  \xe2\x8e\xbf  You've hit your individual spend limit \xc2\xb7 resets 10:40pm\r\n"


def test_state_file_records_the_wrapper_and_the_section(tmp_path):
    ended, spawn, w = _run(tmp_path, [FakeChild([], 0)])
    (argv, env) = spawn.calls[0]
    got = registry.read_state(w.dir)
    assert got["at"] and isinstance(got["at"], str)
    del got["at"]
    assert got == {
        "name": "t1",
        "version": VERSION,
        "pid": os.getpid(),
        "cwd": str(tmp_path),
        "state": "stopped",
        "reason": ended.reason,
        "section": 1,
        "session_id": argv[2],
        "account": None,
        "starts": {"date": date.today().isoformat(), "count": 0},
    }
    assert "NDF_CLAUDE_ACCOUNT" not in env
    rows = _log(w)
    assert [r["kind"] for r in rows] == ["section", "finish", "end"]
    assert rows[0] == {"at": rows[0]["at"], "section": 1, "kind": "section", "session_id": argv[2], "account": None, "resume": False}
    assert rows[-1] == {"at": rows[-1]["at"], "section": 1, "kind": "end", "cause": "", "reason": ended.reason}


def _limited_run(tmp_path, mtime_offset):
    def prepare(d, state):
        accounts.write_usages(state, [Usage("team", 50.0, False), Usage("main", 5.0, False)])
        accounts.use(state, "team")
        at = time.time() + mtime_offset
        os.utime(accounts.usage_path(state), (at, at))

    return _run(tmp_path, [FakeChild([LIMIT_LINE], None), FakeChild([], 0)], prepare)


def test_usage_measured_before_the_limit_line_does_not_free_the_current_account(tmp_path):
    ended, spawn, w = _limited_run(tmp_path, -3600)
    (first, _), (second, env) = spawn.calls
    assert second[:3] == [wrapper.CLAUDE, "--resume", first[2]]
    assert env["NDF_CLAUDE_ACCOUNT"] == "main" and ended.cause == ""
    assert registry.read_state(w.dir)["starts"]["count"] == 1


def test_usage_measured_after_the_limit_line_resumes_on_the_same_account(tmp_path):
    ended, spawn, w = _limited_run(tmp_path, 3600)
    (first, _), (second, env) = spawn.calls
    assert second[:3] == [wrapper.CLAUDE, "--resume", first[2]]
    assert env["NDF_CLAUDE_ACCOUNT"] == "team" and ended.cause == ""
    reasons = [r["reason"] for r in _log(w) if r["kind"] == "account"]
    assert reasons == ["team の上限が解けた。同じアカウントで再開する"]


def test_a_mark_left_by_an_earlier_run_is_dropped_not_used(tmp_path):
    # 前の実行のマーカー（セッションが違う）で、新しい子を終わらせて置き換えない
    def prepare(d, state):
        d.mkdir(parents=True)
        signals.write_next(d, "old-session", "/goal 古いセグメント", True, 0.1)

    ended, spawn, w = _run(tmp_path, [FakeChild([], 0)], prepare)
    assert len(spawn.calls) == 1 and ended.cause == "" and "書かずに終わった" in ended.reason
    assert signals.read_next(w.dir) is None
    assert "stale_next" in [row["kind"] for row in _log(w)]


def test_next_mark_switches_once_its_quiet_time_has_passed_even_while_output_continues(tmp_path):
    # /goal の未達の応答が続いて画面が静かにならなくても、マーカーを最初に書いてから静かな時間が過ぎれば切り替える（#2438）
    chatty = FakeChild([b"goal not met\r\n"] * 50, None)
    later = wrapper.WallClock(now=lambda: time.time() + 60.0)
    ended, spawn, _ = _run(tmp_path, [chatty, FakeChild([], 0)], markers={0: "/goal 次"}, wall=later, quiet=30.0)
    assert len(spawn.calls) == 2 and spawn.calls[1][0][-1] == "/goal 次"
    assert chatty.outputs and ended.cause == ""


class EndedPty(FakeChild):
    """終わった擬似端末: 読むと EIO を返し、select には読める状態を返し続ける。"""

    def __init__(self):
        super().__init__([], 1)
        os.write(self.write_fd, b"x")


def test_an_ended_pty_is_not_read_again_while_waiting(tmp_path, monkeypatch):
    # 子が終わった後の待ち（上限のリセットなど）で、終わった擬似端末を読み続けない
    child = EndedPty()
    reads = []
    real_read = os.read

    def read(fd, size):
        if fd == child.fd:
            reads.append(fd)
            raise OSError(5, "EIO")
        return real_read(fd, size)

    monkeypatch.setattr(wrapper.os, "read", read)
    w = wrapper.Wrapper(_settings(tmp_path), spawn=Spawner([child]), clock=_clock())
    try:
        w._start(wrapper.Launch(1, "s", None, "", resume=False))
        for _ in range(5):
            w._pump(0.0)
    finally:
        child.close()
    assert len(reads) == 1 and w.section.eof is True


def test_question_that_cannot_reach_the_plan_keeps_the_reason(tmp_path, monkeypatch):
    # 計画のリクエストへ載せられない質問は、理由を状態と log.jsonl に残してから消す（#3035）
    asked = []

    def ask_plan(path):
        asked.append(json.loads(path.read_text(encoding="utf-8"))["questions"])
        return "サブコマンド lanes question の結果を読めない（終了コード 2）: 壊れた"

    def prepare(d, state):
        d.mkdir(parents=True, exist_ok=True)
        signals.write_question(d / signals.QUESTION_FILE, "s1", {"tool_input": {"questions": [{"question": "q"}]}}, 0.1)

    states = []
    real = wrapper.Wrapper._write_state

    def spy(self, state, reason):
        states.append((state, reason))
        return real(self, state, reason)

    monkeypatch.setattr(wrapper.Wrapper, "_write_state", spy)
    ended, _, w = _run(tmp_path, [FakeChild([b"q\r\n"], 0)], prepare, ask_plan=ask_plan)
    assert asked == [[{"question": "q"}]] and ended.cause == ""
    assert not signals.has_question(w.dir)
    assert any(state == "waiting" and "計画のリクエストへ載せられない" in reason for state, reason in states)
    assert [(r["ok"], "壊れた" in r["reason"]) for r in _log(w) if r["kind"] == "plan_question"] == [(False, True)]


def test_first_only_args_and_resume_stay_on_the_first_section(tmp_path):
    # 最初のセグメントだけ --resume <ID> と -n で続け、次のセグメントは新しい ID とすべてのセッションの引数だけで始める
    ended, spawn, w = _run(
        tmp_path,
        [FakeChild([b"working\r\n"], None), FakeChild([], 0)],
        markers={0: "/goal 次"},
        first_args=("-n", "作業"),
        first_session="11111111-1111-1111-1111-111111111111",
        first_resume=True,
    )
    (first, _), (second, _) = spawn.calls
    assert first == [wrapper.CLAUDE, "--resume", "11111111-1111-1111-1111-111111111111", "--model", "x", "-n", "作業", "最初の 1 行"]
    assert second[1] == "--session-id" and second[2] != first[2] and second[3:] == ["--model", "x", "/goal 次"]


# --- プラグインの更新と起動先の確認（#3812） ---


class Hands:
    """更新の手・確認の手・起動を、呼ばれた順に `order` へ残す偽物。"""

    def __init__(self, updated=None, targets=None):
        self.order: list[str] = []
        self.updated = list(updated or [])
        self.targets = list(targets or [])

    def update(self):
        self.order.append("update")
        return self.updated.pop(0)

    def confirm(self):
        self.order.append("confirm")
        return self.targets.pop(0)

    def spawner(self, children, markers=None):
        hands = self

        class Ordered(Spawner):
            def __call__(self, argv, env, cwd):
                hands.order.append("spawn")
                return super().__call__(argv, env, cwd)

        return Ordered(children, markers)


def _same(result="up_to_date", reason="新しいバージョンは無い"):
    return Updated(result, VERSION, VERSION, reason, Target(VERSION, Path("/cache/ndf") / VERSION))


def _run_hands(tmp_path, hands, children, markers=None, **changes):
    s = _settings(tmp_path, update=hands.update, confirm=hands.confirm, **changes)
    spawn = hands.spawner(children, markers)
    out = io.BytesIO()
    w = wrapper.Wrapper(s, spawn=spawn, clock=_clock(), out=out)
    try:
        ended = w.run("最初の 1 行")
    finally:
        for c in spawn.made + spawn.children:
            c.close()
    return ended, spawn, w, out.getvalue().decode()


def _updates(w) -> list[dict]:
    return [r for r in _log(w) if r["kind"] == "plugin_update"]


def test_i1_update_runs_before_the_first_section_and_after_each_switch(tmp_path):
    # 受け入れ条件 1・2: 新しいセッションで始めるセグメントは、更新の後にだけ起動する
    hands = Hands(updated=[_same(), _same("updated", "11.0.0 から 11.0.0-dev.4 へ上げた")])
    ended, _, w, _ = _run_hands(tmp_path, hands, [FakeChild([b"working\r\n"], None), FakeChild([], 0)], markers={0: "/goal 次"})
    assert hands.order == ["update", "spawn", "update", "spawn"]
    assert ended.cause == ""
    rows = _updates(w)
    assert [r["result"] for r in rows] == ["up_to_date", "updated"]
    assert set(rows[0]) == {"at", "section", "kind", "result", "from", "to", "launched", "reason", "seconds"}
    assert rows[0]["section"] is None and rows[0]["launched"] == VERSION and isinstance(rows[0]["seconds"], float)
    assert "/cache" not in json.dumps(rows, ensure_ascii=False)


def test_i3_hands_that_continue_the_same_conversation_only_confirm(tmp_path):
    # 受け入れ条件 4: 子の終了から起動し直す手は更新せず、起動先だけを確かめる
    hands = Hands(updated=[_same()], targets=[Target(VERSION, None)])
    _, _, w, _ = _run_hands(tmp_path, hands, [FakeChild([], 1), FakeChild([], 0)])
    assert hands.order == ["update", "spawn", "confirm", "spawn"]
    assert [(r["result"], r["reason"], r["launched"]) for r in _updates(w)][1] == ("skipped", wrapper.NO_UPDATE_REASON, VERSION)


def test_i3_account_switch_on_the_limit_only_confirms(tmp_path):
    def prepare(d, state):
        accounts.write_usages(state, [Usage("team", 100.0, True), Usage("main", 5.0, False)])
        accounts.use(state, "team")

    hands = Hands(updated=[_same()], targets=[Target(VERSION, None)])
    s = _settings(tmp_path, update=hands.update, confirm=hands.confirm)
    spawn = hands.spawner([FakeChild([LIMIT_LINE], None), FakeChild([], 0)])
    w = wrapper.Wrapper(s, spawn=spawn, clock=_clock())
    prepare(w.dir, s.state_dir)
    try:
        w.run("最初の 1 行")
    finally:
        for c in spawn.made + spawn.children:
            c.close()
    assert hands.order == ["update", "spawn", "confirm", "spawn"] and spawn.calls[1][0][1] == "--resume"


def test_i3_a_first_section_resumed_by_the_user_only_confirms(tmp_path):
    hands = Hands(targets=[Target(VERSION, None)])
    _run_hands(tmp_path, hands, [FakeChild([], 0)], first_session="11111111-1111-1111-1111-111111111111", first_resume=True)
    assert hands.order == ["confirm", "spawn"]


def test_without_hands_no_plugin_update_row_is_written(tmp_path):
    _, _, w = _run(tmp_path, [FakeChild([], 0)])
    assert _updates(w) == []


def test_i2_failed_update_still_starts_the_next_section_with_one_line(tmp_path):
    # 受け入れ条件 3: 失敗しても止まらず、記録と端末の 1 行を出す
    hands = Hands(updated=[Updated("failed", None, None, "マーケットプレイスを更新できない: network")])
    ended, spawn, w, out = _run_hands(tmp_path, hands, [FakeChild([], 0)])
    assert hands.order == ["update", "spawn"] and ended.cause == ""
    assert [(r["result"], r["launched"]) for r in _updates(w)] == [("failed", None)]
    line = wrapper.UPDATE_FAILED_LINE.format(reason="マーケットプレイスを更新できない: network", launched="不明なバージョン")
    assert out.count("ndf-relay:") == 1 and line in out


def test_i7_another_major_stops_before_the_next_child_even_when_the_update_failed(tmp_path):
    # 受け入れ条件 11・I8: 起動先は更新の to ではなく確かめた target で判定する
    target = Target("12.0.0", tmp_path / "cache" / "12.0.0")
    hands = Hands(updated=[Updated("failed", None, None, "NDF を更新できない: 終了コード 0・JSON の行が無い", target)])
    ended, spawn, w, out = _run_hands(tmp_path, hands, [FakeChild([], 0)])
    assert ended.cause == "precondition" and spawn.calls == []
    assert wrapper.MAJOR_CHANGED_INSTALL_LINE.format(own=VERSION, launched="12.0.0", root=target.root) in out
    assert registry.read_state(w.dir)["state"] == "stopped"


def test_i7_another_major_stops_a_hand_that_does_not_update(tmp_path):
    hands = Hands(updated=[_same()], targets=[Target("12.0.0", tmp_path / "cache" / "12.0.0")])
    ended, spawn, _, _ = _run_hands(tmp_path, hands, [FakeChild([], 1), FakeChild([], 0)])
    assert ended.cause == "precondition" and hands.order == ["update", "spawn", "confirm"] and len(spawn.calls) == 1


def test_i7_same_major_or_unknown_target_starts_the_next_child(tmp_path):
    hands = Hands(updated=[_same()], targets=[Target("11.9.0", None), Target(None, None)])
    ended, spawn, w, _ = _run_hands(tmp_path, hands, [FakeChild([], 1), FakeChild([], 1), FakeChild([], 0)])
    assert len(spawn.calls) == 3 and ended.cause == ""
    assert [r["launched"] for r in _updates(w)] == [VERSION, "11.9.0", None]


def test_i7_stop_points_the_relay_copy_at_the_target_and_the_copy_calls_it(tmp_path):
    # 停止から打ち直しまで: relay.root が起動先を指し、複製は起動先の bin/ndf を呼ぶ
    def plugin(version):
        root = tmp_path / "cache" / version
        ndf = root / "bin" / "ndf"
        ndf.parent.mkdir(parents=True)
        ndf.write_text(f'#!/bin/sh\necho "{version} $*"\n', encoding="utf-8")
        ndf.chmod(0o755)
        return root

    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude"), "XDG_BIN_HOME": str(tmp_path / "bin"), "PATH": str(tmp_path / "bin")}
    got = install.install(env, plugin(VERSION), 9, replace_foreign=False, version=VERSION)
    new = plugin("12.0.0")
    hands = Hands(updated=[Updated("updated", VERSION, "12.0.0", "上げた", Target("12.0.0", new))])
    ended, spawn, _, out = _run_hands(tmp_path, hands, [FakeChild([], 0)], environ={**env, "NDF_STATE_HOME": str(tmp_path / "old")})
    assert ended.cause == "precondition" and spawn.calls == []
    assert wrapper.MAJOR_CHANGED_LINE.format(own=VERSION, launched="12.0.0") in out
    assert (got.copy.path.parent / "relay.version").read_text(encoding="utf-8") == "12.0.0\n"
    launched = subprocess.run([sys.executable, "-I", str(got.copy.path), "ls"], capture_output=True, text=True, timeout=30, check=False)
    assert launched.stdout.strip() == "12.0.0 relay ls"
