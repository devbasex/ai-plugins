"""キーの子の停止の共通の経路（U11a。#3604 #3864 の受け入れ条件 #3864 の 9・決定 12・I9）。

キーの子（API キーの宣言の従量の接続の子）が helper の拒み・記録の異常・失敗の印・401・設定の当たりを確かめたら、
同じ見回りの `_loop` の (2)〜(3) で替え先の候補を読む前に旧子を止め、止めた後の `_revoked_pick` だけが替え先を読む。
替え先があれば `--resume`、無ければ直し方の 1 行と `account_unavailable` で終わり、子を残さない（`stalled` にしない）。
値はすべて偽の値で、実のキーとアカウントを使わない。
"""

from __future__ import annotations

import fcntl
import io
import json
import os
import time
import types
from datetime import datetime, timezone

import pytest

from ndf.contracts import limits
from ndf.domains.relay import key_helper, observe, switch_decide, wrapper, wrapper_auth, wrapper_stop
from ndf.domains.relay.launch_target import Choice
from relay_children import FakeChild, Spawner, clock_ticks, make_settings, register

FAKE_KEY = "sk-ant-api03-fake-test-key-not-real"
DECL = f"ANTHROPIC_API_KEY={FAKE_KEY}"
# 不正な API キーで打ち直しの後に出る行（2026-11-22 に Claude Code 2.1.330 の端末で取った出力。値は伏せた）
API_KEY_SCREEN = "401 invalid x-api-key · retrying in 9s · attempt 10/10\r\n  ⎿  Invalid API key · Fix external API key\r\n"


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
    assert not key_helper.pay(rec.path, rec.helper_id, None, time.time())


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
    # 401 はキーの子では `_key_auth` が確かめる（`_auth_pick` は打たない）。止める前に拒否の印を足す
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
        fd = os.open(w.dir / wrapper.QUESTION_LOCK, os.O_RDWR | os.O_CREAT, 0o600)
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
