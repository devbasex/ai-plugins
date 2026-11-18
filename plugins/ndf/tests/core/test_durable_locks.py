"""ロックの待ちの上限とオーナー（B11）。flock は開いたファイルごとの排他なので、同じプロセスの 2 つ目の取得で待たせる。"""

from __future__ import annotations

import fcntl
import json
import os
import socket

import pytest

from ndf.core import durable
from ndf.core import durable_locks as locks


@pytest.fixture(autouse=True)
def short_wait(monkeypatch):
    monkeypatch.setattr(locks, "LOCK_WAIT_SECONDS", 0.2)


def _owner() -> str:
    return f"ホスト {socket.gethostname()}・pid {os.getpid()}"


def test_waiting_run_lock_times_out_with_the_owner(root):
    with durable.run_lock(root, "k"):
        with pytest.raises(durable.StoreInUse) as err, durable.run_lock(root, "k"):
            pass
    assert err.value.kind == "store_in_use"
    assert _owner() in str(err.value)
    with durable.run_lock(root, "k") as again:
        assert again is not None


def test_waiting_maint_lock_times_out_with_the_owner(root):
    with durable.maint_lock(root, wait=True):
        with pytest.raises(durable.StoreInUse) as err, durable.maint_lock(root, wait=True):
            pass
    assert _owner() in str(err.value)


def test_shared_store_lock_times_out_while_the_exclusive_is_held(root):
    with durable.store_lock(root, exclusive=True):
        with pytest.raises(durable.StoreInUse) as err, durable.store_lock(root, exclusive=False):
            pass
    assert _owner() in str(err.value)


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


def test_exclusive_lock_times_out_with_the_owner(tmp_path):
    path = tmp_path / "plan.lock"
    with locks.exclusive_lock(path):
        with pytest.raises(locks.StoreInUse) as err, locks.exclusive_lock(path):
            pass
    assert str(err.value) == f"ロックを 0.2 秒待っても取れない: {path}（オーナー: {locks.lock_owner(path)}）"
    assert _owner() in str(err.value) and err.value.paths == (path,) and err.value.kind == "store_in_use"
    assert _free(path)


def test_exclusive_lock_on_an_unwritable_place_is_config_broken(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    path = blocker / "plan.lock"
    with pytest.raises(locks.StoreConfigBroken) as err, locks.exclusive_lock(path):
        pass
    assert str(err.value).startswith(f"ロックのファイルを作れない: {path}: ") and err.value.paths == (path,)
    assert err.value.kind == "config_broken"


def test_maint_lock_waits_like_the_exclusive_lock_or_hands_none(root):
    path = root.parent / locks.MAINT_LOCK
    with locks.maint_lock(root, wait=True) as got:
        assert got == path and _recorded_owner(path)["pid"] == os.getpid()
        with locks.maint_lock(root, wait=False) as busy:
            assert busy is None
        with pytest.raises(locks.StoreInUse) as err, locks.exclusive_lock(path):
            pass
    assert str(err.value) == f"ロックを 0.2 秒待っても取れない: {path}（オーナー: {locks.lock_owner(path)}）"
    with locks.maint_lock(root, wait=False) as again:
        assert again == path


def test_exclusive_store_lock_does_not_wait(root, monkeypatch):
    path = locks.store_lock_path(root)
    monkeypatch.setattr(locks, "LOCK_WAIT_SECONDS", 60.0)
    with locks.store_lock(root, exclusive=True) as got:
        assert got == path and _recorded_owner(path)["pid"] == os.getpid()
        with pytest.raises(locks.StoreInUse) as err, locks.store_lock(root, exclusive=True):
            pass
    assert str(err.value) == f"保存先をほかのプロセスが使っている: {path}（オーナー: {locks.lock_owner(path)}）"
    assert err.value.paths == (path,)
    with locks.store_lock(root, exclusive=False), locks.store_lock(root, exclusive=False) as shared:
        assert shared == path
