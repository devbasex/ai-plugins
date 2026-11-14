"""進行表の DB（SQLite）の控えと、控えから戻すときのファイルの操作（#3344 の決定 81・82）。

控えは置き場の `backup/progress-<UTC の日時>.sqlite` に新しい 7 本を残す。戻しは控えを一時名へ写して
`quick_check` を通してから、本体と -wal・-shm・-journal をそろえて退避し、一時名を本体の名前にする。
URL の解決とロックは呼び手（`store.restore`）が持つ。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import sqlalchemy as sa

from ndf.core import durable

BACKUP_DIR = "backup"
BACKUP_PREFIX = "progress-"
# 残す控えの本数（8 本目を取ると最も古い 1 本を消す）
KEEP_BACKUPS = 7
# 戻すときの一時名の接尾辞と、退避の名前の印
RESTORING_SUFFIX = ".restoring"
BROKEN_MARK = ".broken-"


class BackupFailed(Exception):
    """控えを書けなかった（容量不足など）。書きかけの控えは消してある。"""


def replace_db(root: Path, path: Path, source: Path, now: datetime) -> Path:
    """控え `source` で `path` を置き換え、退避した DB のパスを返す。呼び手が排他のロックを持つ。"""
    temp = _stage_backup(root, source, path)
    retired = _retire_db(path, now)
    _assert_retired(path)
    os.replace(temp, path)
    return retired


def backups(root: Path) -> list[Path]:
    """控えを古い順に（名前は UTC の日時を持つため、名前の順が時刻の順）。"""
    return sorted((root / BACKUP_DIR).glob(f"{BACKUP_PREFIX}*{durable.DB_SUFFIX}"))


def latest_backup(root: Path) -> Optional[Path]:
    found = backups(root)
    return found[-1] if found else None


def _write_backup(engine: sa.Engine, dest: Path) -> None:
    """SQLite の backup API で写し、写しを WAL でない形にする（戻した後に -wal・-shm を残さない）。"""
    raw = engine.raw_connection()
    try:
        target = sqlite3.connect(dest)
        try:
            raw.driver_connection.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
        finally:
            target.close()
    finally:
        raw.close()


def backup(engine: sa.Engine, root: Path, now: datetime, force: bool = False) -> Optional[Path]:
    """`backup/progress-<UTC の日時>.sqlite` を書き、新しい 7 本を残して古い控えを消す。

    最新の控えが 24 時間以内なら取らずに None（`force` なら日時に依らず取る）。書けなければ書きかけを消して
    `BackupFailed` を投げ、古い控えは消さない。SQLite のときだけ呼ぶ。
    """
    latest = latest_backup(root)
    if not force and latest is not None and durable.is_fresh(latest, now):
        return None
    dest = root / BACKUP_DIR / f"{BACKUP_PREFIX}{now:%Y%m%dT%H%M%S%fZ}{durable.DB_SUFFIX}"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write_backup(engine, dest)
    except (OSError, sqlite3.Error) as exc:
        dest.unlink(missing_ok=True)
        raise BackupFailed(f"進行表の DB の控えを書けない: {dest}: {exc}") from exc
    os.utime(dest, (now.timestamp(), now.timestamp()))
    for old in backups(root)[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    return dest


def _with_suffixes(path: Path) -> list[tuple[str, Path]]:
    return [(s, Path(f"{path}{s}")) for s in ("", *durable.SIDE_SUFFIXES)]


def _broken_name(path: Path, now: datetime) -> Path:
    base = path.with_name(f"{path.name}{BROKEN_MARK}{now:%Y%m%dT%H%M%SZ}")
    candidate, n = base, 1
    while any(p.exists() for _, p in _with_suffixes(candidate)):
        candidate = base.with_name(f"{base.name}-{n}")
        n += 1
    return candidate


def _retire_db(path: Path, now: datetime) -> Path:
    """本体と -wal・-shm・-journal をそろえて `<実ファイル>.broken-<日時>`（と同じ接尾辞）へ移す。"""
    target = _broken_name(path, now)
    for suffix, src in _with_suffixes(path):
        if src.exists():
            os.replace(src, Path(f"{target}{suffix}"))
    return target


def _next_backup(root: Path, source: Path) -> Optional[Path]:
    older = [p for p in backups(root) if p.resolve() != source.resolve()]
    return older[-1] if older else None


def _stage_backup(root: Path, source: Path, path: Path) -> Path:
    """控えを一時名へ写し、`quick_check` を通した一時名を返す。通らなければ一時名を消して `StoreBroken`。"""
    temp = path.with_name(f".{path.name}{RESTORING_SUFFIX}")
    shutil.copyfile(source, temp)
    if not durable.check_file(temp):
        temp.unlink(missing_ok=True)
        raise durable.StoreBroken(f"控え {source} が quick_check を通らない。次に新しい控え: {_next_backup(root, source)}", (source,))
    return temp


def _assert_retired(path: Path) -> None:
    """退避の後に -wal・-shm・-journal が残っていれば `StoreBroken`。"""
    left = [str(p) for s, p in _with_suffixes(path) if s and p.exists()]
    if left:
        raise durable.StoreBroken(f"退避の後に残ったファイルがある: {', '.join(left)}", (path,))
