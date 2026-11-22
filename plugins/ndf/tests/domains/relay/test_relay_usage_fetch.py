"""中継のアカウントの使用率の API の取得の記録・取得の排他・取り直しの判定（#3864 の決定 4、I16）。

偽の宛先と認証ファイルを使う取得の手順（`fetch_usage`・`_refresh`）は、起動の前の確かめ（U2・U3）の部品の後に足す。
"""

from __future__ import annotations

import json
import os
import stat
import threading
import time

from ndf.adapters import claude_usage
from ndf.domains.relay import accounts
from ndf.domains.relay.account_decide import Usage

NOW = 1_795_000_000.0
LATER = NOW + 3600
WEEK = NOW + 5 * 86400
INTERVAL = 300.0


def _fetch_record(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _got(**kw) -> claude_usage.Usage:
    return claude_usage.Usage(fetched_at=NOW, **kw)


def test_decision4_fetch_record_lives_in_the_store_and_reads_from_any_state_dir(tmp_path):
    store, state_a, state_b = tmp_path / "store", tmp_path / "state-a", tmp_path / "state-b"
    path = accounts.fetch_path(store, "work2", "g2")
    assert path == store / ".usage-fetch" / "work2" / "g2.json"
    assert accounts.fetch_path(store, "work2", "") == path.with_name("_.json")
    accounts.write_fetch(path, _got(five_hour=(40.0, LATER), seven_day=(20.0, WEEK), scoped=[("opus", 95.0, WEEK)]), NOW)
    rec = _fetch_record(path)
    assert rec["error"] is None and rec["windows"]["seven_day_scoped"] == {"opus": {"percent": 95.0, "resets_at": WEEK}}
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    # 状態の保存先には何も書かず、保存先の違う 2 つのラッパーが同じ記録を読む（モデル別の週の枠 95 を使用率に読む）
    for state in (state_a, state_b):
        assert accounts.usages(state, store, NOW, {"work2": "g2"}) == [Usage("work2", 95.0, False)]
        assert not accounts.usage_dir(state).exists()
    # 旧い世代の取得の記録は読まない
    assert accounts.usages(state_a, store, NOW, {"work2": "g3"}) == []


def test_decision4_usages_merges_the_status_line_and_the_fetch_record_per_window(tmp_path):
    store, state = tmp_path / "store", tmp_path / "state"
    accounts.write_usage(accounts.usage_path(state, "work1", "g1"), {"five_hour": (50.0, LATER)}, NOW)
    accounts.write_fetch(accounts.fetch_path(store, "work1", "g1"), _got(five_hour=(30.0, LATER), seven_day=(60.0, WEEK)), NOW)
    assert accounts.usages(state, store, NOW, {"work1": "g1"}) == [Usage("work1", 60.0, False)]
    # 取得の記録の窓が新しい resets_at を持てば、使用率が小さくてもそれを採る
    accounts.write_fetch(accounts.fetch_path(store, "work1", "g1"), _got(five_hour=(5.0, LATER + 18000)), NOW)
    windows = accounts.read_windows(accounts.usage_path(state, "work1", "g1"), accounts.fetch_path(store, "work1", "g1"))
    assert windows["five_hour"] == (5.0, LATER + 18000)
    # 片方が読めなければもう片方だけで読む
    accounts.fetch_path(store, "work1", "g1").write_text("{", encoding="utf-8")
    assert accounts.usages(state, store, NOW, {"work1": "g1"}) == [Usage("work1", 50.0, False)]


def test_decision4_write_fetch_keeps_model_windows_apart_and_missing_windows(tmp_path):
    path = accounts.fetch_path(tmp_path, "work1", "g1")
    thursday, friday = NOW + 4 * 86400, NOW + 5 * 86400
    accounts.write_fetch(path, _got(five_hour=(10.0, LATER), scoped=[("a", 95.0, friday)]), NOW)
    # 次の応答で別の識別の枠 B が 100。A と B を別々に残し、7 日を 100 と読む。応答に無い 5 時間の窓は前の値が残る
    accounts.write_fetch(path, _got(scoped=[("b", 100.0, thursday)]), NOW)
    windows = _fetch_record(path)["windows"]
    assert windows["seven_day_scoped"] == {"a": {"percent": 95.0, "resets_at": friday}, "b": {"percent": 100.0, "resets_at": thursday}}
    assert windows["five_hour"] == {"percent": 10.0, "resets_at": LATER}
    assert accounts.usages(tmp_path / "state", tmp_path, NOW, {"work1": "g1"}) == [Usage("work1", 100.0, True)]
    # 崩れたモデル別の枠（None）は前の値を残し、古い入力・失効した入力で下げない
    accounts.write_fetch(path, _got(five_hour=(3.0, LATER - 60), scoped=None), NOW)
    accounts.write_fetch(path, _got(five_hour=(1.0, NOW - 1)), NOW)
    assert _fetch_record(path)["windows"] == windows


def test_decision4_spend_limit_holds_until_an_explicit_false(tmp_path):
    store, state = tmp_path / "store", tmp_path / "state"
    path = accounts.fetch_path(store, "work2", "g2")
    # 最初の応答が窓を持たず spend_limit_reached が真なら、窓もキャッシュも無くても上限の行で返る
    accounts.write_fetch(path, _got(spend_limit_reached=True), NOW)
    assert accounts.usages(state, store, NOW, {"work2": "g2"}) == [Usage("work2", None, True)]  # type: ignore[arg-type]
    accounts.write_fetch(path, _got(five_hour=(10.0, LATER)), NOW)  # 応答に無い（None）なら真が残る
    assert accounts.usages(state, store, NOW, {"work2": "g2"}) == [Usage("work2", 10.0, True)]
    accounts.write_fetch(path, _got(five_hour=(10.0, LATER), spend_limit_reached=False), NOW)
    assert accounts.usages(state, store, NOW, {"work2": "g2"}) == [Usage("work2", 10.0, False)]


def test_decision4_write_fetch_keeps_the_attempt_time_and_clears_the_error(tmp_path):
    path = accounts.fetch_path(tmp_path, "work1", "g1")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"at": NOW - 5, "error": "network", "spend_limit_reached": None, "windows": {}}), encoding="utf-8")
    accounts.write_fetch(path, _got(five_hour=(10.0, LATER)), NOW)
    rec = _fetch_record(path)
    assert rec["at"] == NOW - 5 and rec["error"] is None and rec["spend_limit_reached"] is None


def _attempted(store, at, windows=None):
    path = accounts.fetch_path(store, "work1", "g1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": at, "error": None, "spend_limit_reached": None, "windows": windows or {}}), encoding="utf-8")
    return path


def test_i16_due_once_per_interval(tmp_path):
    usage = accounts.usage_path(tmp_path / "state", "work1", "g1")
    fetch = accounts.fetch_path(tmp_path, "work1", "g1")
    assert accounts.due(usage, fetch, NOW, INTERVAL, None, None)  # 試したことが無い
    _attempted(tmp_path, NOW)
    assert not accounts.due(usage, fetch, NOW + 10, INTERVAL, None, None)  # 別のラッパー（tried を持たない）も記録の at で数える
    assert accounts.due(usage, fetch, NOW + INTERVAL, INTERVAL, None, None)
    assert not accounts.due(usage, fetch, NOW + INTERVAL, INTERVAL, None, NOW + 10)  # このラッパーがキューへ入れた時刻
    fetch.write_text("{", encoding="utf-8")  # 読めない取得の記録は at が無い
    assert accounts.due(usage, fetch, NOW + 10, INTERVAL, None, None)


def test_i16_due_once_per_reset_after_the_last_attempt(tmp_path):
    usage = accounts.usage_path(tmp_path / "state", "work1", "g1")
    fetch = _attempted(tmp_path, NOW, {"five_hour": {"percent": 100.0, "resets_at": NOW + 100}})
    assert not accounts.due(usage, fetch, NOW + 50, INTERVAL, None, None)
    assert accounts.due(usage, fetch, NOW + 150, INTERVAL, None, None)  # 最後に試した後にリセットを過ぎた
    # そのリセットで試した後は、取得が失敗して過ぎた窓が残っても真にならない
    _attempted(tmp_path, NOW + 120, {"five_hour": {"percent": 100.0, "resets_at": NOW + 100}})
    assert not accounts.due(usage, fetch, NOW + 150, INTERVAL, None, None)
    # status line のキャッシュのモデル別の枠のリセットと、上限の観測の until でも取り直す
    _attempted(tmp_path, NOW)
    accounts.write_usage(usage, {"seven_day_scoped/opus": (90.0, NOW + 100)}, NOW)
    assert accounts.due(usage, fetch, NOW + 150, INTERVAL, None, None)
    assert not accounts.due(usage, fetch, NOW + 150, INTERVAL, None, NOW + 120)
    fetch.unlink()
    usage.unlink()
    _attempted(tmp_path, NOW)
    assert accounts.due(usage, fetch, NOW + 150, INTERVAL, NOW + 100, None)
    assert not accounts.due(usage, fetch, NOW + 150, INTERVAL, NOW + 200, None)


def test_i16_fetch_lock_is_one_per_store_and_name_and_never_taken_from_a_live_owner(tmp_path, hold_lock):
    store = tmp_path / "store"  # 旧い登録簿（.fetch-locks も .usage-fetch も無い）でも親を作って通る
    lock = accounts.fetch_lock_path(store, "work1")
    assert lock == store / ".fetch-locks" / "work1.lock"
    with accounts.fetch_lock(store, "work1", 0) as got:
        assert got
        assert stat.S_IMODE(lock.parent.stat().st_mode) == 0o700
        with accounts.fetch_lock(store, "work1", 0) as again:  # 同じ名前の 2 つ目の取得は待たずに諦める
            assert not again
        with accounts.fetch_lock(store, "work2", 0) as other:
            assert other
    fd = hold_lock(lock)
    os.utime(lock, (NOW - 86400, NOW - 86400))  # 古いファイルでも、持ち主が生きている間は奪わない
    with accounts.fetch_lock(store, "work1", 0) as got:
        assert not got
    os.close(fd)  # 持ち主が死ねば取れる
    with accounts.fetch_lock(store, "work1", 0) as got:
        assert got


def test_i16_forget_drops_the_fetch_record_of_the_generation_but_not_the_lock(tmp_path):
    store, state = tmp_path / "store", tmp_path / "state"
    for g in ("old", "new"):
        accounts.write_fetch(accounts.fetch_path(store, "work1", g), _got(five_hour=(10.0, LATER)), NOW)
    with accounts.fetch_lock(store, "work1", 0):
        pass
    accounts.forget(state, store, "work1", "old")
    assert sorted(p.name for p in accounts.fetch_path(store, "work1", "new").parent.iterdir()) == ["new.json"]
    accounts.forget(state, store, "work1", "new")
    assert not accounts.fetch_path(store, "work1", "new").parent.exists()
    assert accounts.fetch_lock_path(store, "work1").is_file()


def test_i16_fetch_gate_close_waits_for_a_get_in_flight_and_refuses_new_ones():
    gate = accounts.FetchGate()
    assert gate.start_get() and gate.begin_refresh()
    closed = threading.Event()
    t = threading.Thread(target=lambda: (gate.close(), closed.set()))
    t.start()
    time.sleep(0.05)
    assert not closed.is_set()  # 送信中の GET と更新の予約が終わるまで待つ
    gate.end_get()
    time.sleep(0.05)
    assert not closed.is_set()
    gate.end_refresh()
    t.join(2)
    assert closed.is_set()
    assert not gate.start_get() and not gate.begin_refresh()  # 閉じた後は始めない
