"""見回りの替えと最後の砦（U9。#3604 #3864 の決定 4・8・11、I12・I13・I14）。

- 見回り: 今のアカウントが使い切りの組で閾値の内側の候補があれば、止めてよい時点で子を止めて同じ会話を続ける
- 止めてよい時点: 静かな時間・回答待ちの質問が無い・会話の記録の最後が終わった応答で、それが最後の入力より後
- 上限の後: 上限の観測を書き、替え先を順に試す（次点まで届く）。待つ間も読み直し、解けたアカウントで続ける
- 取得のスレッド: 見回りと中継を止めず、形の変化を 1 回だけ知らせる
値はすべて偽の値で、実のアカウントとネットワークを使わない。
"""

from __future__ import annotations

import io
import json
import threading
import time
from datetime import datetime, timezone

import pytest
from ndf.adapters import claude_usage
from ndf.contracts import limits
from ndf.domains.relay import account_dir, accounts, observe, switch_decide, wrapper, wrapper_fetch
from relay_children import LIMIT_LINE, FakeChild, Spawner, clock_ticks, make_settings, put_usage, register, rows_of, run_wrapper

BARE_LIMIT = b"You've hit your limit\r\n"  # リセットの時刻も窓の語も持たない上限の行
GUARD_WAIT = "閾値を越えた。止めてよい時点を待つ"


class Hooked(FakeChild):
    """起動されたとき `on_start(session_id)` を呼ぶ偽の子（会話の記録や使用率を起動の後に置く）。"""

    def __init__(self, outputs, exit_code, on_start=None):
        super().__init__(outputs, exit_code)
        self.on_start = on_start

    def started(self, directory, session_id, launch):
        if self.on_start is not None:
            self.on_start(session_id)


def put_turn(tmp_path, session_id: str, at: float, tool: bool = False) -> None:
    """会話の記録の最後に `assistant` の記録を 1 つ置く。`tool` なら結果を待つ `tool_use`、偽なら終わった応答。"""
    path = observe.transcript_path(make_settings(tmp_path).environ, tmp_path, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}}] if tool else [{"type": "text", "text": "済んだ"}]
    message = {"role": "assistant", "content": content, "stop_reason": "tool_use" if tool else "end_turn"}
    row = {"type": "assistant", "message": message, "timestamp": datetime.fromtimestamp(at, timezone.utc).isoformat()}
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


def put_window(tmp_path, name: str, five: float, resets: float, now: float) -> None:
    accounts.write_usage(accounts.usage_path(tmp_path / "state", name, "g1"), {"five_hour": (five, resets)}, now)


def _envs(spawn):
    return [env.get("NDF_CLAUDE_ACCOUNT") for _, env in spawn.calls]


def _guarded(tmp_path, usages, turn=60.0, tool=False, outputs=None, exit_code=None):
    """work1 で動く子の見回り。`usages` は名前 → (5 時間, 7 日)。`turn` は起動から会話の記録の終わりの応答までの秒（None は記録なし）。"""

    def prepare(d, state):
        register(tmp_path, *usages)
        for n, (five, seven) in usages.items():
            put_usage(tmp_path, n, five, seven)
        accounts.use(state, "work1")

    def on_start(session_id):
        if turn is not None:
            put_turn(tmp_path, session_id, time.time() + turn, tool)

    first = Hooked(outputs or [b"working\r\n"], exit_code, on_start)
    return run_wrapper(tmp_path, [first, FakeChild([], 0)], prepare)


@pytest.mark.parametrize(
    "work1",
    [(86.0, None), (10.0, 91.0)],
    ids=["five-hour-over-a", "weekly-over-c"],
)
def test_d11_i12_3864_4_guard_switches_at_the_safe_point_and_resumes_the_same_conversation(tmp_path, work1):
    # 決定 11・I12・受け入れ条件 #3864 の 4（見回り）: 5 時間 86% か 7 日 91% の work1 は、止めてよい時点で余裕の work2 へ替える
    ended, spawn, w = _guarded(tmp_path, {"work1": work1, "work2": (10.0, None)})
    (first, _), (second, _) = spawn.calls
    assert _envs(spawn) == ["work1", "work2"] and ended.cause == ""
    assert second[:3] == [wrapper.CLAUDE, "--resume", first[2]] and second[-1] == switch_decide.GUARD_RESUME_PROMPT
    assert [(r["from"], r["account"], r["reason"]) for r in rows_of(w, "account") if "from" in r][-1] == ("work1", "work2", "guard")


@pytest.mark.parametrize(
    "usages",
    [
        {"work1": (10.0, 75.0), "work2": (10.0, None)},  # B 越え・C より下は途中で替えない
        {"work1": (86.0, None), "work2": (86.0, None)},  # 閾値の内側の候補が無ければ使い切る
    ],
    ids=["weekly-b-only", "no-inner-candidate"],
)
def test_i12_guard_keeps_the_account_without_an_inner_candidate_or_below_c(tmp_path, usages):
    outputs = [b"working\r\n"] + [b""] * 8
    ended, spawn, w = _guarded(tmp_path, usages, outputs=outputs, exit_code=0)
    assert _envs(spawn) == ["work1"] and "書かずに終わった" in ended.reason
    assert GUARD_WAIT not in [r["reason"] for r in rows_of(w, "wait")]


@pytest.mark.parametrize(
    "turn, tool, outputs",
    [
        (60.0, True, None),  # 会話の記録の最後が結果を待つ tool_use
        (-60.0, False, None),  # 前の応答の記録が最後のまま（最後の入力より前）
        (None, False, None),  # 会話の記録が読めない
        (60.0, False, [b"x\r\n"] * 10),  # 子の出力が止まらない（静かな時間の前）
    ],
    ids=["tool-use-pending", "turn-before-the-input", "no-transcript", "not-quiet"],
)
def test_i13_guard_does_not_stop_the_child_outside_the_safe_point(tmp_path, turn, tool, outputs):
    # I13: 止めてよい時点の外では子を止めない。子が自分で終わるまで替えず、替えずに終わる
    outputs = outputs or [b"working\r\n"] + [b""] * 8
    ended, spawn, w = _guarded(tmp_path, {"work1": (86.0, None), "work2": (10.0, None)}, turn, tool, outputs, 0)
    assert _envs(spawn) == ["work1"] and "書かずに終わった" in ended.reason
    if outputs[-1] == b"":
        assert GUARD_WAIT in [r["reason"] for r in rows_of(w, "wait")]


def _limited(tmp_path, names, on_start, line=LIMIT_LINE, wall=None, pin="work1"):
    def prepare(d, state):
        register(tmp_path, *names)
        accounts.use(state, pin)

    return run_wrapper(tmp_path, [Hooked([line], None, on_start), FakeChild([], 0)], prepare, wall=wall)


def test_d8_a_spent_window_and_a_quiet_child_count_as_the_limit_without_the_line(tmp_path):
    # 決定 8: 画面に上限の行が無くても、今の窓が 100 以上で子が静かなら上限の観測を書いて替える
    now = time.time()

    def on_start(_sid):
        put_window(tmp_path, "work1", 100.0, now + 3600, now)
        put_window(tmp_path, "work2", 10.0, now + 3600, now)

    ended, spawn, w = _limited(tmp_path, ("work1", "work2"), on_start, line=b"working\r\n")
    assert _envs(spawn) == ["work1", "work2"] and spawn.calls[1][0][-1] == switch_decide.RESUME_PROMPT
    seen = json.loads(accounts.limits_path(tmp_path / "state").read_text(encoding="utf-8"))
    assert list(seen) == ["work1"] and seen["work1"]["g1"]["until"] == pytest.approx(now + 3600)


@pytest.mark.parametrize(
    "others, expect",
    [({"work2": None, "work3": (10.0, 95.0)}, "work2"), ({"work3": (10.0, 95.0)}, "work3")],
    ids=["unknown-usage-first", "spent-group-too"],
)
def test_3864_5_d8_after_the_limit_an_unknown_usage_account_comes_before_the_spent_group(tmp_path, others, expect):
    # 受け入れ条件 #3864 の 5・決定 8: 使用率の分からない work2 で続け、無ければ使い切りの組（7 日 95%）の work3 へも替える
    def on_start(_sid):
        put_usage(tmp_path, "work1", 50.0)
        for n, u in others.items():
            if u is not None:
                put_usage(tmp_path, n, *u)

    ended, spawn, w = _limited(tmp_path, ("work1", *others), on_start)
    assert _envs(spawn) == ["work1", expect] and spawn.calls[1][0][1] == "--resume"


def test_r2_1_a_limit_target_that_fails_after_the_stop_falls_to_the_next_candidate(tmp_path, monkeypatch):
    # U8 の cross-review の r2-1: 上限の後に最上位の替え先（gmail）の用意が止めた後に落ちたら、次点の main で再開する（元の team へ戻らない）
    calls = []
    real = account_dir.prepare

    def prepare(store, name, environ, launch):
        calls.append(name)
        if len(calls) > 1 and name == "gmail":
            return account_dir.Prepared(False, "lock_timeout", store / name)
        return real(store, name, environ, launch)

    monkeypatch.setattr(account_dir, "prepare", prepare)

    def on_start(_sid):
        for n, five in (("team", 50.0), ("main", 5.0), ("gmail", 5.0)):
            put_usage(tmp_path, n, five)

    ended, spawn, w = _limited(tmp_path, ("team", "main", "gmail"), on_start, pin="team")
    assert _envs(spawn) == ["team", "main"] and calls[1:] == ["gmail", "main"]
    assert rows_of(w, "account")[-1]["skipped"] == ["gmail"]


class JumpWall:
    """上限の観測が書かれるまでは `base`、書かれた後は `base + jump` を返す壁時計。"""

    def __init__(self, state, base: float, jump: float):
        self.state, self.base, self.jump = state, base, jump

    def now(self) -> float:
        return self.base + (self.jump if accounts.limits_path(self.state).exists() else 0.0)


def test_r2_3_i14_the_same_account_resumes_after_its_reset_without_a_new_usage_record(tmp_path):
    # U8 の cross-review の r2-3・I14: 替え先が無く wait_reset で待ち、窓のリセットと観測の until を過ぎたら、
    # 使用率の記録が書き足されなくても同じアカウントで同じ会話を続ける
    base, stamp = time.time(), []

    def on_start(_sid):
        if not stamp:
            put_window(tmp_path, "work1", 100.0, base + 3600, base)
            stamp.append(accounts.usage_path(tmp_path / "state", "work1", "g1").stat().st_mtime_ns)

    wall = JumpWall(tmp_path / "state", base, 7200.0)
    ended, spawn, w = _limited(tmp_path, ("work1",), on_start, line=BARE_LIMIT, wall=wall)
    (first, _), (second, _) = spawn.calls
    assert _envs(spawn) == ["work1", "work1"] and second[:3] == [wrapper.CLAUDE, "--resume", first[2]]
    assert rows_of(w, "wait_reset") and accounts.usage_path(tmp_path / "state", "work1", "g1").stat().st_mtime_ns == stamp[0]


def test_3864_10_i14_while_waiting_the_account_that_resets_first_takes_the_conversation(tmp_path):
    # 受け入れ条件 #3864 の 10・I14: 従量の接続も API キーも無く、work2 の 5 時間の窓が work1 より先に解けると work2 で続ける
    base = time.time()

    def on_start(_sid):
        put_window(tmp_path, "work1", 100.0, base + 3600, base)
        put_window(tmp_path, "work2", 100.0, base + 1800, base)

    wall = JumpWall(tmp_path / "state", base, 2000.0)
    ended, spawn, w = _limited(tmp_path, ("work1", "work2"), on_start, line=BARE_LIMIT, wall=wall)
    assert _envs(spawn) == ["work1", "work2"] and rows_of(w, "wait_reset")


# ---------------------------------------------------------------- 取得のスレッド（決定 4）


def test_d4_a_fetch_that_waits_does_not_stop_the_patrol_or_the_relay(tmp_path):
    # 決定 4: 偽の fetch_usage が止まったままでも、見回りとカットポイントの切り替えが進む
    release, began, order = threading.Event(), threading.Event(), []

    def fetch(state_dir, target, now, until, gate):
        order.append(("fetch", target.name))
        began.set()
        release.wait(10)
        return None

    class Releasing(Spawner):
        def __call__(self, argv, env, cwd, pass_fds=()):
            if self.made:
                began.wait(10)  # 取得が始まって止まっている間に、次のセグメントの起動まで進んだことを確かめる
                order.append(("spawn", len(self.made)))
                release.set()
            return super().__call__(argv, env, cwd, pass_fds)

    s = make_settings(tmp_path, fetch=fetch)
    register(tmp_path, "work1", "work2")
    spawn = Releasing([FakeChild([b"working\r\n"], None), FakeChild([], 0)], {0: "/goal 次"})
    w = wrapper.Wrapper(s, spawn=spawn, clock=clock_ticks())
    try:
        ended = w.run("最初の 1 行")
    finally:
        for c in spawn.made:
            c.close()
    assert ended.cause == "" and len(spawn.calls) == 2
    assert order.index(("spawn", 1)) > order.index(next(o for o in order if o[0] == "fetch"))


def test_d4_the_shape_change_is_told_once_and_again_after_a_success(tmp_path):
    # 決定 4（形の変化）: 2 回目の shape では出さず、成功の後の shape でまた 1 行と usage_shape の行を出す。値は出さない
    out = io.BytesIO()
    w = wrapper.Wrapper(make_settings(tmp_path, fetch=lambda *a: None), spawn=Spawner([]), clock=clock_ticks(), out=out)
    w.dir.mkdir(parents=True)
    shape = claude_usage.Usage(error="shape", missing=("five_hour",))
    for got in (shape, shape, claude_usage.Usage(five_hour=(42.0, 1.0)), shape):
        w.fetcher.done.put(("work1", got))
        w._drain_fetch()
    assert out.getvalue().decode().count(wrapper_fetch.SHAPE_LINE) == 2 and "42" not in out.getvalue().decode()
    assert [(r["account"], r["items"]) for r in rows_of(w, "usage_shape")] == [("work1", ["five_hour"])] * 2


def test_d4_a_fetch_that_raises_does_not_stop_the_thread(tmp_path):
    # 決定 4: fetch_usage が例外を上げてもスレッドは止まらず、次のアカウントを取る
    def fetch(state_dir, target, now, until, gate):
        if target.name == "work1":
            raise OSError("偽の失敗")
        return claude_usage.Usage(five_hour=(1.0, 2.0))

    f = wrapper_fetch.Fetcher(tmp_path, fetch)
    for name in ("work1", "work2"):
        f.put(accounts.FetchTarget(tmp_path, name, "g1"), 0.0, None)
    got, deadline = [], time.monotonic() + 10
    while len(got) < 2 and time.monotonic() < deadline:
        got += f.results()
        time.sleep(0.01)
    f.close()
    assert [n for n, _ in got] == ["work1", "work2"] and isinstance(got[0][1], OSError) and got[1][1].error is None
    assert f.busy == set()


def test_d4_i14_a_refetched_window_frees_a_waiting_account(tmp_path):
    # 決定 4・I14: 待つ間（wait_reset）に取り直した値が上限でなければ、そのアカウントで同じ会話を続ける
    base = time.time()
    store = register(tmp_path, "work1", "work2")

    def fetch(state_dir, target, now, until, gate):
        if target.name != "work2":
            return None
        got = claude_usage.Usage(five_hour=(10.0, base + 18000), fetched_at=base)
        accounts.write_fetch(accounts.fetch_path(store, "work2", "g1"), got, base)
        return got

    def on_start(_sid):
        put_window(tmp_path, "work1", 100.0, base + 3600, base)
        put_window(tmp_path, "work2", 100.0, base + 3600, base)

    def prepare(d, state):
        accounts.use(state, "work1")

    first = Hooked([BARE_LIMIT], None, on_start)
    ended, spawn, w = run_wrapper(tmp_path, [first, FakeChild([], 0)], prepare, fetch=fetch)
    assert _envs(spawn) == ["work1", "work2"] and spawn.calls[1][0][1] == "--resume"


# ---------------------------------------------------------------- cross-review の r1（U9）


def test_r1_1_a_limit_found_by_a_spent_window_resumes_after_the_reset_without_the_line(tmp_path):
    # cross-review の r1-1: 上限の行が無く窓の 100% と静かな時間で検出した上限は、窓のリセットで limited が落ちても
    # 引き継いで、唯一のアカウントで同じ会話を続ける
    base = time.time()

    def on_start(_sid):
        put_window(tmp_path, "work1", 100.0, base + 3600, base)

    wall = JumpWall(tmp_path / "state", base, 7200.0)
    ended, spawn, w = _limited(tmp_path, ("work1",), on_start, line=b"working\r\n", wall=wall)
    (first, _), (second, _) = spawn.calls
    assert _envs(spawn) == ["work1", "work1"] and second[:3] == [wrapper.CLAUDE, "--resume", first[2]]


def test_r1_2_an_observation_without_any_window_resumes_the_only_account_after_its_until(tmp_path):
    # cross-review の r1-2: 窓をどこからも得られない上限の観測は、until を過ぎて今の行が消えたら、同じアカウントで続ける
    base = time.time()
    wall = JumpWall(tmp_path / "state", base, limits.seconds("relay.limit-unknown") + 60.0)
    ended, spawn, w = _limited(tmp_path, ("work1",), None, line=BARE_LIMIT, wall=wall)
    (first, _), (second, _) = spawn.calls
    assert _envs(spawn) == ["work1", "work1"] and second[:3] == [wrapper.CLAUDE, "--resume", first[2]]


def test_r1_3_another_lanes_observation_does_not_stop_a_quiet_child_without_a_spent_window(tmp_path):
    # cross-review の r1-3: 別のレーンが書いた上限の観測だけでは、窓に 100% 以上の無い静かな子を上限として止めない
    now = time.time()

    def prepare(d, state):
        register(tmp_path, "work1")
        accounts.use(state, "work1")

    def on_start(_sid):  # 起動の後に別のレーンが観測を書いた
        put_window(tmp_path, "work1", 50.0, now + 3600, now)
        accounts.see_limit(tmp_path / "state", "work1", "g1", now, (now + 3600, None, None))

    first = Hooked([b"working\r\n"] + [b""] * 8, 0, on_start)
    ended, spawn, w = run_wrapper(tmp_path, [first, FakeChild([], 0)], prepare)
    assert _envs(spawn) == ["work1"] and "書かずに終わった" in ended.reason and not rows_of(w, "wait_reset")


def _fetching(tmp_path, at):
    """取得の記録の `at` を別のラッパーが書いた形にした work1 と、キューへ入れた名前を数えるラッパー。"""
    store = register(tmp_path, "work1")
    path = accounts.fetch_path(store, "work1", "g1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": at, "error": None, "windows": {}}), encoding="utf-8")
    w = wrapper.Wrapper(make_settings(tmp_path, fetch=lambda *a: None), spawn=Spawner([]), clock=clock_ticks())
    put = []
    w.fetcher.put = lambda target, now, until: put.append((target.name, now, until))
    return w, put


def test_r1_4_an_until_that_passed_since_the_last_try_queues_a_fetch(tmp_path):
    # cross-review の r1-4: 窓のリセットを持たない上限の観測の until を過ぎたら、次の定期取得を待たずに取り直す
    base = time.time()
    w, put = _fetching(tmp_path, base - 1)
    accounts.see_limit(tmp_path / "state", "work1", "g1", base, (base + 10, None, None))
    w._queue_fetch(base)
    assert put == []
    w._queue_fetch(base + 11)
    assert put == [("work1", base + 11, base + 10)] and w.limit_untils == {}


def test_r1_5_a_fetch_taken_by_another_wrapper_is_not_rechecked_every_patrol(tmp_path, monkeypatch):
    # cross-review の r1-5: 別のラッパーが先に取った名前は、記録のスタンプが同じで判定が変わる時刻の前なら開き直さない
    base = time.time()
    w, put = _fetching(tmp_path, base - 1)
    calls = []
    real = accounts.due_at
    monkeypatch.setattr(accounts, "due_at", lambda *a: calls.append(a) or real(*a))
    for i in range(5):
        w._queue_fetch(base + i * 0.5)
    assert put == [] and len(calls) == 1
    w._queue_fetch(base - 1 + limits.seconds("relay.usage-fetch"))
    assert put and len(calls) == 2


def test_r1_6_a_target_that_fails_the_last_check_keeps_the_child_and_picks_again(tmp_path, monkeypatch):
    # cross-review の r1-6: 止めてよい時点を待つ間に替え先（work2）の照合が外れたら、止める直前の照合で外し、元の子を残す
    real, real_recheck, armed = account_dir.ready, wrapper.Wrapper._recheck_choice, []

    def ready(store, name, environ, launch):
        got = real(store, name, environ, launch)
        return account_dir.Probe(name, "lock_timeout", got.stamps) if name == "work2" and armed else got

    def recheck(self, sec):  # 止める直前の照合から後で work2 の照合が外れる
        armed.append(True)
        return real_recheck(self, sec)

    monkeypatch.setattr(account_dir, "ready", ready)
    monkeypatch.setattr(wrapper.Wrapper, "_recheck_choice", recheck)
    outputs = [b"working\r\n"] + [b""] * 8
    ended, spawn, w = _guarded(tmp_path, {"work1": (86.0, None), "work2": (10.0, None)}, outputs=outputs, exit_code=0)
    assert _envs(spawn) == ["work1"] and "書かずに終わった" in ended.reason
    assert [(r["account"], r["reason"]) for r in rows_of(w, "account_recheck")] == [("work2", "lock_timeout")]


def test_r1_7_the_fetch_thread_is_closed_when_the_loop_raises(tmp_path, monkeypatch):
    # cross-review の r1-7: ラッパーの実行が例外で抜けても、取得のゲートを閉じてから抜ける
    w = wrapper.Wrapper(make_settings(tmp_path, fetch=lambda *a: None), spawn=Spawner([]), clock=clock_ticks())

    def boom(prompt):
        raise RuntimeError("偽の失敗")

    monkeypatch.setattr(w, "_loop", boom)
    with pytest.raises(RuntimeError):
        w.run("最初の 1 行")
    assert w.fetcher.closed and not w.fetcher.gate.begin_refresh()
