"""ファイルロックの包み（#1142 の決定 19・種類 7）。filelock を呼ぶのはこのモジュールだけである。

`fcntl` を import するのもこのモジュールだけにする（構造チェックの I14）。filelock は Windows でも動く。

- 排他は守る対象と別の `<対象>.lock` で取る（filelock は錠のファイルを開くときに中身を消すため、対象そのものを
  錠にしない）。錠のファイルは消さない（消すと別のプロセスが別の inode で錠を取れてしまう）
- 待ちの上限は秒で渡す。`timeout=None` は取れるまで待ち、`0` は待たない。同じプロセスの中でも、別の呼び出しで取った排他とは互いに待つ

    with locks.exclusive(path, timeout=5):
        ...                       # 取れなければ LockTimeout
    if (held := locks.try_exclusive(path)) is not None:
        with held: ...

使う側は `deps.require("locks")` を先に呼ぶ。
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from filelock import FileLock, Timeout


class LockTimeout(TimeoutError):
    """待ちの上限までに排他を取れなかった。"""


def lock_path(target: os.PathLike[str] | str) -> Path:
    """`target` を守る錠のファイル（`<target>.lock`）。"""
    return Path(f"{os.fspath(target)}.lock")


def _file_lock(target: os.PathLike[str] | str) -> FileLock:
    path = lock_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(path))


@contextmanager
def exclusive(target: os.PathLike[str] | str, timeout: float | None = None) -> Iterator[None]:
    """`target` の排他を取った間だけ中を流す。`timeout` 秒で取れなければ `LockTimeout`。"""
    lock = _file_lock(target)
    try:
        lock.acquire(timeout=-1 if timeout is None else timeout)
    except Timeout:
        raise LockTimeout(f"{lock_path(target)} の排他を {timeout:g} 秒で取れない") from None
    try:
        yield
    finally:
        lock.release()


def try_exclusive(target: os.PathLike[str] | str) -> FileLock | None:
    """待たずに排他を取る。取れれば取った錠（`with` で外す）、取れなければ None。"""
    lock = _file_lock(target)
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return None
    return lock


def append_locked(target: os.PathLike[str] | str, line: str, timeout: float | None = 5.0) -> None:
    """排他を取って 1 行を末尾へ足す（改行は無ければ付ける）。並列に書く記録（jsonl）に使う。"""
    data = (line if line.endswith("\n") else line + "\n").encode("utf-8")
    with exclusive(target, timeout=timeout):
        fd = os.open(os.fspath(target), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
        finally:
            os.close(fd)
