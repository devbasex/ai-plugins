"""報告への追記（`ndf worker append`）の排他。報告の読み取り・公開と完了マーカーの作成を、呼んだ利用者の追記どうしで直列にする。

ロックは利用者ごとの状態の保存先（`PluginIdentity.state_dir`）の 1 つのファイル（`LOCK_FILE`）で取る。報告そのものを持つと、
報告を読めるだけの利用者も排他のロックを取って追記を止められ、公開のたびに inode が替わる。報告の隣に置くと、共有の保存先
（グループが書き込めるディレクトリにも報告を書ける）では、ほかの利用者がロックの名前を消せる。追記は短いので、報告ごとに分けない。
"""

from __future__ import annotations

import contextlib
import fcntl
import os
from collections.abc import Iterator

from ndf import identity
from ndf.contracts import worker_paths
from ndf.core import durable_locks

LOCK_FILE = "worker-append.lock"


def append(wd: worker_paths.WorkDir, paths: worker_paths.ReportPaths, body: str, *, done: bool) -> bool:
    """報告の末尾へ `body` を足し、`done` なら続けて完了マーカーを作る。報告が無ければ何もせず偽。

    待つ上限は `durable_locks.LOCK_WAIT` で、当たれば何も書かずに `durable_locks.StoreInUse`。
    """
    if worker_paths.entry_state(wd, paths.report.name) is None:
        return False
    with _held(paths):
        text = worker_paths.read_file(wd, paths.report.name)
        if text is None:
            return False
        worker_paths.write_file(wd, paths.report.name, text + body)
        if done:
            worker_paths.write_file(wd, paths.done.name, "")
        return True


@contextlib.contextmanager
def _held(paths: worker_paths.ReportPaths) -> Iterator[None]:
    """追記のロックを排他で持つ。上限まで待っても取れなければ `StoreInUse`、ロックの名前をほかの利用者が消せるなら `EntryUnsafe`。"""
    with worker_paths.open_or_create(identity.load().state_dir) as state:
        worker_paths.check_unshared(state)
        fd = worker_paths.open_lock(state, LOCK_FILE)
    try:
        durable_locks.wait_lock(
            lambda: _try_lock(fd),
            paths.report,
            lambda limit: f"報告のロックを {limit:g} 秒待っても取れない: {paths.report}",
        )
        yield
    finally:
        os.close(fd)


def _try_lock(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:  # noqa: NDF401 ファイル: 別の追記が持っていれば取れなかったと返す
        return False
    return True
