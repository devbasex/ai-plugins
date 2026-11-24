"""ロックの待ちの上限とオーナー（B11）。flock は開いたファイルごとの排他なので、同じプロセスの 2 つ目の取得で待たせる。"""

from __future__ import annotations

import fcntl
import json
import os
import socket
from types import SimpleNamespace

import pytest

from ndf.core import durable_locks as locks


@pytest.fixture(autouse=True)
def short_wait(monkeypatch):
    monkeypatch.setattr(locks, "lock_wait_seconds", lambda: 0.2)


def test_lock_owner_without_a_record(tmp_path):
    path = tmp_path / "x.lock"
    path.write_text("")
    assert locks.lock_owner(path) == "記録が無い"
    assert locks.lock_owner(tmp_path / "missing.lock") == "記録が無い"


# --- 現状固定（I-021: `exclusive_lock` と `maint_lock`・`store_lock` の重なりを一つにする前） ----------------------


def _free(path) -> bool:
    """別に開いたファイルで、待たずに排他を取れるか（取れたらすぐ放す）。"""
    with open(path, "a") as handle:
        return locks.try_flock(handle, fcntl.LOCK_EX)


def _recorded_owner(path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


def test_exclusive_lock_makes_the_parent_writes_the_owner_and_releases(tmp_path):
    path = tmp_path / "a" / "b" / "plan.lock"
    with locks.exclusive_lock(path) as got:
        assert got == path and not _free(path)
        owner = _recorded_owner(path)
        assert (owner["host"], owner["pid"]) == (socket.gethostname(), os.getpid()) and set(owner) == {"host", "pid", "at"}
    assert _free(path)
    # 取り直すと前の記録を置き換える（追記しない）
    with locks.exclusive_lock(path):
        assert _recorded_owner(path)["pid"] == os.getpid()


def test_exclusive_lock_on_an_unwritable_place_is_config_broken(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    path = blocker / "plan.lock"
    with pytest.raises(locks.StoreConfigBroken) as err, locks.exclusive_lock(path):
        pass
    assert str(err.value).startswith(f"ロックのファイルを作れない: {path}: ") and err.value.paths == (path,)
    assert err.value.kind == "config_broken"


def test_exclusive_store_lock_does_not_wait(root, monkeypatch):
    path = locks.store_lock_path(root)
    monkeypatch.setattr(locks, "lock_wait_seconds", lambda: 60.0)
    with locks.store_lock(root, exclusive=True) as got:
        assert got == path and _recorded_owner(path)["pid"] == os.getpid()
        with pytest.raises(locks.StoreInUse) as err, locks.store_lock(root, exclusive=True):
            pass
    assert str(err.value) == f"保存先をほかのプロセスが使っている: {path}（オーナー: {locks.lock_owner(path)}）"
    assert err.value.paths == (path,)
    with locks.store_lock(root, exclusive=False), locks.store_lock(root, exclusive=False) as shared:
        assert shared == path


# --- 現状固定（4367 の I-001: ロックを上限まで待つループを 1 つの関数へまとめる前） ----------------------------------


@pytest.fixture
def clock(monkeypatch):
    """`time.sleep` が進める偽の時計。`sleeps` は待った秒の並び、`on_sleep` は待つたびに呼ぶ関数。"""
    fake = SimpleNamespace(now=100.0, sleeps=[], on_sleep=lambda: None)

    def sleep(seconds):
        fake.sleeps.append(seconds)
        fake.now += seconds
        fake.on_sleep()

    monkeypatch.setattr("time.monotonic", lambda: fake.now)
    monkeypatch.setattr("time.sleep", sleep)
    return fake


@pytest.mark.parametrize("mode", [fcntl.LOCK_EX, fcntl.LOCK_SH])
def test_waiting_lock_polls_to_the_limit_and_names_the_owner(tmp_path, clock, mode):
    path = tmp_path / "x.lock"
    with locks.exclusive_lock(path), locks.open_lock_file(path) as other:
        before, owner = path.read_text(encoding="utf-8"), locks.lock_owner(path)
        with pytest.raises(locks.StoreInUse) as err:
            locks.lock(other, mode, True, path)
        assert path.read_text(encoding="utf-8") == before
    assert str(err.value) == f"ロックを 0.2 秒待っても取れない: {path}（オーナー: {owner}）"
    assert err.value.paths == (path,) and err.value.kind == "store_in_use"
    # 上限を越えるまで、同じ間隔で待つ
    assert set(clock.sleeps) == {locks.LOCK_POLL_SECONDS} and sum(clock.sleeps) == pytest.approx(0.2, abs=locks.LOCK_POLL_SECONDS * 1.01)


def test_a_lock_released_during_the_last_wait_is_taken(tmp_path, clock, monkeypatch):
    # 上限を越える待ちの後にも 1 回試す
    monkeypatch.setattr(locks, "lock_wait_seconds", lambda: locks.LOCK_POLL_SECONDS / 2)
    path = tmp_path / "x.lock"
    with locks.open_lock_file(path) as held, locks.open_lock_file(path) as other:
        assert locks.try_flock(held, fcntl.LOCK_EX)
        clock.on_sleep = lambda: fcntl.flock(held, fcntl.LOCK_UN)
        assert locks.lock(other, fcntl.LOCK_EX, True, path) is True
        assert len(clock.sleeps) == 1 and _recorded_owner(path)["pid"] == os.getpid()


def test_a_lock_not_waited_for_returns_false_without_sleeping(tmp_path, clock):
    path = tmp_path / "x.lock"
    with locks.exclusive_lock(path), locks.open_lock_file(path) as other:
        assert locks.lock(other, fcntl.LOCK_EX, False, path) is False
    assert clock.sleeps == []


def test_a_waited_lock_with_no_time_left_is_tried_once(tmp_path, clock, monkeypatch):
    monkeypatch.setattr(locks, "lock_wait_seconds", lambda: 0.0)
    path = tmp_path / "x.lock"
    with locks.exclusive_lock(path), pytest.raises(locks.StoreInUse), locks.exclusive_lock(path):
        pass
    assert clock.sleeps == []


def test_a_shared_lock_does_not_record_the_owner(tmp_path):
    path = tmp_path / "x.lock"
    with locks.open_lock_file(path) as handle:
        assert locks.lock(handle, fcntl.LOCK_SH, True, path) is True
        assert path.read_text(encoding="utf-8") == ""
