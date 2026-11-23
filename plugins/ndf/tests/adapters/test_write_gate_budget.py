"""待ちの予算を持つ書き込み（#4016 の u2b。決定 10。`within`・`withdraw`・`Busy`・`gate_of`）。

時計と眠りは差し替え、記録は `tmp_path` に置く。本物のホームの下（`~/.local/state/ndf-github/`）を書かない。
偽の時計と記録の読み書きは `test_write_gate` のものを使う。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import httpx
import pytest
from filelock import Timeout

from ndf import slot_pool
from ndf.adapters import write_gate
from ndf.adapters.write_gate import CONTENT, WRITE

from .test_write_gate import HOUR, NOW, PATCH, RECORD_LOCK, W, Clock, graphql, record, seed


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def path(tmp_path) -> Path:
    return tmp_path / "gate" / write_gate.GATE_FILE  # tmp_path/state は共通の fixture が XDG_STATE_HOME に使う


@pytest.fixture
def gate(path, clock) -> write_gate.WriteGate:
    return write_gate.WriteGate(path, clock, clock.sleep)


# within（待ちの予算を持つ書き込み。決定 10）


def _send(throttler, request: httpx.Request = PATCH) -> list[bool]:
    """`throttler` で 1 つの書き込みを通し、送ったかを返す（`Busy` はそのまま出す）。"""
    sent = []
    with throttler.acquire(request):
        sent.append(True)
    return sent


class _Budget:
    """`remaining` の偽物。`left` を書き換えて予算を減らす。"""

    def __init__(self, left: float) -> None:
        self.left = left

    def __call__(self) -> float:
        return self.left


def _spy_locks(monkeypatch) -> list[str]:
    """取りに行くロックのファイル名を覚える。"""
    taken: list[str] = []
    real = slot_pool.lock
    monkeypatch.setattr(slot_pool, "lock", lambda lock_path: taken.append(Path(lock_path).name) or real(lock_path))
    return taken


@pytest.mark.parametrize(("times", "wait"), [({"blocked_until": NOW + 60}, 60), ({"next": NOW + 3}, 3)])
def test_within_refuses_a_wait_not_under_the_budget_without_writing(gate, path, clock, times, wait):
    seed(path, **times)
    before = record(path)

    with pytest.raises(write_gate.Busy) as busy:
        _send(gate.within(_Budget(3)))

    assert busy.value.seconds == pytest.approx(wait)
    assert record(path) == before and clock.slept == []


@pytest.mark.parametrize("left", [0.0, -5.0])
def test_within_without_budget_waits_for_no_lock(gate, path, clock, monkeypatch, left):
    seed(path)
    before = record(path)
    taken = _spy_locks(monkeypatch)

    with pytest.raises(write_gate.Busy) as busy:
        _send(gate.within(_Budget(left)))

    assert busy.value.seconds == 0 and taken == [] and record(path) == before


def test_within_gives_back_the_reservation_when_the_budget_runs_out_while_sleeping(gate, path, clock, monkeypatch):
    seed(path, next=NOW + 2)
    budget = _Budget(5)
    taken = _spy_locks(monkeypatch)
    clock.on_sleep = lambda: setattr(budget, "left", -1)
    with pytest.raises(write_gate.Busy):
        _send(gate.within(budget))

    assert clock.slept == [pytest.approx(2)]
    assert write_gate.SEND_LOCK_FILE not in taken
    assert record(path)["next"] == pytest.approx(NOW + 2)


def _slow_save(gate, monkeypatch, clock: Clock, budget: _Budget, seconds: float, spent: Optional[float] = None) -> None:
    """予約を書き終えるまでに時計が `seconds` 秒進み、予算が `spent`（省けば同じ秒）減ったことにする。

    予算は呼び手が自分の数え方で返す（`RetryWithin` の `remaining` と同じ）ので、時計と同じだけ減るとは限らない。"""
    real = gate.reserve

    def reserve(*args, **kwargs):
        made = real(*args, **kwargs)
        clock.now += seconds
        budget.left -= seconds if spent is None else spent
        return made

    monkeypatch.setattr(gate, "reserve", reserve)


def test_within_rereads_the_wait_after_saving_the_reservation(gate, path, clock, monkeypatch):
    # PR 4267 の r2-2: 予約の保存中に 2 秒過ぎたら、予約の時の 3 秒でなく残りの 1 秒だけ眠る
    seed(path, next=NOW + 3)
    budget = _Budget(10)
    _slow_save(gate, monkeypatch, clock, budget, 2)

    assert _send(gate.within(budget)) == [True]
    assert clock.slept == [pytest.approx(1)]


def test_within_refuses_before_sleeping_when_saving_used_up_the_budget(gate, path, clock, monkeypatch):
    # PR 4267 の r2-2: 予約の保存中に予算が予約の時刻までの残りを下回れば、眠らずに `Busy` を出して予約を取り消す
    seed(path, next=NOW + 3)
    budget = _Budget(4)
    _slow_save(gate, monkeypatch, clock, budget, 1, spent=3)

    with pytest.raises(write_gate.Busy) as busy:
        _send(gate.within(budget))

    assert busy.value.seconds == pytest.approx(2)
    assert clock.slept == [] and record(path)["next"] == pytest.approx(NOW + 3)


class _Held:
    """ほかのプロセスが持つロックの偽物。待つ秒を覚え、その秒で Timeout を返す。"""

    waited: list[Optional[float]] = []

    def __init__(self, path: Path) -> None:
        self.lock_file = str(path)

    def acquire(self, timeout: Optional[float] = None) -> None:
        _Held.waited.append(timeout)
        raise Timeout(self.lock_file)


@pytest.fixture
def held(monkeypatch):
    """`held(name)` の後は、ファイル名が `name` のロックをほかのプロセスが持つ。`held.discard(name)` で放す。"""
    names: set[str] = set()
    _Held.waited = []
    real = slot_pool.lock
    monkeypatch.setattr(slot_pool, "lock", lambda lock_path: _Held(lock_path) if Path(lock_path).name in names else real(lock_path))
    return names


@pytest.mark.parametrize(("lock", "left"), [(RECORD_LOCK, 1), (write_gate.SEND_LOCK_FILE, 2)])
def test_within_stops_waiting_for_a_lock_at_the_budget_and_leaves_no_reservation(gate, path, clock, held, capsys, lock, left):
    # 送信のロックの時間切れは予約の後なので、予約を取り消す。どちらも I5（ロックを持たずに送る）へ落とさない
    seed(path)
    held.add(lock)

    with pytest.raises(write_gate.Busy) as busy:
        _send(gate.within(_Budget(left)))

    assert _Held.waited == [left] and busy.value.seconds == left
    assert {k: record(path)[k] for k in write_gate.TIMES} == dict.fromkeys(write_gate.TIMES, 0.0)
    assert record(path)["content"] == []
    assert "取れない" not in capsys.readouterr().err


def test_within_does_not_send_when_the_budget_is_gone_just_before_sending(gate, path, clock, monkeypatch):
    seed(path)
    budget = _Budget(5)
    real = gate._peek

    def peek(kind, remaining=None):
        got = real(kind, remaining)
        budget.left = 0
        return got

    monkeypatch.setattr(gate, "_peek", peek)
    with pytest.raises(write_gate.Busy):
        _send(gate.within(budget))

    assert {k: record(path)[k] for k in write_gate.TIMES} == dict.fromkeys(write_gate.TIMES, 0.0)
    assert record(path)["content"] == []


def test_within_leaves_the_reservation_when_another_holds_the_record_lock(gate, path, clock, held):
    seed(path, next=NOW + 2)
    clock.on_sleep = lambda: held.add(RECORD_LOCK)

    with pytest.raises(write_gate.Busy) as busy:
        _send(gate.within(_Budget(5)))

    # 予約の後の読み直しで記録のロックの待ちを予算で縛り、取り消しは待たない（timeout 0）
    assert _Held.waited == [5, 0] and busy.value.seconds == 5
    held.discard(RECORD_LOCK)
    assert gate.reserve(WRITE).at == pytest.approx(NOW + 2 + W)


def test_within_refuses_content_when_the_window_opens_beyond_the_budget(gate, path, clock):
    # 1 時間のウィンドウが上限に達し、空くまでの 200 秒が予算に収まらない。送らず、一覧に自分の時刻を残さない
    length, count = HOUR
    times = [NOW - 3500 + i for i in range(count + 100)]
    seed(path, content=times)
    content = graphql("mutation { addComment(input: {}) { x } }")

    with pytest.raises(write_gate.Busy) as busy:
        _send(gate.within(_Budget(100)), content)

    assert busy.value.seconds == pytest.approx(times[-count] + length - NOW)
    assert record(path)["content"] == times and clock.slept == []


def test_within_refuses_content_when_the_window_fills_before_sending(gate, path, clock):
    # 予約の後、ほかのプロセスの項目が送り終えた時刻へ延びて 1 時間のウィンドウが埋まった。送る直前の読み直しで
    # 送信のロックを放して予約を取り直し、空くまでの秒（約 98 秒）を予算で数え、送らずに自分の時刻を除く
    _, count = HOUR
    times = [NOW - 3500 + i for i in range(count - 1)]
    seed(path, content=times, next=NOW + 2)

    def fill() -> None:
        saved = record(path)
        saved["content"] = sorted([*saved["content"], NOW + 1])
        path.write_text(json.dumps(saved), encoding="utf-8")

    clock.on_sleep = fill
    with pytest.raises(write_gate.Busy) as busy:
        _send(gate.within(_Budget(50)), graphql("mutation { addComment(input: {}) { x } }"))

    assert busy.value.seconds == pytest.approx(times[0] + 3600 - (NOW + 2))
    assert record(path)["content"] == [*times, NOW + 1]


def test_within_a_late_previous_send_over_the_budget_is_busy(gate, path, clock):
    # 先の書き込みは今から 5 秒の応答の後に送り終えた（I6）。その待ちが予算に収まらない
    seed(path, next=NOW - 10, sent_next=NOW + 5 + W)

    with pytest.raises(write_gate.Busy) as busy:
        _send(gate.within(_Budget(3)))

    assert busy.value.seconds == pytest.approx(5 + W)
    assert record(path)["next"] == pytest.approx(NOW - 10)


def test_within_sends_when_the_waits_fit_the_budget(gate, path, clock):
    seed(path, next=NOW + 2)

    assert _send(gate.within(_Budget(10))) == [True]
    assert clock.slept == [pytest.approx(2)]
    assert record(path)["sent_next"] == pytest.approx(NOW + 2 + W)


# within（待ち直し）


def _block_during_the_first_sleep(clock: Clock, other: write_gate.WriteGate, seconds: float) -> None:
    def block_once() -> None:
        if len(clock.slept) == 1:
            other.block(seconds)

    clock.on_sleep = block_once


def test_within_replaced_reservation_is_given_back_when_the_budget_is_gone_before_sending(gate, path, clock, monkeypatch):
    seed(path, next=NOW + 2)
    _block_during_the_first_sleep(clock, write_gate.WriteGate(path, clock, clock.sleep), 30)
    budget = _Budget(100)
    peeks = []
    real = gate._peek

    def peek(kind, remaining=None):
        got = real(kind, remaining)
        peeks.append(got)
        if len(peeks) == 2:  # 置き換えた予約の眠りの後の読み直し
            budget.left = 0
        return got

    monkeypatch.setattr(gate, "_peek", peek)
    with pytest.raises(write_gate.Busy):
        _send(gate.within(budget), graphql("mutation { addComment(input: {}) { x } }"))

    assert clock.slept == [pytest.approx(2), pytest.approx(30)]
    assert (record(path)["next"], record(path)["content"]) == (pytest.approx(NOW + 2), [])


def test_within_replacement_over_the_budget_gives_back_the_old_reservation(gate, path, clock):
    seed(path, next=NOW + 2)
    _block_during_the_first_sleep(clock, write_gate.WriteGate(path, clock, clock.sleep), 30)

    with pytest.raises(write_gate.Busy) as busy:
        _send(gate.within(_Budget(10)), graphql("mutation { addComment(input: {}) { x } }"))

    assert busy.value.seconds == pytest.approx(30) and clock.slept == [pytest.approx(2)]
    assert (record(path)["next"], record(path)["content"]) == (pytest.approx(NOW + 2), [])
    assert record(path)["sent_next"] == 0.0


def test_within_async_refuses_a_wait_over_the_budget(gate, path):
    seed(path, blocked_until=NOW + 60)

    async def write() -> None:
        async with gate.within(_Budget(3)).async_acquire(PATCH):
            pass

    with pytest.raises(write_gate.Busy):
        asyncio.run(write())


def test_within_async_rereads_the_budget_right_before_sleeping(gate, path, clock, monkeypatch):
    # PR 4267 の r3-2: 手順が眠りを出してからループが再開するまでに予算が切れたら、眠らずに `Busy` を出して予約を取り消す
    seed(path, next=NOW + 3)
    budget = _Budget(5)
    real = write_gate._advance

    def advance(steps):
        done = real(steps)
        clock.now += 1
        budget.left -= 3  # 予算は呼び手の数え方で減る（時計と同じとは限らない）
        return done

    monkeypatch.setattr(write_gate, "_advance", advance)

    async def write() -> None:
        async with gate.within(budget).async_acquire(PATCH):
            pass

    with pytest.raises(write_gate.Busy) as busy:
        asyncio.run(write())

    assert busy.value.seconds == pytest.approx(2)
    assert record(path)["next"] == pytest.approx(NOW + 3)


# withdraw・gate_of


def test_withdraw_gives_back_the_last_reservation_only_when_the_record_lock_is_free(gate, path, held):
    gate.withdraw(gate.reserve(CONTENT))
    assert (record(path)["next"], record(path)["content"]) == (0.0, [])

    reservation = gate.reserve(WRITE)
    held.add(RECORD_LOCK)
    gate.withdraw(reservation)

    assert _Held.waited == [0] and record(path)["next"] == pytest.approx(NOW + W)


def test_gate_of_finds_the_gate_behind_a_throttler(gate):
    from githubkit.throttling import LocalThrottler

    assert write_gate.gate_of(gate) is gate
    assert write_gate.gate_of(gate.within(_Budget(1))) is gate
    assert write_gate.gate_of(LocalThrottler(1)) is None
