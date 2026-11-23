"""プランの公開で、置き換える前のプランの実効のアクセス権（所有者・グループ・ACL・モード）を新しいプランへ写す。

`ndf route render` の `_publish` だけが使う。モードだけでは読める利用者が決まらない（グループと ACL でも変わる）ので、
実効のアクセス権を全部写し、確かめられない・写せないときは `AccessError` で公開をやめる（PR 4319 の指摘 r4-2）。

- 置き換える前のプランは、リンクをたどらずに 1 回だけ開いた記述子から所有者・モード・ACL を読む（別々に名前を引くと、
  間に差し替えられたとき別の inode のモードと ACL を組み合わせる。指摘 r5-5）。置き換える直前にも名前が同じ inode を
  指すかを確かめ、違えば公開しない
- ACL は Linux では `/proc/self/fd` から `system.posix_acl_access` を、macOS では `acl_get_fd_np` で拡張 ACL を読む
  （macOS に `os.getxattr` は無い。指摘 r5-1）。ACL が無い・ファイルシステムが ACL を扱わないことを示すエラーだけを
  ACL 無しとして扱い、ほかの失敗は確かめられないとして止まる（指摘 r5-2）
- 新しいプランが親ディレクトリの既定の ACL から継いだ ACL は、既存のプランの ACL で置き換えるか外してからモードを写す
  （継いだ名前付きの項目のマスクを `fchmod` が広げない。指摘 r5-3）
- 所有者とグループは、新しいプランの実際の所有者とグループ（`fstat`）と比べて違えば変える（setgid のディレクトリでは
  グループが親から継がれる。指摘 r5-4）
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple, Optional

from ndf.contracts.worker_paths_open import ACL_TYPE_EXTENDED, darwin_acl_lib

_ACL_ATTR = "system.posix_acl_access"
# ACL を持たない・ファイルシステムが ACL を扱わないことを示す errno（Linux の `getxattr`・`removexattr`）
_NO_ACL = (errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP)
# ファイルシステムが ACL を扱わないことを示す errno（macOS の `acl_get_fd_np`。ACL の無いファイルは `ENOENT`）
_UNSUPPORTED = (errno.ENOTSUP, errno.EOPNOTSUPP)


class AccessError(Exception):
    """置き換える前のプランのアクセス権を確かめられない・写せない。"""


class Existing(NamedTuple):
    """置き換える前のプラン: リンクをたどらずに開いた記述子と、その `fstat`。"""

    st: os.stat_result
    fd: int


def _open_flags() -> int:
    if sys.platform == "darwin":
        return os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_PATH") and hasattr(os, "getxattr"):
        return os.O_PATH
    raise AccessError("この OS では置き換える前のプランの ACL を確かめられない")


def _lstat(out: Path) -> Optional[os.stat_result]:
    try:
        return os.lstat(out)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 置き換える前のプランが無ければ新規として 0600 のまま公開する
        return None


@contextlib.contextmanager
def existing(out: Path) -> Iterator[Optional[Existing]]:
    """置き換える前のプランを開いて渡す。無い・通常のファイルでなければ None（新規として 0600 のまま公開する）。

    シンボリックリンクはたどらない（`os.replace` が置き換えるのはリンクそのもので、リンクの先のアクセス権を写すと、
    別のファイルの公開範囲で worker の入力を出してしまう。指摘 r4-1）。開けない・開く間に差し替わったら `AccessError`。
    """
    st = _lstat(out)
    if st is None or not stat.S_ISREG(st.st_mode):
        yield None
        return
    try:
        fd = os.open(out, _open_flags() | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise AccessError(f"置き換える前のプランを開けない: {exc}") from exc
    try:
        held = os.fstat(fd)
        if (held.st_dev, held.st_ino) != (st.st_dev, st.st_ino):
            raise AccessError("置き換える前のプランが確かめる間に差し替わった")
        yield Existing(held, fd)
    finally:
        os.close(fd)


def apply(fd: int, src: Existing) -> None:
    """書き終えた新しいプラン `fd` へ、`src` の所有者・グループ・ACL・モードを写す。写せなければ `AccessError`。"""
    try:
        mine = os.fstat(fd)
        if (src.st.st_uid, src.st.st_gid) != (mine.st_uid, mine.st_gid):
            os.fchown(fd, src.st.st_uid, src.st.st_gid)
        if sys.platform == "darwin":
            _copy_acl_darwin(src.fd, fd)
        else:
            _copy_acl(src.fd, fd)
        os.fchmod(fd, stat.S_IMODE(src.st.st_mode))
    except OSError as exc:
        raise AccessError(str(exc)) from exc


def check_unchanged(out: Path, src: Existing) -> None:
    """置き換える直前に、`out` の名前がまだ `src` の inode を指すかを確かめる。違えば `AccessError`。"""
    now = _lstat(out)
    if now is None or (now.st_dev, now.st_ino) != (src.st.st_dev, src.st.st_ino):
        raise AccessError("置き換える前のプランがアクセス権を写す間に差し替わった")


def _copy_acl(src: int, dst: int) -> None:
    """Linux: `src` の POSIX のアクセス ACL を `dst` へ写す。`src` に無ければ `dst` が継いだ ACL を外す。"""
    try:
        acl: Optional[bytes] = os.getxattr(f"/proc/self/fd/{src}", _ACL_ATTR)
    except OSError as exc:
        if exc.errno not in _NO_ACL:
            raise AccessError(f"置き換える前のプランの ACL を読めない: {exc}") from exc
        acl = None
    if acl is not None:
        os.setxattr(dst, _ACL_ATTR, acl)
        return
    try:
        os.removexattr(dst, _ACL_ATTR)
    except OSError as exc:
        if exc.errno not in _NO_ACL:
            raise


def _copy_acl_darwin(src: int, dst: int) -> None:
    """macOS: `src` の拡張 ACL を `dst` へ写す。`src` に無ければ空の ACL を付けて `dst` が継いだ ACL を外す。"""
    libc = darwin_acl_lib()
    if libc is None:
        raise AccessError("macOS の ACL の関数を引けない")
    acl = libc.acl_get_fd_np(src, ACL_TYPE_EXTENDED)
    if not acl:
        err = ctypes.get_errno()
        if err in _UNSUPPORTED:  # 同じディレクトリの新しいプランも ACL を持てない
            return
        if err != errno.ENOENT:
            raise AccessError(f"置き換える前のプランの ACL を読めない: {os.strerror(err)}")
        acl = libc.acl_init(0)
        if not acl:
            raise AccessError("空の ACL を作れない")
    try:
        if libc.acl_set_fd_np(dst, acl, ACL_TYPE_EXTENDED) != 0:
            raise AccessError(f"ACL を写せない: {os.strerror(ctypes.get_errno())}")
    finally:
        libc.acl_free(acl)
