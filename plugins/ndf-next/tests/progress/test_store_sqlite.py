"""SQLite の進行表の DB の開き方・検査・控え・戻し（P1-1・P1-2・P1-5・P1-6。#3344 の決定 82・I9・I11）。"""

from __future__ import annotations

import multiprocessing as mp
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ndf.core import durable
from ndf.progress import model, store
from ndf.progress import backup as progress_backup

T0 = datetime(2026, 11, 14, 0, 0, tzinfo=UTC)


@pytest.fixture
def checks(monkeypatch):
    """`quick_check` を打った回数を数える。"""
    seen: list[Path] = []
    original = durable.check_file

    def counting(path: Path) -> bool:
        seen.append(path)
        return original(path)

    monkeypatch.setattr(durable, "check_file", counting)
    return seen


def _write_one(st: store.Store, name: str = "s1") -> str:
    with store.transaction(st) as conn:
        return store.create_table(
            conn, ("sprint", "r", name), None, {"stages": []}, [model.EventDraft("created", "conductor", "実装", rev=1)]
        )


def _names(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT name FROM ndf_progress_tables ORDER BY name")]
    finally:
        conn.close()


def test_opens_in_wal_with_a_30_second_busy_timeout(root):
    with store.open_store(store.default_url(root), root) as st, st.engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 30_000


def test_quick_check_runs_only_when_the_mark_is_old(root, checks):
    url = store.default_url(root)
    store.open_store(url, root, T0).close()
    assert len(checks) == 1
    store.open_store(url, root, T0 + timedelta(hours=1)).close()
    assert len(checks) == 1
    store.open_store(url, root, T0 + timedelta(hours=25)).close()
    assert len(checks) == 2


def test_broken_file_stops_without_writing(root, checks):
    path = root / store.DB_NAME
    with store.open_store(store.default_url(root), root, T0) as st:
        _write_one(st)
    with path.open("r+b") as f:
        f.write(b"\0" * 100)
    (root / store.CHECKED_MARK).unlink()
    before = path.stat()
    with pytest.raises(durable.StoreBroken, match="restore --from"):
        store.open_store(store.default_url(root), root, T0 + timedelta(hours=1))
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    assert not (root / store.CHECKED_MARK).exists()


def test_backup_after_the_first_check_of_the_day_only(root):
    url = store.default_url(root)
    store.open_store(url, root, T0).close()
    assert len(progress_backup.backups(root)) == 1
    (root / store.CHECKED_MARK).unlink()
    store.open_store(url, root, T0 + timedelta(hours=2)).close()
    assert len(progress_backup.backups(root)) == 1
    (root / store.CHECKED_MARK).unlink()
    store.open_store(url, root, T0 + timedelta(hours=25)).close()
    assert len(progress_backup.backups(root)) == 2


def test_eighth_backup_drops_the_oldest(root):
    with store.open_store(store.default_url(root), root, T0) as st:
        taken = [progress_backup.backup(st.engine, root, T0 + timedelta(days=i), force=True) for i in range(1, 9)]
    kept = progress_backup.backups(root)
    assert len(kept) == progress_backup.KEEP_BACKUPS
    assert taken[-1] in kept and progress_backup.backups(root)[0] == taken[1]


def test_failed_backup_leaves_no_partial_file_and_keeps_old_ones(root, monkeypatch):
    with store.open_store(store.default_url(root), root, T0) as st:
        old = progress_backup.backups(root)

        def broken(engine, dest):
            dest.write_bytes(b"partial")
            raise OSError("容量が足りない")

        monkeypatch.setattr(progress_backup, "_write_backup", broken)
        with pytest.raises(progress_backup.BackupFailed):
            progress_backup.backup(st.engine, root, T0 + timedelta(days=2), force=True)
    assert progress_backup.backups(root) == old


def test_table_version_bump_takes_a_backup_first(root):
    url = store.default_url(root)
    with store.open_store(url, root, T0) as st, store.transaction(st) as conn:
        conn.execute(store.VERSIONS.update().values(value=0))
    before = len(progress_backup.backups(root))
    store.open_store(url, root, T0 + timedelta(hours=1)).close()
    assert len(progress_backup.backups(root)) == before + 1


def _write_and_crash(url: str, root: str) -> None:
    """書き込んで、チェックポイントさせずにプロセスを落とす（-wal にフレームを残す）。"""
    path = store.sqlite_path(url)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("INSERT INTO ndf_progress_tables VALUES ('sprint:r:late', 'sprint', 'r', 'late', NULL, '2026-11-14')")
    conn.commit()
    os._exit(0)


def test_restore_puts_back_the_backup_and_moves_the_wal_aside(root):
    url = store.default_url(root)
    path = root / store.DB_NAME
    with store.open_store(url, root, T0) as st:
        _write_one(st)
        backup = progress_backup.backup(st.engine, root, T0 + timedelta(minutes=1), force=True)
    proc = mp.get_context("fork").Process(target=_write_and_crash, args=(url, str(root)))
    proc.start()
    proc.join()
    wal = Path(f"{path}-wal")
    assert wal.exists() and wal.stat().st_size > 0
    retired = store.restore(url, root, backup, T0 + timedelta(hours=1))
    assert _names(path) == _names(backup) == ["s1"]
    assert Path(f"{retired}-wal").exists() and not wal.exists()
    assert not path.with_name(f".{path.name}{progress_backup.RESTORING_SUFFIX}").exists()


def test_restore_targets_the_file_named_by_the_url(root, tmp_path, monkeypatch):
    outside = tmp_path / "elsewhere" / "mine.sqlite"
    monkeypatch.setenv("NDF_PROGRESS_DB", f"sqlite:///{outside}")
    url = store.default_url(root)
    with store.open_store(url, root, T0) as st:
        _write_one(st, "a")
        backup = progress_backup.backup(st.engine, root, T0 + timedelta(minutes=1), force=True)
        _write_one(st, "b")
    with store.open_store(f"sqlite:///{root / store.DB_NAME}", root, T0) as inside:
        _write_one(inside, "in")
    store.restore(url, root, backup, T0 + timedelta(hours=1))
    assert _names(outside) == ["a"]
    assert _names(root / store.DB_NAME) == ["in"]


def test_restore_stops_while_another_checkout_has_the_db_open(root, tmp_path):
    url = store.default_url(root)
    with store.open_store(url, root, T0) as st:
        backup = progress_backup.backup(st.engine, root, T0 + timedelta(minutes=1), force=True)
    other_root = tmp_path / "other" / ".git" / "ndf" / "state"
    other_root.mkdir(parents=True)
    # 別のチェックアウトが NDF_PROGRESS_DB でこの置き場の既定の DB を開いている（state.lock は別のもの）
    with store.open_store(url, other_root, T0), pytest.raises(durable.StoreInUse):
        store.restore(url, root, backup, T0 + timedelta(hours=1))
    assert _names(root / store.DB_NAME) == []


def test_restore_stops_while_the_store_is_in_use(root):
    url = store.default_url(root)
    with store.open_store(url, root, T0) as st:
        backup = progress_backup.backup(st.engine, root, T0 + timedelta(minutes=1), force=True)
    with durable.store_lock(root, exclusive=False), pytest.raises(durable.StoreInUse):
        store.restore(url, root, backup)
    assert not list(root.glob(f"*{progress_backup.BROKEN_MARK}*"))


def test_broken_backup_is_refused_and_nothing_moves(root):
    url = store.default_url(root)
    store.open_store(url, root, T0).close()
    bad = root / "bad.sqlite"
    bad.write_bytes(b"not a database" * 100)
    with pytest.raises(durable.StoreBroken):
        store.restore(url, root, bad)
    assert (root / store.DB_NAME).exists() and not list(root.glob(f"*{progress_backup.BROKEN_MARK}*"))


def test_db_lock_sits_next_to_the_real_file(root, tmp_path):
    # 既定の DB のロックは、置き場の退避が確かめる progress.sqlite.lock と同じ名前
    assert Path(f"{root / store.DB_NAME}{durable.LOCK_SUFFIX}").name == durable.PROGRESS_LOCK
    link = tmp_path / "link.sqlite"
    link.symlink_to(root / store.DB_NAME)
    assert store.sqlite_path(f"sqlite:///{link}") == store.sqlite_path(store.default_url(root))


def _open_once(url: str, root: str, log: str) -> None:
    original = durable.check_file

    def counting(path: Path) -> bool:
        with open(log, "a") as f:
            f.write("1\n")
        return original(path)

    durable.check_file = counting
    store.open_store(url, Path(root)).close()


def test_concurrent_openers_check_and_back_up_once(root, tmp_path):
    # P1-6: 目印の無い置き場を 8 つのプロセスが同時に開いても、検査と控えは 1 回だけ（maint_lock と目印の読み直し）
    url = store.default_url(root)
    log = tmp_path / "checks.log"
    ctx = mp.get_context("fork")
    procs = [ctx.Process(target=_open_once, args=(url, str(root), str(log))) for _ in range(8)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    assert all(p.exitcode == 0 for p in procs)
    assert log.read_text().count("1") == 1
    assert len(progress_backup.backups(root)) == 1
