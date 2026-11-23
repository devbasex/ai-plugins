"""worker の報告の保存先のパスと、リンクをたどらない読み書き。

4 つのファイル（プロンプト・報告・完了マーカー・作業の結果ファイル）の名前の形と接尾辞は
このモジュールだけが持つ。手順書・テスト・使う側のサブコマンドは字面を持たずに `report_paths` を呼ぶ。
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import secrets
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Literal, NamedTuple, Optional

NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
PROMPT_SUFFIX = ".prompt.md"
REPORT_SUFFIX = ".report.md"
DONE_SUFFIX = ".done"
RESULT_SUFFIX = ".result.json"


class NameInvalid(ValueError):
    """worker の名前が `[A-Za-z0-9._-]` の 1〜64 文字でない。"""


class EntryError(ValueError):
    """作業ディレクトリの中の名前を安全に読み書きできない（`EntryUnsafe`・`EntryUnreadable` の親）。"""


class EntryUnsafe(EntryError):
    """名前がリンクか、リンク数が 1 の通常のファイルでない。`kind` は symlink・hardlink・directory・other。"""

    def __init__(self, name: str, kind: str) -> None:
        super().__init__(f"{name} はリンク数が 1 の通常のファイルでない（{kind}）")
        self.name = name
        self.kind = kind


class EntryUnreadable(EntryError):
    """通常のファイルを読めない、または UTF-8 として解けない。"""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"{name} を読めない: {reason}")
        self.name = name
        self.reason = reason


class WorkDir(NamedTuple):
    """開いた作業ディレクトリ。`path` は表示とパスを組むためだけに使い、操作は `fd` に対する相対で行う。"""

    path: Path
    fd: int


class ReportPaths(NamedTuple):
    """1 人の worker が読み書きする 4 つのファイルの絶対パス。"""

    prompt: Path
    report: Path
    done: Path
    result: Path


def report_paths(directory: Path, name: str) -> ReportPaths:
    """作業ディレクトリと worker の名前から 4 つの絶対パスを組む。ファイルの有無は見ない。

    リンクを解くのは `directory` だけで、名前を足した後のパスには当てない。
    """
    if not NAME_RE.fullmatch(name):
        raise NameInvalid(f"worker の名前は [A-Za-z0-9._-] の 1〜64 文字にする: {name!r}")
    base = directory.resolve()
    report = base / f"{name}{REPORT_SUFFIX}"
    return ReportPaths(
        prompt=base / f"{name}{PROMPT_SUFFIX}",
        report=report,
        done=report.with_name(report.name + DONE_SUFFIX),
        result=base / f"{name}{RESULT_SUFFIX}",
    )


@contextlib.contextmanager
def open_workdir(directory: Path) -> Iterator[WorkDir]:
    """作業ディレクトリを 1 回だけ開く。リンクを解くのはこの 1 回で、抜けるときに閉じる。"""
    path = directory.resolve()
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        yield WorkDir(path, fd)
    finally:
        os.close(fd)


def _unsafe_kind(st: os.stat_result) -> Optional[str]:
    """リンク数が 1 の通常のファイルなら None、そうでなければ見つけた種類。"""
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if stat.S_ISDIR(st.st_mode):
        return "directory"
    if not stat.S_ISREG(st.st_mode):
        return "other"
    if st.st_nlink != 1:
        return "hardlink"
    return None


def _check_name(name: str) -> None:
    """作業ディレクトリの直下の 1 要素でない名前（空・`.`・`..`・区切りを含む）を `EntryUnsafe`（`other`）で拒む。

    `dir_fd` は絶対パスを制限せず、`O_NOFOLLOW` と `follow_symlinks=False` は途中の要素のリンクに効かないため。
    """
    if name in ("", ".", "..") or "/" in name or "\0" in name or (os.altsep and os.altsep in name):
        raise EntryUnsafe(name, "other")


def entry_state(wd: WorkDir, name: str) -> Optional[os.stat_result]:
    """名前が無ければ None、リンク数が 1 の通常のファイルならその stat を返す。ほかは `EntryUnsafe`。"""
    _check_name(name)
    try:
        st = os.stat(name, dir_fd=wd.fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    kind = _unsafe_kind(st)
    if kind:
        raise EntryUnsafe(name, kind)
    return st


def read_file(wd: WorkDir, name: str) -> Optional[str]:
    """名前のファイルを UTF-8 で読む。無ければ None。

    開いた記述子を確かめ、同じ記述子から読むので、確かめの後に名前を差し替えられても読むのは確かめた inode である。
    """
    _check_name(name)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        fd = os.open(name, flags, dir_fd=wd.fd)
    except FileNotFoundError:
        return None
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise EntryUnsafe(name, "symlink") from None
        raise EntryUnreadable(name, e.strerror or str(e)) from None
    try:
        kind = _unsafe_kind(os.fstat(fd))
        if kind:
            raise EntryUnsafe(name, kind)
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read()
    except OSError as e:
        raise EntryUnreadable(name, e.strerror or str(e)) from None
    finally:
        os.close(fd)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise EntryUnreadable(name, str(e)) from None


def write_file(wd: WorkDir, name: str, text: str) -> None:
    """私有の一時ディレクトリに作った新しい inode を `name` へ公開する。既存のファイルを開かず、切り詰めない。

    置き換えを許すのは、呼んだ利用者が書き込める既存のファイルだけである（読み取り専用なら `PermissionError`）。
    後始末で消すのは、この呼び出しが作った `data` と、開いた一時ディレクトリと同じ inode の名前だけである。
    """
    _check_name(name)
    data = text.encode("utf-8")
    if entry_state(wd, name) is not None and not os.access(name, os.W_OK, dir_fd=wd.fd, follow_symlinks=False):
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(wd.path / name))
    tmp = f".{name}.{secrets.token_hex(8)}"
    os.mkdir(tmp, 0o700, dir_fd=wd.fd)
    try:
        tmp_fd = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=wd.fd)
    except OSError:
        with contextlib.suppress(OSError):
            os.rmdir(tmp, dir_fd=wd.fd)
        raise
    created = False
    private = False
    st: Optional[os.stat_result] = None
    try:
        st = os.fstat(tmp_fd)
        # setgid のディレクトリの下では 0o2700 になるので、権限は所有者のほかに与えていないことだけを見る
        private = stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid() and not st.st_mode & 0o077
        if not private:
            raise EntryUnsafe(tmp, "other")
        fd = os.open("data", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=tmp_fd)
        created = True
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace("data", name, src_dir_fd=tmp_fd, dst_dir_fd=wd.fd)
        created = False
    finally:
        try:
            if created:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink("data", dir_fd=tmp_fd)
        finally:
            os.close(tmp_fd)
        if st is not None:
            _remove_opened_dir(wd, tmp, st, quiet=not private)


def _remove_opened_dir(wd: WorkDir, name: str, opened: os.stat_result, *, quiet: bool) -> None:
    """名前が開いたディレクトリと同じ inode のときだけ `rmdir` する。`quiet` なら失敗を飲む（元の例外を残す）。"""
    try:
        now = os.stat(name, dir_fd=wd.fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (now.st_dev, now.st_ino) != (opened.st_dev, opened.st_ino):
        return
    try:
        os.rmdir(name, dir_fd=wd.fd)
    except OSError:
        if not quiet:
            raise


def remove_file(wd: WorkDir, name: str) -> Literal["absent", "removed"]:
    """確かめてから名前を消す。確かめの時点でリンクなら消さずに `EntryUnsafe`（リンクの先もリンクも残す）。

    確かめの後に名前をリンクへ差し替えられたときは、最後の要素をたどらない `unlink` がリンクの名前だけを消す。
    """
    _check_name(name)
    if entry_state(wd, name) is None:
        return "absent"
    os.unlink(name, dir_fd=wd.fd)
    return "removed"
