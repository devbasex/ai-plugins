"""ラッパーの見回り: 区間の切り替え・上限でのアカウントの替え・認証の失敗・版の混ざり（#2438・#3337・#3243・3341-2）。

子は擬似端末の代わりにパイプを持つ偽物で、時計は呼ぶたびに 1 秒進む偽物である（擬似端末も壁時計も待たない。#2962）。
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from datetime import date
from pathlib import Path

from ndf.domains.relay import accounts, marks, registry, signals, wrapper
from ndf.domains.relay.account_decide import Usage

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
    """`marks` は起動の順番 → その子のセッションが Stop の hook で書く次の区間のブロック。"""

    def __init__(self, children, marks=None):
        self.children = list(children)
        self.marks = dict(marks or {})
        self.calls: list[tuple[list[str], dict]] = []
        self.made: list[FakeChild] = []

    def __call__(self, argv, env, cwd):
        self.calls.append((argv, dict(env)))
        child = self.children.pop(0)
        block = self.marks.get(len(self.made))
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
        environ={"NDF_STATE_HOME": str(tmp_path / "old")},
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


def _run(tmp_path, children, prepare=None, marks=None, wall=None, **changes):
    s = _settings(tmp_path, **changes)
    spawn = Spawner(children, marks)
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

    ended, spawn, w = _run(tmp_path, [FakeChild([b"working\r\n"], None), FakeChild([], 0)], prepare, marks={0: "/goal 次の区間"})
    assert ended.cause == "" and "書かずに終わった" in ended.reason
    (first, first_env), (second, second_env) = spawn.calls
    assert first[:2] == [wrapper.CLAUDE, "--session-id"] and first[3:] == ["--model", "x", "最初の 1 行"]
    assert second[0:2] == [wrapper.CLAUDE, "--session-id"] and second[-1] == "/goal 次の区間" and second[2] != first[2]
    # カットポイントで使用率が閾値を超えていれば、空いたアカウントで次の区間を始める（#3336）
    assert (first_env["NDF_CLAUDE_ACCOUNT"], second_env["NDF_CLAUDE_ACCOUNT"]) == ("team", "main")
    assert first_env["NDF_RELAY_DIR"] == str(w.dir) and first_env["CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION"] == "false"
    assert marks.take(tmp_path / "state", second[2]) is True
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
    # 旧い版のラッパーが動いていれば、新しい版のラッパーは動かずに止まる（3341-2）
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
    # 前の実行の印（セッションが違う）で、新しい子を終わらせて置き換えない
    def prepare(d, state):
        d.mkdir(parents=True)
        signals.write_next(d, "old-session", "/goal 古い区間", True, 0.1)

    ended, spawn, w = _run(tmp_path, [FakeChild([], 0)], prepare)
    assert len(spawn.calls) == 1 and ended.cause == "" and "書かずに終わった" in ended.reason
    assert signals.read_next(w.dir) is None
    assert "stale_next" in [row["kind"] for row in _log(w)]


def test_next_mark_switches_once_its_quiet_time_has_passed_even_while_output_continues(tmp_path):
    # /goal の未達の応答が続いて画面が静かにならなくても、印を最初に書いてから静かな時間が過ぎれば切り替える（#2438）
    chatty = FakeChild([b"goal not met\r\n"] * 50, None)
    later = wrapper.WallClock(now=lambda: time.time() + 60.0)
    ended, spawn, _ = _run(tmp_path, [chatty, FakeChild([], 0)], marks={0: "/goal 次"}, wall=later, quiet=30.0)
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
