"""報告への追記（`ndf worker append`）の排他。報告の読み取り・公開と完了マーカーの作成を、同じ報告の追記どうしで直列にする。

ロックは報告の隣の、追記する利用者だけが開けるファイル（`LOCK_SUFFIX`）で取る。報告そのものを持つと、報告を読めるだけの
利用者も排他のロックを取って追記を止められ、公開のたびに inode が替わる。
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import time
from collections.abc import Iterator

from ndf.contracts import worker_paths
from ndf.core import durable_locks

LOCK_SUFFIX = ".append-lock"


def append(wd: worker_paths.WorkDir, paths: worker_paths.ReportPaths, body: str, *, done: bool) -> bool:
    """報告の末尾へ `body` を足し、`done` なら続けて完了マーカーを作る。報告が無ければ何もせず偽。

    待つ上限は `durable_locks.LOCK_WAIT_SECONDS` で、当たれば何も書かずに `durable_locks.StoreInUse`。
    """
    if worker_paths.entry_state(wd, paths.report.name) is None:
        return False
    with _held(wd, paths):
        text = worker_paths.read_file(wd, paths.report.name)
        if text is None:
            return False
        worker_paths.write_file(wd, paths.report.name, text + body)
        if done:
            worker_paths.write_file(wd, paths.done.name, "")
        return True


@contextlib.contextmanager
def _held(wd: worker_paths.WorkDir, paths: worker_paths.ReportPaths) -> Iterator[None]:
    """報告のロックを排他で持つ。上限まで待っても取れなければ `StoreInUse`。"""
    fd = worker_paths.open_lock(wd, paths.report.name + LOCK_SUFFIX)
    try:
        deadline = time.monotonic() + durable_locks.LOCK_WAIT_SECONDS
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                detail = f"報告のロックを {durable_locks.LOCK_WAIT_SECONDS:g} 秒待っても取れない: {paths.report}"
                raise durable_locks.StoreInUse(detail, (paths.report,))
            time.sleep(durable_locks.LOCK_POLL_SECONDS)
        yield
    finally:
        os.close(fd)


def _try_lock(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:  # noqa: NDF401 ファイル: 別の追記が持っていれば取れなかったと返す
        return False
    return True
