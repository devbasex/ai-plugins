"""キーの子の停止の共通の経路（U11a。#3604 #3864 の受け入れ条件 #3864 の 9・決定 12・I9）。

キーの子（API キーの設定の従量の接続の子）が helper の拒み・記録の異常・失敗のマーク・401・設定の当たりを確かめたら、
同じ見回りの `_loop` の (2)〜(3) で替え先の候補を読む前に旧子を止め、止めた後の `_revoked_pick` だけが替え先を読む。
替え先があれば `--resume`、無ければ直し方の 1 行と `account_unavailable` で終わり、子を残さない（`stalled` にしない）。
値はすべて偽の値で、実のキーとアカウントを使わない。
"""

from __future__ import annotations

import fcntl
import io
import json
import os
import sys
import time
import types
from datetime import datetime, timezone

import pytest

from ndf.contracts import limits
from ndf.domains.relay import key_helper, observe, switch_decide, wrapper, wrapper_auth, wrapper_launch, wrapper_stop
from ndf.domains.relay.launch_target import Choice
from relay_children import FakeChild, Spawner, clock_ticks, make_settings, register

FAKE_KEY = "sk-ant-api03-fake-test-key-not-real"
DECL = f"ANTHROPIC_API_KEY={FAKE_KEY}"
# 不正な API キーで打ち直しの後に出る行（2026-11-22 に Claude Code 2.1.330 の端末で取った出力。値は伏せた）
API_KEY_SCREEN = "401 invalid x-api-key · retrying in 9s · attempt 10/10\r\n  ⎿  Invalid API key · Fix external API key\r\n"


@pytest.fixture(autouse=True)
def key_child_ready(monkeypatch):
    """停止の経路を確かめるため、払い出しの上限を子へ組み込んだ前提で API キーの子を起動する。"""
    monkeypatch.setattr(wrapper_launch, "KEY_CHILD_READY", True)


def _iso(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


def put_failure(tmp_path, session_id: str, at: float) -> None:
    """会話の行の最後が 401 の API の失敗の記録の会話の記録を置く。"""
    path = observe.transcript_path(make_settings(tmp_path).environ, tmp_path, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    failure = {
        "type": "assistant",
        "timestamp": _iso(at),
        "isApiErrorMessage": True,
        "error": "authentication_failed",
        "apiErrorStatus": 401,
        "message": {"role": "assistant", "content": [{"type": "text", "text": "Failed to authenticate"}]},
    }
    rows = [{"type": "user", "timestamp": _iso(at), "message": {"role": "user", "content": "続けて"}}, failure]
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


class KeyChild(FakeChild):
    """見回りの回（isalive を呼ばれた回数）で `script[回](wrapper)` を打つ偽のキーの子。止められた回を `killed` に残す。"""

    def __init__(self, script, outputs=(b"working\r\n",)):
        super().__init__(list(outputs), None)
        self.script = dict(script)
        self.polls = 0
        self.w = None
        self.events: list = []
        self.killed = None

    def isalive(self):
        if self.alive:
            self.polls += 1
            act = self.script.get(self.polls)
            if act is not None:
                act(self.w)
        return super().isalive()

    def terminate(self, force=False):
        self.killed = self.polls
        self.events.append("stop")
        return super().terminate(force)


def _run(tmp_path, first, rest=(), names=(), stdin_fd=None):
    register(tmp_path, *names)
    environ = {**make_settings(tmp_path).environ, "NDF_SUPERVISE_CLAUDE_FALLBACK": DECL}
    s = make_settings(tmp_path, environ=environ)
    spawn = Spawner([first, *rest])
    out = io.BytesIO()
    w = wrapper.Wrapper(s, spawn=spawn, clock=clock_ticks(), out=out, stdin_fd=stdin_fd)
    w._initial = lambda now: Choice(("metered",), fixed=True)
    first.w = w
    inputs, key_auth = w._inputs, w._key_auth

    def spy_inputs(now, need):
        first.events.append("inputs")
        return inputs(now, need)

    def spy_key_auth(sec, screen):
        key_auth(sec, screen)
        if sec.must_end and "hit" not in first.events:
            first.events.append("hit")

    w._inputs, w._key_auth = spy_inputs, spy_key_auth
    try:
        ended = w.run("最初の 1 行")
    finally:
        for c in spawn.made + spawn.children:
            c.close()
    return ended, spawn, w, out.getvalue().decode("utf-8", errors="replace")


def refuse(w):
    rec = w.section.target.helper
    assert not key_helper.pay(rec.path, rec.helper_id, None)


def fail_mark(w):
    rec = w.section.target.helper
    with open(rec.path.parent / key_helper.FAIL_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": rec.helper_id, "by": "helper", "cause": "append"}) + "\n")


def gate_row(w):
    rec = w.section.target.helper
    with open(rec.path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": rec.helper_id, "gate": "利用者の設定"}) + "\n")


def _stopped_in_the_hit_tick(child) -> bool:
    """当たった見回りから止めるまでに候補の読み取りが無く、止めた後にだけ読む（同じ見回りの (2)〜(3)）。"""
    after = child.events[child.events.index("hit") + 1 :]
    return bool(after) and after[0] == "stop"


@pytest.mark.parametrize("act", [refuse, fail_mark], ids=["out-false", "fail-mark"])
def test_3864_9_d12_a_refused_helper_stops_the_child_and_resumes_on_work2(tmp_path, act):
    # 受け入れ条件 #3864 の 9・決定 12: 替え先があれば、止めた後に替え先を読んで --resume（AUTH_RESUME_PROMPT）
    first = KeyChild({3: act})
    ended, spawn, w, _ = _run(tmp_path, first, [FakeChild([], 0)], names=("work2",))
    (one, env1), (two, env2) = spawn.calls
    assert env1["NDF_CLAUDE_ACCOUNT"] == "metered" and env2["NDF_CLAUDE_ACCOUNT"] == "work2"
    assert two[:3] == [wrapper.CLAUDE, "--resume", one[2]] and two[-1] == switch_decide.AUTH_RESUME_PROMPT
    assert _stopped_in_the_hit_tick(first) and ended.cause == ""
    assert "metered" in w.tried


def test_3864_9_d12_no_fallback_ends_with_the_fix_line_instead_of_stalling(tmp_path):
    first = KeyChild({3: refuse})
    ended, spawn, w, out = _run(tmp_path, first)
    assert ended.cause == "account_unavailable" and len(spawn.calls) == 1
    assert first.killed is not None and _stopped_in_the_hit_tick(first)
    assert wrapper_auth.AUTH_FIX_METERED in out and "metered" in out
    assert (w.dir / "wrapper.json").exists() and json.loads((w.dir / "wrapper.json").read_text())["state"] != "stalled"


def test_3864_9_d12_a_401_writes_the_deny_mark_and_stops(tmp_path):
    # 401 はキーの子では `_key_auth` が確かめる（`_auth_pick` は打たない）。止める前に拒否のマークを足す
    def failing(w):
        put_failure(tmp_path, w.section.session_id, time.time() + 1)

    first = KeyChild({2: failing}, outputs=(API_KEY_SCREEN.encode(),))
    ended, _, w, out = _run(tmp_path, first)
    rec = w.section.target.helper
    rows = [json.loads(line) for line in rec.path.read_text().splitlines()]
    assert {"id": rec.helper_id, "deny": True} in rows
    assert ended.cause == "account_unavailable" and _stopped_in_the_hit_tick(first)
    assert wrapper_auth.AUTH_FIX_METERED in out


def test_d12_a_settings_hit_stops_without_trying_the_key_out(tmp_path):
    # 設定の当たりは tried に入れず、end_cause は drift で、キーが断られた前提の直し方を出さない
    first = KeyChild({3: gate_row})
    ended, _, w, out = _run(tmp_path, first)
    assert ended.cause == "account_unavailable" and _stopped_in_the_hit_tick(first)
    assert "metered" not in w.tried and w.section.end_cause == "drift"
    assert "利用者の設定" in out and wrapper_auth.AUTH_FIX_METERED not in out


def test_d12_a_question_lock_that_never_frees_ends_refused_without_a_successor(tmp_path):
    held = []

    def refuse_and_hold(w):
        fd = os.open(w.dir / wrapper_stop.QUESTION_LOCK, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        held.append(fd)
        refuse(w)

    first = KeyChild({3: refuse_and_hold})
    try:
        ended, spawn, w, _ = _run(tmp_path, first, [FakeChild([], 0)], names=("work2",))
    finally:
        for fd in held:
            os.close(fd)
    assert ended == wrapper.Ended("refused", wrapper_stop.LAUNCH_STUCK.format(dir=w.dir))
    assert len(spawn.calls) == 1 and first.killed is not None


def _bare(tmp_path, **sec):
    w = wrapper.Wrapper(make_settings(tmp_path), spawn=Spawner([]), clock=lambda: 100.0)
    w.dir.mkdir(parents=True, exist_ok=True)
    w._safe_point = lambda s, idle: False
    base = dict(must_end=True, end_now=False, must_end_at=100.0, child=FakeChild([b"x"], None), last_output=100.0)
    return w, types.SimpleNamespace(**{**base, **sec})


def test_d12_end_point_waits_until_end_wait_unless_told_to_end_now(tmp_path):
    w, sec = _bare(tmp_path)
    try:
        assert not w._end_point(sec)
        sec.end_now = True
        assert w._end_point(sec)
        sec.end_now, sec.must_end_at = False, 100.0 - limits.seconds("relay.end-wait")
        assert w._end_point(sec)
    finally:
        sec.child.close()


def test_d12_end_point_is_true_once_the_child_has_ended(tmp_path):
    w, sec = _bare(tmp_path, child=FakeChild([], 1))
    try:
        assert w._end_point(sec)
    finally:
        sec.child.close()


def test_d12_input_is_not_sent_to_the_child_while_it_must_end(tmp_path):
    r, wfd = os.pipe()
    w, sec = _bare(tmp_path, eof=True, screen=bytearray(), input_at=None)
    w.stdin_fd = r
    w.section = sec
    try:
        os.write(wfd, b"hello\r")
        w._pump(0.0)
        assert sec.child.written == [] and sec.input_at is None
    finally:
        os.close(r)
        os.close(wfd)
        sec.child.close()


def test_revoked_fix_names_every_tried_connection():
    line = wrapper_stop.revoked_fix({"metered", "key:k1"})
    assert line.index("key:k1") < line.index("metered")
    assert wrapper_auth.AUTH_FIX_METERED in line and "ndf relay account add-api-key k1" in line


def test_d12_end_point_after_quiet_with_a_question_or_an_api_error(tmp_path):
    # 静かな時間の後、回答待ちの質問があるか、最後の入力より後の API の失敗の記録で入力待ちに戻っていれば止める
    w, sec = _bare(tmp_path, last_output=100.0 - w_quiet(tmp_path), session_id="s-1", input_at=None)
    try:
        assert not w._end_point(sec)
        put_failure(tmp_path, "s-1", time.time())
        assert w._end_point(sec)
        sec.input_at = time.time() + 60
        assert not w._end_point(sec)
        (w.dir / wrapper.signals.QUESTION_FILE).write_text("{}")
        assert w._end_point(sec)
    finally:
        sec.child.close()


def w_quiet(tmp_path) -> float:
    return make_settings(tmp_path).quiet


def test_d12_an_api_key_child_is_not_launched_until_the_payout_limit_is_wired(tmp_path, monkeypatch):
    # 宣言のキーが ANTHROPIC_API_KEY のまま子へ渡る間は、払い出しの上限を迂回するので起動しない
    monkeypatch.setattr(wrapper_launch, "KEY_CHILD_READY", False)
    first = KeyChild({})
    ended, spawn, w, out = _run(tmp_path, first)
    assert spawn.calls == [] and ended.cause == "account_unavailable"
    assert "払い出しの上限" in out and not (w.dir / key_helper.RECORD_FILE).exists()


class Stubborn(FakeChild):
    """強制終了しても終わらない子。"""

    def terminate(self, force=False):
        return False


def test_d12_a_contended_record_lock_shares_the_stop_deadline(tmp_path, monkeypatch):
    # 401 の拒否のマークで記録の錠を待った分は、旧子の終わりを待つ時間から引く（確かめてから止めるまで relay.child-exit の中）
    lock, child_exit = 0.3, 0.4
    real_seconds, real_value = limits.seconds, limits.value
    monkeypatch.setattr(limits, "seconds", lambda name: child_exit if name == "relay.child-exit" else real_seconds(name))
    monkeypatch.setattr(limits, "value", lambda name, key: lock if (name, key) == (key_helper.POLICY, "lock") else real_value(name, key))
    w = wrapper.Wrapper(make_settings(tmp_path), spawn=Spawner([]), out=io.BytesIO())
    w.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    rec = key_helper.create(w.dir)
    w._auth_seen = lambda sec, screen: True
    child = Stubborn([], None)
    fields = dict(must_end=False, end_now=False, must_end_at=None, end_cause=None, end_source=None, stop_by=None, denied=False)
    sec = types.SimpleNamespace(launch="l-1", account="metered", child=child, target=types.SimpleNamespace(helper=rec), **fields)
    fd = os.open(rec.path, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        began = time.monotonic()
        w._key_auth(sec, "")
        stopped = w._stop_child(sec, sec.stop_by)
        took = time.monotonic() - began
    finally:
        os.close(fd)
        child.close()
    assert sec.end_now and not stopped
    assert child_exit - 0.05 <= took < child_exit + 0.15


def test_d12_the_stop_of_a_real_pty_child_keeps_its_signal_waits_inside_the_deadline(tmp_path):
    # 実際の PtyProcess.terminate は SIGHUP・SIGCONT・SIGINT・SIGKILL の後に固定の待ちを挟む。期限の残りの中に収める（r2-3）
    from ptyprocess import PtyProcess

    code = "import signal,time;[signal.signal(s,signal.SIG_IGN) for s in (signal.SIGHUP,signal.SIGINT)];print('ready',flush=True);time.sleep(30)"
    child = PtyProcess.spawn([sys.executable, "-c", code])
    try:
        assert b"ready" in child.read(64) and child.delayafterterminate >= 0.1
        w = wrapper.Wrapper(make_settings(tmp_path), spawn=Spawner([]), out=io.BytesIO())
        began = time.monotonic()
        stopped = w._stop_child(types.SimpleNamespace(child=child), began + 0.12)
        took = time.monotonic() - began
    finally:
        if child.isalive():
            child.kill(9)
        child.close(force=True)
    assert stopped and took < 0.25 and child.delayafterterminate >= 0.1  # 縮めずに打てば 3 回の待ちだけで 0.3 秒


def test_d12_a_stop_after_an_earlier_deny_gets_its_own_deadline(tmp_path):
    # 止めずに待つ間の拒否のマークの期限を、後で止めへ移ったときの旧子の終わりの期限に使い回さない（r2-4）
    now = [100.0]
    w = wrapper.Wrapper(make_settings(tmp_path), spawn=Spawner([]), out=io.BytesIO(), clock=lambda: now[0])
    w.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    rec = key_helper.create(w.dir)
    w._auth_seen = lambda sec, screen: now[0] == 100.0
    fields = dict(must_end=True, end_now=False, must_end_at=90.0, end_cause="drift", end_source=None, stop_by=None, denied=False)
    sec = types.SimpleNamespace(
        launch="l-1", account="metered", child=FakeChild([], None), target=types.SimpleNamespace(helper=rec), **fields
    )
    w._key_auth(sec, "")
    assert not sec.end_now and sec.stop_by is None
    assert {"id": rec.helper_id, "deny": True} in [json.loads(x) for x in rec.path.read_text().splitlines()]
    now[0] = 200.0
    with open(rec.path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": rec.helper_id, "gate": "利用者の設定"}) + "\n")
    w._key_auth(sec, "")
    assert sec.end_now and sec.stop_by == 200.0 + limits.seconds("relay.child-exit")
    sec.child.close()


def test_d12_a_401_left_on_the_screen_writes_the_deny_mark_once_per_launch(tmp_path, monkeypatch):
    # 止める時点を待つ間に 401 が画面に残っても、書けた後は見回りごとに書き足さず錠も待たない。書けなければ期限の中で打ち直す（r3-3）
    now = [100.0]
    w = wrapper.Wrapper(make_settings(tmp_path), spawn=Spawner([]), out=io.BytesIO(), clock=lambda: now[0])
    w.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    rec = key_helper.create(w.dir)
    w._auth_seen = lambda sec, screen: True
    fields = dict(must_end=True, end_now=False, must_end_at=95.0, end_cause="drift", end_source=None, stop_by=None, denied=False)
    sec = types.SimpleNamespace(
        launch="l-1", account="metered", child=FakeChild([], None), target=types.SimpleNamespace(helper=rec), **fields
    )
    calls, real = [], key_helper.deny
    monkeypatch.setattr(key_helper, "deny", lambda r, wait=None: calls.append(wait) or real(r, wait))

    def marks():
        return [json.loads(x) for x in rec.path.read_text().splitlines()].count({"id": rec.helper_id, "deny": True})

    fd = os.open(rec.path, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        w._key_auth(sec, "")  # 錠を取れずに書けない
        assert not sec.denied and marks() == 0
        now[0] = 95.0 + limits.seconds("relay.end-wait") - 0.01
        w._key_auth(sec, "")  # 打ち直しは期限の残りの中
        assert calls[-1] == pytest.approx(0.01)
    finally:
        os.close(fd)
    now[0] = 101.0
    w._key_auth(sec, "")
    assert sec.denied and marks() == 1
    for _ in range(3):
        w._key_auth(sec, "")
    assert marks() == 1 and len(calls) == 3
    sec.child.close()
