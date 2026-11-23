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


def report_name(report: Path) -> tuple[Path, str]:
    """起動指示の「置く場所」（`<名前>.report.md`）から、作業ディレクトリと worker の名前を取り出す。形が違えば `NameInvalid`。"""
    name = report.name.removesuffix(REPORT_SUFFIX)
    if name == report.name or not NAME_RE.fullmatch(name):
        raise NameInvalid(f"置く場所は <[A-Za-z0-9._-] の 1〜64 文字>{REPORT_SUFFIX} の形にする: {str(report)!r}")
    return report.parent, name


@contextlib.contextmanager
def open_workdir(directory: Path) -> Iterator[WorkDir]:
    """作業ディレクトリを 1 回だけ開き、抜けるときに閉じる。`WorkDir.path` は開いた場所そのものである。

    リンクは解かない。`directory` は `report_paths` が解いた後のパスを渡す。根から 1 要素ずつリンクをたどらずに開くので、
    パスを組んだ後に途中の要素をリンクへ差し替えられると、別の場所を開かずに `EntryUnsafe`（`symlink`）で止まる。
    組んだパスと開いた場所が食い違ったまま操作することは無い。
    """
    path = Path(os.path.abspath(directory))
    fd = _open_dir_nofollow(path)
    try:
        yield WorkDir(path, fd)
    finally:
        os.close(fd)


def _open_dir_nofollow(path: Path) -> int:
    """絶対パスのディレクトリを根から 1 要素ずつ `O_NOFOLLOW` で開く。リンクか、ディレクトリでない要素は `EntryUnsafe`。"""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=fd)
            except OSError as e:
                if e.errno == errno.ELOOP:
                    raise EntryUnsafe(str(path), "symlink") from None
                if e.errno == errno.ENOTDIR:
                    raise EntryUnsafe(str(path), "other") from None
                raise
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    return fd


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
    if not _is_entry_name(name):
        raise EntryUnsafe(name, "other")


def _is_entry_name(name: str) -> bool:
    """作業ディレクトリの直下の 1 要素の名前（空・`.`・`..`・区切りを含まない）なら真。"""
    return not (name in ("", ".", "..") or "/" in name or "\0" in name or (os.altsep and os.altsep in name))


def entry_state(wd: WorkDir, name: str) -> Optional[os.stat_result]:
    """名前が無ければ None、リンク数が 1 の通常のファイルならその stat を返す。ほかは `EntryUnsafe`。"""
    _check_name(name)
    try:
        st = os.stat(name, dir_fd=wd.fd, follow_symlinks=False)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 名前が無いことは None で呼ぶ側へ返す
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
    except FileNotFoundError:  # noqa: NDF401 ファイル: 名前が無いことは None で呼ぶ側へ返す
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
    作業ディレクトリの権限（グループ書き込み・sticky bit の有無）では拒まない。一時ディレクトリの後始末は、作った名前で先に
    一致を確かめ、それで片付けられないとき（同じ親の別名へ移されたとき）だけ開いた記述子から今の名前を引いて、作業
    ディレクトリの直下で開いた記述子と同じ inode の名前だけを消す。記述子は後始末が終わるまで閉じない。開けなかった一時ディレクトリと、開いても所有者・モードの確かめに落ちた一時ディレクトリは作ったものだと確かめられないので消さない。消すのは名前で引く
    `rmdir` で、確かめた後に名前を差し替えられると差し替え先に当たりうるが、`rmdir` は空のディレクトリしか消さず
    リンクをたどらない。名前を差し替えるのに要る権限は同じ名前を消すのに要る権限と同じなので、差し替えた手は
    その空のディレクトリを自分で消せる。この呼び出しが消せるものは差し替えた手が消せるものを超えない。
    """
    _check_name(name)
    data = text.encode("utf-8")
    if entry_state(wd, name) is not None and not os.access(name, os.W_OK, dir_fd=wd.fd, follow_symlinks=False):
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(wd.path / name))
    tmp = f".{name}.{secrets.token_hex(8)}"
    os.mkdir(tmp, 0o700, dir_fd=wd.fd)
    # 開けなければ作ったディレクトリだと確かめる記述子が無いので、名前を消さずに上げる（差し替えられた別の手の
    # 空のディレクトリを消さない）
    tmp_fd = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=wd.fd)
    created = False
    private = False
    published = False
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
        published = True
    finally:
        # 所有者・モードの確かめに落ちた記述子は作ったディレクトリだと確かめられないので、名前を消さずに閉じるだけにする
        _clean_tmp(wd, tmp_fd, tmp, st if private else None, created=created, published=published)


def _clean_tmp(wd: WorkDir, tmp_fd: int, tmp: str, owned: Optional[os.stat_result], *, created: bool, published: bool) -> None:
    """`write_file` の一時ディレクトリを片付け、最後に記述子を閉じる。

    片付けるのは所有者・モードの確かめを通った（`owned` が `None` でない）一時ディレクトリだけである。突き合わせで確かめられるのは
    開いた対象と今の名前の一致までで、`mkdir` で作った対象かどうかは確かめられないため。
    記述子は inode の突き合わせと `rmdir` が終わるまで閉じない。開いている間は inode が解放されず、同じ番号を
    別のディレクトリが再利用して突き合わせを通ることが無い。
    公開した後（`published`）の片付けの失敗は上げない。名前は既に新しい inode を指しており、上げると呼び出し側は
    書き込みが起きなかったと読み違える。残るのは中身の無い一時ディレクトリだけである。
    """
    try:
        if created:
            with contextlib.suppress(FileNotFoundError):  # noqa: NDF402 ファイル: 公開できなかった一時ファイルの後始末。元の失敗を上げる
                os.unlink("data", dir_fd=tmp_fd)
        if owned is not None:
            _drop_tmp_dir(wd, tmp_fd, tmp, owned, published=published)
    finally:
        os.close(tmp_fd)


def _drop_tmp_dir(wd: WorkDir, tmp_fd: int, tmp: str, owned: os.stat_result, *, published: bool) -> None:
    """確かめを通った一時ディレクトリの名前を消す。公開した後の失敗は上げない（`_clean_tmp` の docstring）。"""
    if not published:
        _remove_opened_dir(wd, tmp_fd, tmp, owned)
        return
    with contextlib.suppress(OSError):  # noqa: NDF402 ファイル: 公開済み。空の一時ディレクトリを残して書き込みは成功として返す
        _remove_opened_dir(wd, tmp_fd, tmp, owned)


def _current_name(fd: int, original: str) -> Optional[str]:
    """開いたディレクトリの今の名前を記述子から引く。作った名前と同じか引けなければ None。

    共有の作業ディレクトリでは別の利用者が一時ディレクトリを同じ親の別名へ移せるので、作った名前で片付け
    られなかったときだけ、Linux の `/proc/self/fd` から今の名前を引く。
    """
    with contextlib.suppress(OSError):  # noqa: NDF402 ファイル: 今の名前を引けなければ None（片付けずに残す）
        current = os.path.basename(os.readlink(f"/proc/self/fd/{fd}"))
        if current and not current.endswith(" (deleted)") and current != original:
            return current
    return None


def _remove_opened_dir(wd: WorkDir, fd: int, original: str, opened: os.stat_result) -> None:
    """作業ディレクトリの直下で、開いたディレクトリ（`fd`）と同じ inode の名前を 1 つ `rmdir` する。

    作った名前で先に inode を突き合わせ、同じでなければ（移された・消されたとき）だけ記述子から今の名前を引いて突き合わせる。
    名前が開いたディレクトリでなければ消さない（差し替え先を消さない）。
    呼ぶ側は `fd` をこの関数が終わるまで閉じない。
    """
    if _remove_if_same(wd, original, opened):
        return
    current = _current_name(fd, original)
    if current is not None:
        _remove_if_same(wd, current, opened)


def _remove_if_same(wd: WorkDir, name: str, opened: os.stat_result) -> bool:
    """名前が開いたディレクトリと同じ inode なら `rmdir` して真を返す。同じでなければ偽。"""
    if not _is_entry_name(name):
        return False
    try:
        now = os.stat(name, dir_fd=wd.fd, follow_symlinks=False)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 名前が無ければ消すものは無い
        return False
    if (now.st_dev, now.st_ino) != (opened.st_dev, opened.st_ino):
        return False
    os.rmdir(name, dir_fd=wd.fd)
    return True


def remove_file(wd: WorkDir, name: str) -> Literal["absent", "removed"]:
    """確かめてから名前を消す。確かめの時点でリンクなら消さずに `EntryUnsafe`（リンクの先もリンクも残す）。

    確かめの後に名前をリンクへ差し替えられたときは、最後の要素をたどらない `unlink` がリンクの名前だけを消す。
    """
    _check_name(name)
    if entry_state(wd, name) is None:
        return "absent"
    os.unlink(name, dir_fd=wd.fd)
    return "removed"
