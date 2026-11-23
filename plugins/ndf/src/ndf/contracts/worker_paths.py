"""worker の報告の保存先のパスと、リンクをたどらない読み書き。

4 つのファイル（プロンプト・報告・完了マーカー・作業の結果ファイル）の名前の形と接尾辞は
このモジュールだけが持つ。手順書・テスト・使う側のサブコマンドは字面を持たずに `report_paths` を呼ぶ。
作業ディレクトリを解いて開くところは `worker_paths_open` にあり、その公開の名前はここから引く
（使う側は `worker_paths` だけを読み込めばよい）。
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import secrets
import stat
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Literal, NamedTuple, Optional

from ndf.contracts.worker_paths_open import (
    EntryError,
    EntryUnsafe,
    Origin,
    WorkDir,
    check_dir_id,
    dir_id,
    open_resolved,
    open_workdir,
    resolve_existing,
)

__all__ = [
    "DONE_SUFFIX",
    "EntryError",
    "EntryUnreadable",
    "EntryUnsafe",
    "NAME_RE",
    "NameInvalid",
    "Origin",
    "PROMPT_SUFFIX",
    "REPORT_SUFFIX",
    "RESULT_SUFFIX",
    "ReportPaths",
    "RetiredKept",
    "WorkDir",
    "check_dir_id",
    "check_entries",
    "dir_id",
    "entry_state",
    "names_a_directory",
    "open_resolved",
    "open_workdir",
    "prepare_paths",
    "read_file",
    "remove_file",
    "report_name",
    "report_paths",
    "resolve_existing",
    "staged_write",
    "write_file",
]


NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")


PROMPT_SUFFIX = ".prompt.md"


REPORT_SUFFIX = ".report.md"


DONE_SUFFIX = ".done"


RESULT_SUFFIX = ".result.json"


class NameInvalid(ValueError):
    """worker の名前が `[A-Za-z0-9._-]` の 1〜64 文字でない。"""


class RetiredKept(EntryError):
    """`staged_write` が退けた名前を、公開に落ちた後に元へ戻せなかった。`left` は名前と回収先のパスの組の並び。

    回収先は一時ディレクトリの記述子から引いた今の場所で、その場所が開いた一時ディレクトリと同じ inode だと確かめられた
    ものだけを載せる（一時ディレクトリや親が改名・差し替えされていれば、作った時点のパスは別の場所を指しうる）。
    確かめられなければ None にし、回収先を確かめられないと書く。
    """

    def __init__(self, left: list[tuple[str, Optional[Path]]]) -> None:
        shown = "・".join(f"{name} は {path}" if path is not None else f"{name} は回収先を確かめられない" for name, path in left)
        super().__init__(f"退けた名前を元へ戻せなかった（元の名前が埋まっているなど。上書きせずに残した: {shown}）")
        self.left = left


class EntryUnreadable(EntryError):
    """通常のファイルを読めない、または UTF-8 として解けない。"""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"{name} を読めない: {reason}")
        self.name = name
        self.reason = reason


class ReportPaths(NamedTuple):
    """1 人の worker が読み書きする 4 つのファイルの絶対パス。"""

    prompt: Path
    report: Path
    done: Path
    result: Path


def report_paths(directory: Path, name: str, *, resolve: bool = True) -> ReportPaths:
    """作業ディレクトリと worker の名前から 4 つの絶対パスを組む。ファイルの有無は見ない。

    `resolve` が真ならリンクを解くのは `directory` だけで、名前を足した後のパスには当てない（`prepare` が利用者の入力を受けるとき）。
    解くのは存在する要素までで、参照先の無いリンクは解かずに `EntryUnsafe`（`symlink`）で拒む（`resolve_existing`）。
    偽なら `directory` も解かずに絶対パスにするだけである（`prepare` が返した解決済みのパスを受ける `append`・`collect`、
    supervise.py が選んだ状態の保存先を受ける `ndf answer work`）。
    """
    if not resolve:
        _check_worker_name(name)
        return _paths_under(Path(os.path.abspath(directory)), name)
    return prepare_paths(directory, name)[0]


def prepare_paths(directory: Path, name: str) -> tuple[ReportPaths, Origin]:
    """`report_paths(resolve=True)` と同じパスを組み、解いた時点の観測（`Origin`）を組で返す。

    `prepare` はその観測を `open_workdir(origin=...)` へ渡す。解いてから開くまでに別の実ディレクトリが無かった場所へ
    現れると、開く側は最初の `open` で開けてしまい、無かったことを自分では知れないため。あった親が別の
    ディレクトリへ差し替えられたことも、開く側は `anchor` との突き合わせで知る。
    """
    _check_worker_name(name)
    base, missing, anchor = resolve_existing(directory)
    return _paths_under(base, name), Origin(missing, anchor)


def names_a_directory(raw: str) -> bool:
    """利用者が渡したファイルのパスの生の文字列が、ディレクトリを指す形（空・末尾の区切り・末尾の `.`・`..`）なら真。

    `Path` は末尾の `/`・`/.` を落とすので、`Path` にした後では `saved.json/` と `saved.json` を見分けられない。
    """
    last = raw
    for sep in (os.sep, os.altsep):
        if sep:
            last = last.rsplit(sep, 1)[-1]
    return last in ("", ".", "..")


def _check_worker_name(name: str) -> None:
    if not NAME_RE.fullmatch(name):
        raise NameInvalid(f"worker の名前は [A-Za-z0-9._-] の 1〜64 文字にする: {name!r}")


def _paths_under(base: Path, name: str) -> ReportPaths:
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


def check_entries(wd: WorkDir, names: Iterable[str]) -> None:
    """初期化する前に `names` を 1 回ずつ `lstat` で確かめる。無い名前は通す。

    `wd.created` なら、ある名前は種類によらず `EntryUnsafe`（`replaced`）で止める。偽なら、ある名前は
    `entry_state` と同じくリンク数が 1 の通常のファイルだけを通し、ほかは見つけた種類の `EntryUnsafe` で止める。

    最初に無かった要素を経て開いたディレクトリは、空で作られた後に、この呼び出しか同じ利用者の並行した準備が別の名前を
    足したものしか持たないはずである。この呼び出しが初期化する名前（worker の名前は稼働中の worker と重ねない）が既にあれば、
    既存のディレクトリへ差し替えられたとみなし、その中の報告・完了マーカー・結果ファイルを空にも削除もしない。
    """
    for name in names:
        _check_name(name)
        st = _lstat_or_none(wd, name)
        if st is None:
            continue
        if wd.created:
            raise EntryUnsafe(name, "replaced")
        kind = _unsafe_kind(st)
        if kind:
            raise EntryUnsafe(name, kind)


def _lstat_or_none(wd: WorkDir, name: str) -> Optional[os.stat_result]:
    """作業ディレクトリの直下の `name` をリンクをたどらずに `stat` する。無ければ None（初期化してよい）。"""
    try:
        return os.stat(name, dir_fd=wd.fd, follow_symlinks=False)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 無い名前は初期化してよい
        return None


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
    既存のファイルを置き換えるときは、確かめた時点のアクセス権（setuid などを除くモードとグループ）を新しい inode へ
    公開する前に写す（`_inherit_access`）。グループ権限で読んでいた利用者が、置き換えの後に読めなくならないため。
    所有者か所有グループを新しい inode で保てないときは、既存のファイルを置き換える前に `EntryUnsafe`（`owner`）を上げる
    （所有者に与えていた権限を別の利用者へ、グループに与えていた権限を別のグループへ与えない）。
    拡張 ACL のあるファイルは ACL の値を写さずに、置き換える前に `EntryUnsafe`（`acl`）を上げる（`_refuse_acl`）。
    新しい inode が親の既定の ACL から継いだ ACL は、公開の前に外す（`_drop_inherited_acl`）。どちらも、モードのビット
    だけを写して置き換えると読める人が広がりうるためである（拡張 ACL のあるファイルでは、モードのグループのビットは
    所有グループの権限ではなく ACL のマスクである）。
    """
    with staged_write(wd, name, text):
        pass


@contextlib.contextmanager
def staged_write(wd: WorkDir, name: str, text: str, *, retire: Iterable[str] = ()) -> Iterator[dict[str, str]]:
    """`write_file` の一時ファイルを作ってアクセス権まで設定し、`retire` の名前を退けてから本体を走らせ、本体が終えたら `name` へ公開する。

    置き換えを拒む条件（読み取り専用・`EntryUnsafe`（`owner`・`acl`）など）は退ける前に上がる。`retire` の名前は
    `remove_file` と同じく確かめてから私有の一時ディレクトリへ移し（リンク数が 1 の通常のファイルでなければ `EntryUnsafe`）、
    公開できた後にだけ消す。退けた名前ごとの `absent`・`removed` を本体へ渡す（公開できたときの結果）。
    退けている途中・本体・公開（sticky bit の所有者の制約などで `os.replace` が落ちる）のどれかが上げれば、退けた名前を
    元の名前が空いているときだけ元へ戻し（`_restore_retired`）、一時ファイルを片付けて上げ直す。元の名前が埋まっている
    （退けた後に別の手が同じ名前へ書いた）などで戻せなかった名前があれば、上書きせずに一時ディレクトリごと残し、
    元の例外を原因にした `RetiredKept`（回収先のパスを持つ）を上げる。
    """
    _check_name(name)
    data = text.encode("utf-8")
    existing = _check_replaceable(wd, name)
    tmp, tmp_fd = _make_tmp_dir(wd)
    created = False
    published = False
    retired: list[tuple[str, str]] = []
    kept = True
    owned: Optional[os.stat_result] = None
    kept_error: Optional[RetiredKept] = None
    try:
        owned = _tmp_owned(tmp, tmp_fd)
        fd = os.open("data", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=tmp_fd)
        created = True
        _fill_data(wd, fd, name, data, existing)
        yield _retire_all(wd, tmp_fd, retire, retired)
        os.replace("data", name, src_dir_fd=tmp_fd, dst_dir_fd=wd.fd)
        created = False
        published = True
    except BaseException as exc:
        left, kept = _restore_retired(wd, tmp_fd, retired)
        if left:
            kept_error = RetiredKept(left)
            raise kept_error from exc
        raise
    finally:
        if published:
            _drop_retired(tmp_fd, retired)
        # 所有者・モードの確かめに落ちた記述子（`owned` が None）は作ったディレクトリだと確かめられないので、名前を消さずに
        # 閉じるだけにする。戻せなかった名前の中身を持つ一時ディレクトリも消さない
        if kept_error is None:
            _clean_tmp(wd, tmp_fd, tmp, owned if kept else None, created=created, published=published)
        else:
            # 回収先を持つ例外を、後始末（一時ファイルの削除）の失敗で覆わない。記述子は `_clean_tmp` の finally が閉じる
            with contextlib.suppress(OSError):  # noqa: NDF402 ファイル: 回収先を知らせる RetiredKept を上げるため
                _clean_tmp(wd, tmp_fd, tmp, None, created=created, published=False)


def _check_replaceable(wd: WorkDir, name: str) -> Optional[os.stat_result]:
    """置き換える既存のファイルの `stat`。無ければ None。呼んだ利用者が書き込めなければ `PermissionError`。"""
    existing = entry_state(wd, name)
    if existing is not None and not os.access(name, os.W_OK, dir_fd=wd.fd, follow_symlinks=False):
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(wd.path / name))
    return existing


def _make_tmp_dir(wd: WorkDir) -> tuple[str, int]:
    """作業ディレクトリの直下に私有の一時ディレクトリを作って開き、名前と記述子を返す。

    名前の長さは出力名に依らない（出力名が上限に近くても作れる）。開けなければ作ったディレクトリだと確かめる
    記述子が無いので、名前を消さずに上げる（差し替えられた別の手の空のディレクトリを消さない）。
    """
    tmp = f".ndf-{secrets.token_hex(8)}"
    os.mkdir(tmp, 0o700, dir_fd=wd.fd)
    return tmp, os.open(tmp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=wd.fd)


def _tmp_owned(tmp: str, tmp_fd: int) -> os.stat_result:
    """開いた一時ディレクトリが呼んだ利用者だけの持ち物であることを確かめ、その `stat` を返す。違えば `EntryUnsafe`（`other`）。"""
    st = os.fstat(tmp_fd)
    # setgid のディレクトリの下では 0o2700 になるので、権限は所有者のほかに与えていないことだけを見る
    if not (stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid() and not st.st_mode & 0o077):
        raise EntryUnsafe(tmp, "other")
    return st


def _fill_data(wd: WorkDir, fd: int, name: str, data: bytes, existing: Optional[os.stat_result]) -> None:
    """開いた一時ファイル `fd` から継いだ拡張 ACL を外し、置き換える既存のファイルのアクセス権を写してから中身を書き、閉じる。

    置き換える既存のファイルが拡張 ACL を持つなら、何も退けない・公開しないうちに `EntryUnsafe`（`acl`）で止まる。
    """
    with os.fdopen(fd, "wb") as f:
        _drop_inherited_acl(f.fileno(), name)  # 無い名前へ作るときも親の既定の ACL を継ぐ
        if existing is not None:
            _refuse_acl(wd, name, existing)
            _inherit_access(f.fileno(), name, existing)
        f.write(data)


_ACL_ACCESS = "system.posix_acl_access"


def _refuse_acl(wd: WorkDir, name: str, existing: os.stat_result) -> None:
    """置き換える既存のファイルが拡張 ACL を持つ・持つかを確かめられないなら `EntryUnsafe`（`acl`）。

    拡張 ACL のあるファイルでは、モードのグループのビットは所有グループの権限ではなく ACL のマスクなので、
    `_inherit_access` がモードのビットだけを写すと、置き換えの後に読める人が広がりうる（macOS の ACL は拒否の項目も
    持つので、外すと読める人が広がる）。ACL の値は写さず、置き換えないことで広がらないようにする。
    リンクをたどらずに開いた記述子を確かめた inode（`existing`）と突き合わせ、違えば `EntryUnsafe`（`replaced`）。
    Linux は `O_PATH` の記述子を `/proc/self/fd` から引いて `system.posix_acl_access` を見る（`_has_acl`）。
    macOS は書き込めることを確かめ済みの既存のファイルを書き込み専用で（切り詰めずに）開き、`acl_get_fd_np` で
    拡張 ACL を見る（`_has_acl_darwin`）。拡張 ACL の無いファイルと、ACL に対応しないファイルシステムは置き換える。
    どちらの手段も無い OS と、それ以外の失敗は持つかを確かめられないので置き換えない。
    """
    if hasattr(os, "O_PATH") and hasattr(os, "getxattr"):
        flags, check = os.O_PATH, _has_acl
    elif sys.platform == "darwin":
        flags, check = os.O_WRONLY | os.O_NONBLOCK, _has_acl_darwin
    else:
        raise EntryUnsafe(name, "acl")
    fd = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=wd.fd)
    try:
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino) != (existing.st_dev, existing.st_ino):
            raise EntryUnsafe(name, "replaced")
        if check(fd):
            raise EntryUnsafe(name, "acl")
    finally:
        os.close(fd)


def _has_acl(fd: int) -> bool:
    """`O_PATH` で開いた記述子（`/proc/self/fd` 経由）に拡張 ACL があるか。確かめられなければ真（置き換えない側へ倒す）。"""
    try:
        os.getxattr(f"/proc/self/fd/{fd}", _ACL_ACCESS)
    except OSError as e:
        return e.errno not in (errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP)
    return True


_ACL_TYPE_EXTENDED = 0x00000100  # macOS の <sys/acl.h>
_ACL_FIRST_ENTRY = 0


def _darwin_acl_lib():  # noqa: ANN202 ctypes の CDLL
    """macOS の libc の ACL 関数（`acl_get_fd_np`・`acl_get_entry`・`acl_init`・`acl_set_fd_np`・`acl_free`）。引けなければ None。"""
    import ctypes

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        get_fd, get_entry, init, set_fd, free = (
            libc.acl_get_fd_np,
            libc.acl_get_entry,
            libc.acl_init,
            libc.acl_set_fd_np,
            libc.acl_free,
        )
    except (OSError, AttributeError):
        return None
    get_fd.restype = init.restype = ctypes.c_void_p
    get_fd.argtypes = [ctypes.c_int, ctypes.c_int]
    get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    init.argtypes = [ctypes.c_int]
    set_fd.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    free.argtypes = [ctypes.c_void_p]
    return libc


def _has_acl_darwin(fd: int) -> bool:
    """macOS で開いた記述子に拡張 ACL の項目があるか（`acl_get_fd_np`）。確かめられなければ真（置き換えない側へ倒す）。

    ACL の無いファイルは `ENOENT`、ACL に対応しないファイルシステムは `ENOTSUP`・`EOPNOTSUPP` で NULL を返す。
    項目の無い空の ACL（`_drop_acl_darwin` が付けた）は持たないとみなす。
    """
    import ctypes

    libc = _darwin_acl_lib()
    if libc is None:
        return True
    acl = libc.acl_get_fd_np(fd, _ACL_TYPE_EXTENDED)
    if not acl:
        return ctypes.get_errno() not in (errno.ENOENT, errno.ENOTSUP, errno.EOPNOTSUPP)
    try:
        entry = ctypes.c_void_p()
        return libc.acl_get_entry(acl, _ACL_FIRST_ENTRY, ctypes.byref(entry)) == 0
    finally:
        libc.acl_free(acl)


def _drop_acl_darwin(fd: int, name: str) -> None:
    """macOS で新しい一時ファイル `fd` が親から継いだ拡張 ACL を、空の ACL を付けて外す（`chmod -N` と同じ）。

    継いだ許可の項目が残ると、置き換えの前に読めなかった利用者が新しい報告を読める。外した後に項目が残る・確かめられない・
    外せないなら `EntryUnsafe`（`acl`）で公開しない。
    """
    if not _has_acl_darwin(fd):
        return
    libc = _darwin_acl_lib()
    if libc is None:
        raise EntryUnsafe(name, "acl")
    empty = libc.acl_init(0)
    if not empty:
        raise EntryUnsafe(name, "acl")
    try:
        failed = libc.acl_set_fd_np(fd, empty, _ACL_TYPE_EXTENDED) != 0
    finally:
        libc.acl_free(empty)
    if failed or _has_acl_darwin(fd):
        raise EntryUnsafe(name, "acl")


def _drop_inherited_acl(fd: int, name: str) -> None:
    """新しい一時ファイル `fd` が親から継いだ拡張 ACL を、公開の前に外す。外せなければ `EntryUnsafe`（`acl`）。

    継いだ名前付きの項目が、写したモードのグループのビットをマスクにして（macOS では許可の項目のまま）読めるように
    ならないためである。Linux は `system.posix_acl_access` を `os.removexattr` で外し、macOS は `_drop_acl_darwin` で外す。
    ACL が無い・ACL に対応しないファイルシステムと、どちらの手段も無い OS は何もしない。
    """
    if sys.platform == "darwin":
        _drop_acl_darwin(fd, name)
        return
    if not hasattr(os, "removexattr"):
        return
    try:
        os.removexattr(fd, _ACL_ACCESS)
    except OSError as e:
        if e.errno not in (errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP):
            raise EntryUnsafe(name, "acl") from None


def _retire_all(wd: WorkDir, tmp_fd: int, retire: Iterable[str], retired: list[tuple[str, str]]) -> dict[str, str]:
    """`retire` の名前を順に退け、名前ごとの `absent`・`removed` を返す（`_retire`）。"""
    return {entry: _retire(wd, entry, tmp_fd, f"r{len(retired)}", retired) for entry in retire}


def _retire(wd: WorkDir, name: str, tmp_fd: int, slot: str, retired: list[tuple[str, str]]) -> str:
    """`name` を確かめてから一時ディレクトリの `slot` へ移し、`retired` に記録する。無ければ `absent`、移せば `removed`。

    確かめの後に名前をリンクへ差し替えられても、`rename` はリンクをたどらずにリンク自体を移す。
    """
    _check_name(name)
    if entry_state(wd, name) is None:
        return "absent"
    os.rename(name, slot, src_dir_fd=wd.fd, dst_dir_fd=tmp_fd)
    retired.append((name, slot))
    return "removed"


def _drop_retired(tmp_fd: int, retired: list[tuple[str, str]]) -> None:
    """公開できた後に、退けた名前を一時ディレクトリから消す。失敗は上げない（公開済み。残れば一時ディレクトリが残るだけ）。"""
    for _, slot in retired:
        with contextlib.suppress(OSError):  # noqa: NDF402 ファイル: 公開済み。消せなければ一時ディレクトリごと残す
            os.unlink(slot, dir_fd=tmp_fd)


def _restore_retired(wd: WorkDir, tmp_fd: int, retired: list[tuple[str, str]]) -> tuple[list[tuple[str, Optional[Path]]], bool]:
    """公開できなかったときに、退けた名前を元の名前が空いているときだけ戻す。

    `os.link` は名前が埋まっていれば `FileExistsError` で失敗するので、空いているかの確かめと戻すことが 1 回の
    呼び出しで済む（`rename` は埋まった名前を黙って上書きする）。リンクをたどらない（退けた後にリンクへ差し替えられても
    リンク自体を戻す）。戻せた名前は一時ディレクトリの側の名前を消す。
    戻せなかった名前と回収先のパス（`_tmp_location` で確かめられなければ None）の組の並びと、一時ディレクトリを
    消してよいか（中身が残っていなければ真）を返す。元の失敗を上げるため、ここでは上げない。
    """
    left: list[tuple[str, Optional[Path]]] = []
    empty = True
    for name, slot in reversed(retired):
        try:
            os.link(slot, name, src_dir_fd=tmp_fd, dst_dir_fd=wd.fd, follow_symlinks=False)
        except OSError:  # 戻せなければ一時ディレクトリに中身を残し、回収先を知らせる
            where = _tmp_location(tmp_fd)
            left.append((name, where / slot if where is not None else None))
            empty = False
            continue
        try:
            os.unlink(slot, dir_fd=tmp_fd)
        except OSError:  # 元の名前へは戻せた。残った側の名前ごと一時ディレクトリを残す
            empty = False
    return left, empty


def _inherit_access(fd: int, name: str, existing: os.stat_result) -> None:
    """置き換える既存のファイルのグループとモード（setuid・setgid・sticky を除く）を新しいファイル `fd` へ写す。

    モードの所有者・グループのビットは所有者・所有グループに当たるので、写すのは新しいファイルの所有者と所有グループが
    元と同じときだけである。所有者が違う（別の利用者のファイルをグループの書き込み権限で置き換える）か、グループを
    変えられない（呼んだ利用者が属さない）ときは、公開する前に `EntryUnsafe`（`owner`）を上げる（元の所有者が読めなかった
    内容を `group` の権限で読めるようにしない・グループの権限を元と違うグループへ与えない）。
    """
    if os.fstat(fd).st_uid != existing.st_uid:
        raise EntryUnsafe(name, "owner")
    if existing.st_gid != os.fstat(fd).st_gid:
        try:
            os.fchown(fd, -1, existing.st_gid)
        except PermissionError:  # 属さないグループへは変えられない
            raise EntryUnsafe(name, "owner") from None
    os.fchmod(fd, stat.S_IMODE(existing.st_mode) & 0o777)


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


def _tmp_location(fd: int) -> Optional[Path]:
    """開いた一時ディレクトリの今の絶対パス。記述子から引き（Linux の `/proc/self/fd`）、そのパスが同じ inode を指すと確かめられたときだけ返す。

    作った時点のパスは、一時ディレクトリや作業ディレクトリが改名されると、無い名前か差し替え先を指す。
    引けない・消された（` (deleted)`）・別の inode を指すときは None。
    """
    with contextlib.suppress(OSError):  # noqa: NDF402 ファイル: 確かめられなければ None（回収先を確かめられないと知らせる）
        where = os.readlink(f"/proc/self/fd/{fd}")
        if where.startswith("/") and not where.endswith(" (deleted)"):
            now, opened = os.stat(where, follow_symlinks=False), os.fstat(fd)
            if (now.st_dev, now.st_ino) == (opened.st_dev, opened.st_ino):
                return Path(where)
    return None


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
