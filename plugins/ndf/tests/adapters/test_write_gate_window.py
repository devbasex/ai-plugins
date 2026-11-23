"""内容を作る書き込みを 1 分と 1 時間のウィンドウで数える（#4016 の決定 11・I2。記録の `content` の一覧と version 2）。

時計と眠りは差し替え、記録は `tmp_path` に置く。本物のホームの下（`~/.local/state/ndf-github/`）を書かない。
偽の時計と記録の読み書きは `test_write_gate` のものを使う。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.adapters import write_gate
from ndf.adapters.write_gate import CONTENT, WRITE

from .test_write_gate import HOUR, MINUTE, NOW, W, Clock, _send_lock_is_free, record, seed


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def path(tmp_path) -> Path:
    return tmp_path / "gate" / write_gate.GATE_FILE  # tmp_path/state は共通の fixture が XDG_STATE_HOME に使う


@pytest.fixture
def gate(path, clock) -> write_gate.WriteGate:
    return write_gate.WriteGate(path, clock, clock.sleep)


def test_a_full_hour_window_pushes_content_until_the_500th_from_the_end_leaves(gate, path):
    # 1 時間に 600 件。後ろから 500 番目（NOW - 3400）が 1 時間のウィンドウから出る時刻まで待つ
    length, count = HOUR
    times = [NOW - 3500 + i for i in range(count + 100)]
    seed(path, content=times)

    assert gate.reserve(WRITE).wait == 0
    assert gate.reserve(CONTENT).at == pytest.approx(times[-count] + length)


def test_a_full_minute_window_pushes_content_until_the_80th_from_the_end_leaves(gate, path):
    length, count = MINUTE
    times = [NOW - 50 + i * 0.5 for i in range(count)]
    seed(path, content=times)

    assert gate.reserve(CONTENT).at == pytest.approx(times[-count] + length)


def test_content_waiting_for_the_window_does_not_push_the_other_writes(gate, path):
    # PR 4273 の r1-2: ウィンドウの空きを待つ予約は共通の `next` を進めない
    length, count = HOUR
    times = [NOW - 500 + i * 0.1 for i in range(count)]
    seed(path, content=times)

    waiting = gate.reserve(CONTENT)

    assert waiting.at == pytest.approx(times[0] + length) and not waiting.spaced
    assert record(path)["next"] == 0.0
    assert gate.reserve(WRITE).wait == 0


def test_content_waiting_for_the_window_takes_its_turn_again_when_the_window_opens(gate, path, clock):
    length, count = HOUR
    times = [NOW - 500 + i * 0.1 for i in range(count)]
    seed(path, content=times)

    with gate.hold(CONTENT):
        sent_at = clock.now

    assert sent_at == pytest.approx(times[0] + length)
    assert clock.slept == [pytest.approx(times[0] + length - NOW)]
    assert record(path)["next"] == pytest.approx(sent_at + W)


def test_a_later_reservation_waiting_for_the_window_does_not_hold_back_an_earlier_send(gate, path, clock):
    # PR 4273 の r1-1: 送った 499 件の後、先の予約が眠る間に後ろの予約がウィンドウの空きを待つ時刻を取っても、
    # 先の予約は後ろの予約を数えずに送る
    length, count = HOUR
    times = [NOW - 500 + i * 0.1 for i in range(count - 1)]
    seed(path, next=NOW + W, content=times)
    other = write_gate.WriteGate(path, clock, clock.sleep)
    later = []
    clock.on_sleep = lambda: later.append(other.reserve(CONTENT)) if not later else None

    with gate.hold(CONTENT):
        sent_at = clock.now

    assert sent_at == pytest.approx(NOW + W) and clock.slept == [pytest.approx(W)]
    assert later[0].at == pytest.approx(times[0] + length) and not later[0].spaced


def test_a_later_reservation_waiting_for_the_window_does_not_push_back_an_earlier_one_taking_its_turn_again(gate, path, clock):
    # PR 4273 の r2-1: 先の予約が 10 秒後・後ろの予約が 200 秒後にウィンドウの空きを待つ。10 秒後に送った履歴が 499 件へ
    # 減ったら、先の予約は取り直しで後ろの予約を数えず、90 秒の予算の中で送る
    length, count = HOUR
    times = [NOW + 10 - length, NOW + 200 - length, *(NOW - 3000 + i * 0.1 for i in range(count - 2))]
    seed(path, content=times)
    other = write_gate.WriteGate(path, clock, clock.sleep)
    later = []
    clock.on_sleep = lambda: later.append(other.reserve(CONTENT)) if not later else None

    with gate.hold(CONTENT, remaining=lambda: NOW + 90 - clock.now):
        sent_at = clock.now

    assert sent_at == pytest.approx(NOW + 10) and clock.slept == [pytest.approx(10)]
    assert later[0].at == pytest.approx(NOW + 200) and not later[0].spaced
    assert NOW + 200 in record(path)["content"]  # 後ろの予約は残り、送る直前の判定で自分の番を確かめる


def test_a_send_waits_for_the_window_outside_the_send_lock(gate, path, clock):
    # PR 4273 の r1-1: 送る直前にウィンドウが空いていなければ、送信のロックを放して予約を取り直す
    length, count = HOUR
    times = [NOW - 500 + i * 0.1 for i in range(count - 1)]
    seed(path, next=NOW + W, content=times)
    free = []

    def crowd() -> None:
        if not free:  # 予約の後、記録の外で送られた 1 件（I5 の重ね）が一覧に入る
            saved = record(path)
            saved["content"] = sorted([*saved["content"], NOW])
            path.write_text(json.dumps(saved), encoding="utf-8")
        free.append(_send_lock_is_free(path))

    clock.on_sleep = crowd
    with gate.hold(CONTENT):
        sent_at = clock.now

    opens = times[0] + length
    assert sent_at == pytest.approx(opens)
    assert clock.slept == [pytest.approx(W), pytest.approx(opens - NOW - W)]
    assert free == [True, True]  # ウィンドウの空きを待つ眠りの間、送信のロックを持たない


def test_the_send_replaces_its_own_time_with_when_it_finished(gate, path, clock):
    seed(path, content=[NOW - 100])

    with gate.hold(CONTENT):
        clock.now += 5  # 応答に 5 秒かかる

    assert record(path)["content"] == [NOW - 100, pytest.approx(NOW + 5)]


def test_withdraw_and_replacing_take_out_only_their_own_time(gate, path):
    seed(path, content=[NOW - 100])
    gate.withdraw(gate.reserve(CONTENT))
    assert record(path)["content"] == [NOW - 100]

    old = gate.reserve(CONTENT)
    got = gate.reserve(CONTENT, replacing=old)

    assert got.at == NOW and record(path)["content"] == [NOW - 100, NOW]


def test_times_older_than_the_hour_window_are_dropped(gate, path):
    length, _ = HOUR
    seed(path, content=[NOW - length - 1, NOW - length + 1])

    gate.reserve(WRITE)

    assert record(path)["content"] == [NOW - length + 1]


def test_a_rewound_wall_clock_shifts_the_content_times(gate, path):
    hour = 3600.0
    seed(path, content=[NOW + hour - 600], written_at=NOW + hour)

    gate.reserve(WRITE)

    assert record(path)["content"] == [pytest.approx(NOW - 600)]


def test_a_version_1_record_keeps_its_times_and_is_rewritten_as_version_2(gate, path):
    path.parent.mkdir(parents=True)
    old = {"next": NOW + 3, "next_content": NOW + 50, "sent_next": NOW + 2, "sent_next_content": NOW + 60, "blocked_until": NOW + 30}
    path.write_text(json.dumps({"version": 1, **old, "written_at": NOW}), encoding="utf-8")

    got = gate.reserve(CONTENT)

    assert got.at == pytest.approx(NOW + 30)
    saved = record(path)
    assert saved["version"] == 2 and saved["content"] == [pytest.approx(NOW + 30)]
    assert (saved["next"], saved["sent_next"], saved["blocked_until"]) == (pytest.approx(NOW + 30 + W), NOW + 2, NOW + 30)
    assert "next_content" not in saved and "sent_next_content" not in saved
