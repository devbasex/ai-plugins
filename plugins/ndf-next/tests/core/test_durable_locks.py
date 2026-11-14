"""ロックの待ちの上限と持ち主（B11）。flock は開いたファイルごとの排他なので、同じプロセスの 2 つ目の取得で待たせる。"""

from __future__ import annotations

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
