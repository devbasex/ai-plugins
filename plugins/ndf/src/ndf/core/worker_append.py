"""報告への追記（`ndf worker append`）の排他。報告の読み取り・公開と完了マーカーの作成を、同じ報告の追記どうしで直列にする。

ロックのファイルを別に置かず、報告そのものを `flock` で持つ。公開は新しい inode への置き換えなので、取った後に名前が同じ inode を
指すかを確かめ、置き換わっていれば開き直す（先の追記を読まずに古い本文から公開しない）。
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import time
from collections.abc import Iterator

from ndf.contracts import worker_paths
from ndf.core import durable_locks


def append(wd: worker_paths.WorkDir, paths: worker_paths.ReportPaths, body: str, *, done: bool) -> bool:
    """報告の末尾へ `body` を足し、`done` なら続けて完了マーカーを作る。報告が無ければ何もせず偽。

    待つ上限は `durable_locks.LOCK_WAIT_SECONDS` で、当たれば何も書かずに `durable_locks.StoreInUse`。
    """
    with _held_report(wd, paths) as held:
        text = worker_paths.read_file(wd, paths.report.name) if held else None
        if text is None:
            return False
        worker_paths.write_file(wd, paths.report.name, text + body)
        if done:
            worker_paths.write_file(wd, paths.done.name, "")
        return True


@contextlib.contextmanager
def _held_report(wd: worker_paths.WorkDir, paths: worker_paths.ReportPaths) -> Iterator[bool]:
    """報告の今の inode を排他で持つ間だけ真を渡す。報告が無ければ偽を渡す。"""
    fd = _lock_current(wd, paths)
    try:
        yield fd is not None
    finally:
        if fd is not None:
            os.close(fd)


def _lock_current(wd: worker_paths.WorkDir, paths: worker_paths.ReportPaths) -> int | None:
    """排他のロックを取った報告の記述子。報告が無ければ None。待つ間に置き換わった報告は開き直す。"""
    deadline = time.monotonic() + durable_locks.LOCK_WAIT_SECONDS
    while True:
        fd = worker_paths.open_file(wd, paths.report.name)
        if fd is None:
            return None
        kept = False
        try:
            got = _try_lock(fd)
            kept = got and _is_current(wd, paths.report.name, fd)
            if kept:
                return fd
        finally:
            if not kept:
                os.close(fd)
        if time.monotonic() >= deadline:
            detail = f"報告を {durable_locks.LOCK_WAIT_SECONDS:g} 秒待っても持てない: {paths.report}"
            raise durable_locks.StoreInUse(detail, (paths.report,))
        if not got:
            time.sleep(durable_locks.LOCK_POLL_SECONDS)


def _try_lock(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:  # noqa: NDF401 ファイル: 別の追記が持っていれば取れなかったと返す
        return False
    return True


def _is_current(wd: worker_paths.WorkDir, name: str, fd: int) -> bool:
    """名前が今も開いた報告を指すか。"""
    now = worker_paths.entry_state(wd, name)
    opened = os.fstat(fd)
    return now is not None and (now.st_dev, now.st_ino) == (opened.st_dev, opened.st_ino)
