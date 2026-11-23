"""見回りの替えと最後の砦（U9。#3604 #3864 の決定 4・8・11、I12・I13・I14）。

- 見回り: 今のアカウントが使い切りの組で閾値の内側の候補があれば、止めてよい時点で子を止めて同じ会話を続ける
- 止めてよい時点: 静かな時間・回答待ちの質問が無い・会話の記録の最後が終わった応答で、それが最後の入力より後
- 上限の後: 今のアカウントを取り直し、上限を見た後の取得が届くまで今のアカウントを上限として扱う。替え先を順に試す
  （次点まで届く）。待つ間も取得の記録を読み直し、解けたアカウントで続ける
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
from ndf.domains.relay import account_choice, account_dir, accounts, observe, switch_decide, wrapper, wrapper_fetch
from ndf.domains.relay import account_registry as reg
from relay_children import LIMIT_LINE, FakeChild, Spawner, clock_ticks, make_settings, put_usage, register, rows_of, run_wrapper

BARE_LIMIT = b"You've hit your limit\r\n"  # リセットの時刻も窓の語も持たない上限の行
GUARD_WAIT = "閾値を越えた。止めてよい時点を待つ"
BEDROCK_DECL = "CLAUDE_CODE_USE_BEDROCK=1 AWS_REGION=us-east-1"


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


def _store(tmp_path):
    return reg.store_dir(make_settings(tmp_path).environ)


def put_fetch(tmp_path, name: str, five=None, seven=None, at=None, spent=None) -> None:
    """`name` の取得の記録を、`at`（既定は今）に成功した取得の値で置き換える。窓は (使用率, リセットの時刻) の対。"""
    at = time.time() if at is None else at
    got = claude_usage.Usage(five_hour=five, seven_day=seven, spend_limit_reached=spent)
    accounts.write_fetch(accounts.fetch_path(_store(tmp_path), name, "g1"), got, at)


def put_window(tmp_path, name: str, five: float, resets: float, now: float) -> None:
    put_fetch(tmp_path, name, (five, resets), at=now)


def on_first_wait(monkeypatch, then, kind="wait_reset") -> list:
    """上限の後の判断が最初に `kind` を返した見回りで `then()` を 1 回呼ぶ。返した判断を並べたリストを返す。"""
    picks, real = [], wrapper.Wrapper._limit_pick

    def spy(self, sec, screen):
        got = real(self, sec, screen)
        if got is not None:
            if got.kind == kind and not any(p.kind == kind for p in picks):
                then()
            picks.append(got)
        return got

    monkeypatch.setattr(wrapper.Wrapper, "_limit_pick", spy)
    return picks


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
    # 決定 8: 画面に上限の行が無くても、取得の記録で今の窓が 100 以上で子が静かなら上限として替える（ファイルには書かない）
    now = time.time()

    def on_start(_sid):
        put_window(tmp_path, "work1", 100.0, now + 3600, now)
        put_window(tmp_path, "work2", 10.0, now + 3600, now)

    ended, spawn, w = _limited(tmp_path, ("work1", "work2"), on_start, line=b"working\r\n")
    assert _envs(spawn) == ["work1", "work2"] and spawn.calls[1][0][-1] == switch_decide.RESUME_PROMPT


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
    """`jump()` を呼ぶまでは `base`、呼んだ後は `base + by` を返す壁時計（待つ間にリセットを過ぎた形を作る）。"""

    def __init__(self, base: float, by: float):
        self.base, self.by, self.jumped = base, by, False

    def now(self) -> float:
        return self.base + (self.by if self.jumped else 0.0)

    def jump(self) -> None:
        self.jumped = True


def test_i14_after_the_limit_the_same_account_waits_for_a_fetch_newer_than_the_limit(tmp_path, monkeypatch):
    # 決定 8・I14: 上限の行を見たら work1 をすぐ取り直す（force）。上限を見た時刻より前の記録（10%）では work1 へ戻らず
    # wait_reset で待ち、上限を見た後の取得が届いて上限が解けていれば、同じアカウントで同じ会話を続ける
    store, release, forced = _store(tmp_path), threading.Event(), []

    def fetch(target, now, force, gate):
        if not force:
            return None
        forced.append(target.name)
        release.wait(10)  # 最初の wait_reset を見届けてから取得を返す
        got = claude_usage.Usage(five_hour=(10.0, time.time() + 18000))
        accounts.write_fetch(accounts.fetch_path(store, target.name, target.generation), got, time.time())
        return got

    def prepare(d, state):
        register(tmp_path, "work1")
        put_fetch(tmp_path, "work1", (10.0, time.time() + 3600), at=time.time() - 60)  # 上限を見る前の取得
        accounts.use(state, "work1")

    picks = on_first_wait(monkeypatch, release.set)
    ended, spawn, w = run_wrapper(tmp_path, [Hooked([BARE_LIMIT], None), FakeChild([], 0)], prepare, fetch=fetch)
    (first, _), (second, _) = spawn.calls
    assert forced == ["work1"] and picks[0].kind == "wait_reset" and picks[-1].kind == "switch"
    assert _envs(spawn) == ["work1", "work1"] and second[:3] == [wrapper.CLAUDE, "--resume", first[2]]


def test_3864_10_i14_while_waiting_the_account_that_resets_first_takes_the_conversation(tmp_path, monkeypatch):
    # 受け入れ条件 #3864 の 10・I14: 従量の接続も API キーも無く、work2 の 5 時間の窓が work1 より先に解けると work2 で続ける
    base = time.time()

    def on_start(_sid):
        put_window(tmp_path, "work1", 100.0, base + 3600, base)
        put_window(tmp_path, "work2", 100.0, base + 1800, base)

    wall = JumpWall(base, 2000.0)
    on_first_wait(monkeypatch, wall.jump)
    ended, spawn, w = _limited(tmp_path, ("work1", "work2"), on_start, line=BARE_LIMIT, wall=wall)
    assert _envs(spawn) == ["work1", "work2"] and rows_of(w, "wait_reset")


def test_d8_d11_a_fetch_that_returns_100_keeps_the_patrol_on_metered(tmp_path, monkeypatch):
    # 決定 8・11（2026-11-22 の team の事例の再発防止）: 上限で従量の接続へ替えた後、取り直した記録が team を 100% と返す間は、
    # 見回りが従量の接続から team へ戻らない。上限の前の記録は 55%、取り直した応答はリセットの時刻が 0.6 秒早い
    base, release, wrote = time.time(), threading.Event(), threading.Event()
    store = _store(tmp_path)
    picks = on_first_wait(monkeypatch, release.set, kind="switch")  # 上限の後の最初の判断は取り直す前の記録で決める
    monkeypatch.setattr(account_choice, "claude_version", lambda env, cwd, exe_of=None: "2.1.330")
    monkeypatch.setattr(wrapper.Wrapper, "_safe_point", lambda self, sec, idle: True)

    def fetch(target, now, force, gate):
        if not force:
            return None
        release.wait(10)
        got = claude_usage.Usage(five_hour=(100.0, base + 3600 - 0.6))
        accounts.write_fetch(accounts.fetch_path(store, target.name, target.generation), got, time.time())
        wrote.set()
        return got

    class AfterFetch(Spawner):
        def __call__(self, argv, env, cwd, pass_fds=()):
            if self.made:
                wrote.wait(10)  # 従量の接続の子の見回りは、取り直した記録を読む
            return super().__call__(argv, env, cwd, pass_fds)

    s = make_settings(tmp_path, fetch=fetch, environ={**make_settings(tmp_path).environ, "NDF_SUPERVISE_CLAUDE_FALLBACK": BEDROCK_DECL})
    register(tmp_path, "team")
    put_fetch(tmp_path, "team", (55.0, base + 3600), at=base - 60)
    accounts.use(s.state_dir, "team")
    metered = FakeChild([b"working\r\n"] + [b""] * 8, 0)
    spawn = AfterFetch([FakeChild([LIMIT_LINE], None), metered, FakeChild([], 0)])
    w = wrapper.Wrapper(s, spawn=spawn, clock=clock_ticks())
    try:
        ended = w.run("最初の 1 行")
    finally:
        for c in spawn.made + spawn.children:
            c.close()
    assert picks[0].account == reg.METERED and wrote.is_set()
    assert _envs(spawn) == ["team", reg.METERED] and "書かずに終わった" in ended.reason
    assert not [r for r in rows_of(w, "account") if r.get("from") == reg.METERED]


# ---------------------------------------------------------------- 取得のスレッド（決定 4）


def test_d4_a_fetch_that_waits_does_not_stop_the_patrol_or_the_relay(tmp_path):
    # 決定 4: 偽の fetch_usage が止まったままでも、見回りとカットポイントの切り替えが進む
    release, began, order = threading.Event(), threading.Event(), []

    def fetch(target, now, force, gate):
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
    def fetch(target, now, force, gate):
        if target.name == "work1":
            raise OSError("偽の失敗")
        return claude_usage.Usage(five_hour=(1.0, 2.0))

    f = wrapper_fetch.Fetcher(fetch)
    for name in ("work1", "work2"):
        f.put(accounts.FetchTarget(tmp_path, name, "g1"), 0.0)
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

    def fetch(target, now, force, gate):
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


def test_r1_1_a_limit_found_by_a_spent_window_resumes_after_the_reset_without_the_line(tmp_path, monkeypatch):
    # cross-review の r1-1: 上限の行が無く窓の 100% と静かな時間で検出した上限は、窓のリセットで limited が落ちても
    # 引き継いで、唯一のアカウントで同じ会話を続ける（記録は上限を見た時刻より後に取ったもの）
    base = time.time()

    def on_start(_sid):
        put_window(tmp_path, "work1", 100.0, base + 3600, base + 1)

    wall = JumpWall(base, 7200.0)
    on_first_wait(monkeypatch, wall.jump)
    ended, spawn, w = _limited(tmp_path, ("work1",), on_start, line=b"working\r\n", wall=wall)
    (first, _), (second, _) = spawn.calls
    assert _envs(spawn) == ["work1", "work1"] and second[:3] == [wrapper.CLAUDE, "--resume", first[2]]


def test_r2_1_a_spend_limit_from_the_api_switches_a_quiet_child_without_a_spent_window(tmp_path):
    # cross-review の r2-1: 使用率の API が上限の到達を返したら、窓が 100% 未満で上限の行の無い静かな子も上限として替える
    def prepare(d, state):
        register(tmp_path, "work1", "work2")
        accounts.use(state, "work1")

    def on_start(_sid):  # 起動の後に取得が上限の到達を返した
        now = time.time()
        put_fetch(tmp_path, "work1", (50.0, now + 3600), at=now, spent=True)
        put_usage(tmp_path, "work2", 10.0)

    first = Hooked([b"working\r\n"] + [b""] * 8, None, on_start)
    ended, spawn, w = run_wrapper(tmp_path, [first, FakeChild([], 0)], prepare)
    assert _envs(spawn) == ["work1", "work2"] and spawn.calls[1][0][-1] == switch_decide.RESUME_PROMPT


def _fetching(tmp_path, at):
    """取得の記録の `at` を別のラッパーが書いた形にした work1 と、キューへ入れた名前を数えるラッパー。"""
    store = register(tmp_path, "work1")
    path = accounts.fetch_path(store, "work1", "g1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": at, "error": None, "windows": {}}), encoding="utf-8")
    w = wrapper.Wrapper(make_settings(tmp_path, fetch=lambda *a: None), spawn=Spawner([]), clock=clock_ticks())
    put = []
    w.fetcher.put = lambda target, now, force=False: put.append((target.name, now, force))
    return w, put


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
    due = base - 1 + limits.seconds("relay.usage-fetch")
    w._queue_fetch(due)
    assert put == [("work1", due, False)] and len(calls) == 1  # 再利用した時刻で取り直す


def test_r1_6_a_target_that_fails_the_last_check_keeps_the_child_and_picks_again(tmp_path, monkeypatch):
    # cross-review の r1-6: 止めてよい時点を待つ間に替え先（work2）の確かめが外れたら、止める直前の確かめで外し、元の子を残す
    real, real_recheck, armed = account_dir.ready, wrapper.Wrapper._recheck_choice, []

    def ready(store, name, environ, launch):
        got = real(store, name, environ, launch)
        return account_dir.Probe(name, "lock_timeout", got.stamps) if name == "work2" and armed else got

    def recheck(self, sec):  # 止める直前の確かめから後で work2 の確かめが外れる
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


# ---------------------------------------------------------------- cross-review の r3（U9）


def test_r3_1_a_limit_found_only_by_the_api_resumes_when_the_api_clears_it(tmp_path, monkeypatch):
    # cross-review の r3-1: API の上限の到達だけで検出した上限は、上限を見た後の取得で API が解除を返したら同じアカウントで再開する
    def prepare(d, state):
        register(tmp_path, "work1")
        accounts.use(state, "work1")

    def on_start(_sid):
        now = time.time()
        put_fetch(tmp_path, "work1", (50.0, now + 3600), at=now, spent=True)

    def cleared():  # 次の取得で API が解除を返した
        now = time.time()
        put_fetch(tmp_path, "work1", (50.0, now + 3600), at=now + 1, spent=False)

    picks = on_first_wait(monkeypatch, cleared)
    first = Hooked([b"working\r\n"] + [b""] * 8, None, on_start)
    ended, spawn, w = run_wrapper(tmp_path, [first, FakeChild([], 0)], prepare)
    (one, _), (two, _) = spawn.calls
    assert picks[0].kind == "wait_reset" and _envs(spawn) == ["work1", "work1"] and two[:3] == [wrapper.CLAUDE, "--resume", one[2]]


def test_r3_3_the_generations_come_with_the_usage_without_rescanning_the_registry(tmp_path, monkeypatch):
    # cross-review の r3-3: 取得の判定は使用率のキャッシュが一緒に読んだ世代を使い、登録簿を走査し直さない
    base = time.time()
    w, put = _fetching(tmp_path, base - 1)
    w._inputs(base, False)
    monkeypatch.setattr(w.reader, "generations", lambda: pytest.fail("登録簿を走査し直した"))
    w._queue_fetch(base)
    assert put == [] and list(w.due_memo) == ["work1"]


def test_r3_4_the_queued_time_of_the_old_generation_does_not_hold_back_the_new_one(tmp_path):
    # cross-review の r3-4: 旧い世代をキューへ入れた直後に登録し直したら、新しい世代は旧い投入時刻で取得を抑えない
    base = time.time()
    register(tmp_path, "work1")
    w = wrapper.Wrapper(make_settings(tmp_path, fetch=lambda *a: None), spawn=Spawner([]), clock=clock_ticks())
    put = []
    w.fetcher.put = lambda target, now, force=False: (
        put.append((target.generation, now)) or w.fetcher.tried.update({target.name: (target.generation, now)})
    )
    w._queue_fetch(base)
    assert put == [("g1", base)]
    register(tmp_path, "work1", generation="g2")
    w._queue_fetch(base + 1)
    assert put == [("g1", base), ("g2", base + 1)]
