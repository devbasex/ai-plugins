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
from collections.abc import Iterable, Iterator
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
    """名前がリンクか、リンク数が 1 の通常のファイルでない。`kind` は symlink・hardlink・directory・other・replaced。

    `replaced` は `open_workdir(create=True)` が最初に無かった名前を開いたとき、呼んだ利用者の持ち物でないディレクトリが現れたこと、
    または最初に無かった要素を経て開いた作業ディレクトリに、新しく作ったディレクトリには無いはずの名前があったこと（`check_entries`）。
    """

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
    """開いた作業ディレクトリ。`path` は表示とパスを組むためだけに使い、操作は `fd` に対する相対で行う。

    `created` は、根からたどる途中で最初の `open` に無かった要素（作った・間に現れた）を経て開いたこと。
    """

    path: Path
    fd: int
    created: bool = False


class ReportPaths(NamedTuple):
    """1 人の worker が読み書きする 4 つのファイルの絶対パス。"""

    prompt: Path
    report: Path
    done: Path
    result: Path


def report_paths(directory: Path, name: str, *, resolve: bool = True) -> ReportPaths:
    """作業ディレクトリと worker の名前から 4 つの絶対パスを組む。ファイルの有無は見ない。

    `resolve` が真ならリンクを解くのは `directory` だけで、名前を足した後のパスには当てない（`prepare` が利用者の入力を受けるとき）。
    解くのは存在する要素までで、参照先の無いリンクは解かずに `EntryUnsafe`（`symlink`）で拒む（`_resolve_existing`）。
    偽なら `directory` も解かずに絶対パスにするだけである（`prepare` が返した解決済みのパスを受ける `append`・`collect`、
    supervise.py が選んだ状態の保存先を受ける `ndf answer work`）。
    """
    if not resolve:
        _check_worker_name(name)
        return _paths_under(Path(os.path.abspath(directory)), name)
    return prepare_paths(directory, name)[0]


class Origin(NamedTuple):
    """`prepare_paths` が解いた時点の観測。`open_workdir(origin=...)` へ渡し、開くときに突き合わせる。

    `fresh` は解いた時点で無かった末尾の要素の数（0 なら保存先があった）。`anchor` はその手前の、解いた時点で
    あった最も深いディレクトリ（`fresh` が 0 なら保存先そのもの）の `(st_dev, st_ino)` である。
    """

    fresh: int
    anchor: tuple[int, int]


def prepare_paths(directory: Path, name: str) -> tuple[ReportPaths, Origin]:
    """`report_paths(resolve=True)` と同じパスを組み、解いた時点の観測（`Origin`）を組で返す。

    `prepare` はその観測を `open_workdir(origin=...)` へ渡す。解いてから開くまでに別の実ディレクトリが無かった場所へ
    現れると、開く側は最初の `open` で開けてしまい、無かったことを自分では知れないため。あった親が別の
    ディレクトリへ差し替えられたことも、開く側は `anchor` との突き合わせで知る。
    """
    _check_worker_name(name)
    base, missing, anchor = _resolve_existing(directory)
    return _paths_under(base, name), Origin(missing, anchor)


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


def _resolve_existing(directory: Path) -> tuple[Path, int, tuple[int, int]]:
    """根の側から 1 要素ずつ、確かめた親の記述子に対して次の要素だけを開いて解き、無い要素からは解かずに足す。

    残った無い要素の数と、その手前の解いた最も深いディレクトリの `(st_dev, st_ino)` を組で返す。
    `Path.resolve()`（`strict=False`）は参照先の無いリンクも先のパスへ解くので、`create=True` の `open_workdir` が
    リンクの先にディレクトリを作ってしまう。ある要素（リンク自体を含む）を厳密に解き、解けなければ
    （参照先の無いリンク・リンクの循環）作る前に `EntryUnsafe`（`symlink`）で拒む。無い残りの要素は `open_workdir` が
    リンクを拒みながら作る。

    根の側から確かめるのは、末尾の側から確かめると `parent/child` が無いと観測した後に別の利用者の `parent` が
    現れたとき、`parent` を既存とみなして無い要素から外してしまうためである。根の側からなら、ある要素は
    その子を確かめる前にあったと観測しており、無いと観測した要素はその後に現れても無い要素に残る。
    各要素は確かめた親の記述子に対する `openat` で開き、絶対パスから解き直さない（`_Walk`）。解いている間に親を
    差し替えられても、子の観測は確かめた親のものになり、差し替え先で得た識別子を `anchor` に記録しない。
    返す `anchor` は最も深い記述子の `fstat` で、`open_workdir` が開いた記述子と突き合わせる。
    1 要素につき親の記述子に対する呼び出しだけで済むので、深さ d の保存先で調べる要素は d 個である。

    無い要素の後の `..` は無い要素を打ち消すだけにし、打ち消した後にまた要素が来れば、そこから確かめ直す。
    ただし一度無いと観測した場所（解いた親の下の名前）は、`..` の後に同じ場所へ戻っても無い要素として残す
    （`new/../new` で、観測の後に既存のディレクトリが `new` へ移されても既存とみなさない）。
    `open_workdir` は `os.path.abspath` で `..` を畳むので、返すパスに `..` を残すと開く場所と食い違う。
    解いた要素はディレクトリであることを確かめ、通常のファイルなど（リンクの先を含む）なら次の `..` で
    その親へ進む前に `EntryUnsafe`（`other`）で拒む。
    """
    probe = directory.absolute()
    walk = _Walk(probe.anchor)
    try:
        missing = _walk_parts(walk, probe.parts[1:])
        st = os.fstat(walk.fds[-1])
        return walk.path.joinpath(*missing), len(missing), (st.st_dev, st.st_ino)
    finally:
        walk.close()


def _walk_parts(walk: "_Walk", parts: Iterable[str]) -> list[str]:
    """`parts` を `walk` の位置から順に解き、無いと観測した後の要素（`..` で打ち消した残り）を返す。"""
    missing: list[str] = []
    absent: set[Path] = set()
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            if missing:
                missing.pop()
            else:  # 解いた位置はリンクを含まないので、持っている親の記述子へ戻るだけでよい
                walk.up()
            continue
        candidate = walk.path / part
        if not missing and candidate not in absent and walk.enter(part, candidate, strict=False):
            continue
        if not missing:
            absent.add(candidate)
        missing.append(part)
    return missing


# リンクをたどる回数の上限（Linux の MAXSYMLINKS と同じ）。超えたらリンクの循環とみなす
_MAX_LINKS = 40


class _Walk:
    """`_resolve_existing` が解いている位置。`fds[i]` は根から `names[:i]` を開いた記述子で、抜けるまで開いたまま持つ。

    次の要素は `fds[-1]`（確かめた親）に対してだけ開き、`..` は 1 つ手前の記述子へ戻る。
    """

    def __init__(self, root: str) -> None:
        self.root = root
        self.fds = [os.open(root, _SEARCH | os.O_DIRECTORY | os.O_CLOEXEC)]
        self.names: list[str] = []
        self.links = _MAX_LINKS

    @property
    def path(self) -> Path:
        return Path(self.root).joinpath(*self.names)

    def up(self) -> None:
        if self.names:  # 根の親は根
            os.close(self.fds.pop())
            self.names.pop()

    def close(self) -> None:
        for fd in self.fds:
            os.close(fd)
        self.fds.clear()

    def enter(self, part: str, probe: Path, *, strict: bool) -> bool:
        """確かめた親の直下の `part` へ進む。リンクなら確かめた親から先を厳密に解く。

        無ければ、`strict` なら `EntryUnsafe`（`symlink`。リンクの先が無い）、そうでなければ偽を返す。
        ディレクトリでなければ（リンクの先を含む）`EntryUnsafe`（`other`）。
        """
        parent = self.fds[-1]
        try:
            child = _lookup(parent, part)
        except OSError as e:
            if e.errno not in (errno.ELOOP, errno.ENOTDIR):
                raise
            if _refused_kind(parent, part) != "symlink":
                raise EntryUnsafe(str(probe), "other") from None
            self._follow(os.readlink(part, dir_fd=parent), probe)
            return True
        if child is None:
            if strict:
                raise EntryUnsafe(str(probe), "symlink")
            return False
        self.fds.append(child)
        self.names.append(part)
        return True

    def _follow(self, target: str, probe: Path) -> None:
        """リンクの中身 `target` を、リンクのある親（絶対なら根）から 1 要素ずつ厳密に解く。"""
        self.links -= 1
        if self.links < 0:
            raise EntryUnsafe(str(probe), "symlink")
        if target.startswith("/"):
            while self.names:
                self.up()
        for part in target.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                self.up()
                continue
            self.enter(part, probe, strict=True)


def _lookup(parent: int, part: str) -> Optional[int]:
    """確かめた親 `parent` の直下の `part` を、リンクをたどらずにディレクトリとして開く。無ければ None。

    リンクか、ディレクトリでなければ `OSError`（`ELOOP`・`ENOTDIR`）を上げる。
    """
    try:
        return os.open(part, _SEARCH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 無い要素は無い要素として残す
        return None


def report_name(report: Path) -> tuple[Path, str]:
    """起動指示の「置く場所」（`<名前>.report.md`）から、作業ディレクトリと worker の名前を取り出す。形が違えば `NameInvalid`。"""
    name = report.name.removesuffix(REPORT_SUFFIX)
    if name == report.name or not NAME_RE.fullmatch(name):
        raise NameInvalid(f"置く場所は <[A-Za-z0-9._-] の 1〜64 文字>{REPORT_SUFFIX} の形にする: {str(report)!r}")
    return report.parent, name


@contextlib.contextmanager
def open_workdir(directory: Path, *, create: bool = False, origin: Optional[Origin] = None) -> Iterator[WorkDir]:
    """作業ディレクトリを 1 回だけ開き、抜けるときに閉じる。`WorkDir.path` は開いた場所そのものである。

    リンクは解かない。`directory` は `prepare` が利用者の入力を `report_paths` で解いた後のパスを渡す。根から 1 要素ずつリンクをたどらずに開くので、
    パスを組んだ後に途中の要素をリンクへ差し替えられると、別の場所を開かずに `EntryUnsafe`（`symlink`）で止まる。
    `create` が真なら、無い要素は開いた親の記述子に対する `mkdir` で作ってから開く（リンクの先に作らない）。
    最初に無かった要素が開くまでに呼んだ利用者の持ち物でないディレクトリになっていれば `EntryUnsafe`（`replaced`）で止める。
    最初に無かった要素を経て開いたときは `WorkDir.created` を真にする。オーナーは同じ利用者の既存のディレクトリへの
    差し替え（sticky bit の無い共有の親では、別の利用者も同じ親の中で改名できる）を見分けないので、初期化する側は
    書き換える前に `check_entries` で、新しく作ったディレクトリには無いはずの名前が無いことを確かめる。
    `origin` は呼ぶ側がパスを解いた時点の観測（`prepare_paths`）である。末尾の `origin.fresh` 個の要素は最初の `open` で
    開けても、開いた記述子を `_check_created` で確かめ、`WorkDir.created` を真にする（解いてから開くまでに現れた
    実ディレクトリを既存とみなさず、ほかの利用者の持ち物なら `EntryUnsafe`（`replaced`）で止める）。
    その手前の要素は作らずに開き、開いた記述子を `origin.anchor` と突き合わせし、無くなっているか解いた時点のディレクトリで
    なければ `EntryUnsafe`（`replaced`）で止める。
    組んだパスと開いた場所が食い違ったまま操作することは無い。
    """
    path = Path(os.path.abspath(directory))
    fd, created = _open_dir_nofollow(path, create=create, origin=origin)
    try:
        yield WorkDir(path, fd, created)
    finally:
        os.close(fd)


# 途中の要素は検索権限だけで開ける記述子にする（読み取り権限の無い共有ディレクトリの配下でも開ける）。
# `O_PATH` の記述子は `*at` の `dir_fd` に使える。`O_PATH` の無い OS は `O_SEARCH`、それも無ければ `O_RDONLY` に落とす
_SEARCH = getattr(os, "O_PATH", getattr(os, "O_SEARCH", os.O_RDONLY))


def _open_dir_nofollow(path: Path, *, create: bool = False, origin: Optional[Origin] = None) -> tuple[int, bool]:
    """絶対パスのディレクトリを根から 1 要素ずつ `O_NOFOLLOW` で開く。リンクか、ディレクトリでない要素は `EntryUnsafe`。

    途中の要素は `_SEARCH`、最後の要素だけ `O_RDONLY` で開く。`create` が真なら、無い要素を親の記述子に対する `mkdir` で作る。
    `origin` があれば、末尾の `origin.fresh` 個の要素は呼ぶ側が解いた時点で無かったので、最初の `open` で開けても最初に
    無かった要素として扱い、その手前の要素（無ければ根）の記述子を `origin.anchor` と突き合わせる。
    `create` で作るのは末尾の `origin.fresh` 個だけで、その手前の解いた時点であった要素は作らずに開く。無くなっていれば
    `EntryUnsafe`（`replaced`）で止める（移された親の元の名前に空のディレクトリを作らない）。
    開いた記述子と、最初の `open` に無かった要素を経たか（`WorkDir.created`）を返す。
    """
    parts = path.parts[1:]
    fd = os.open(path.anchor, (_SEARCH if parts else os.O_RDONLY) | os.O_DIRECTORY | os.O_CLOEXEC)
    created = False
    at = len(parts) - (origin.fresh if origin is not None else 0)
    try:
        _check_anchor(fd, origin, path, at=at, opened=0)
        for i, part in enumerate(parts):
            mode = os.O_RDONLY if i == len(parts) - 1 else _SEARCH
            flags = mode | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            observed = None if origin is None else i < at  # 解いた時点であった要素は、アンカーと突き合わせる前に作らない
            child, made = _open_part(fd, part, flags, path, create=create, observed=observed)
            created = created or made
            os.close(fd)
            fd = child
            _check_anchor(fd, origin, path, at=at, opened=i + 1)
    except BaseException:
        os.close(fd)
        raise
    return fd, created


def _open_part(fd: int, part: str, flags: int, path: Path, *, create: bool, observed: Optional[bool]) -> tuple[int, bool]:
    """`_open_child` で開く。`observed` は解いた時点であった（真）・無かった（偽、`fresh`）・観測が無い（None）である。

    あった要素は作らず、無ければ `EntryUnsafe`（`replaced`）にする。
    """
    try:
        return _open_child(fd, part, flags, path, create=create and not observed, fresh=observed is False)
    except FileNotFoundError:
        if observed:  # 解いた時点であった要素が無い: 移された・差し替えられた
            raise EntryUnsafe(str(path), "replaced") from None
        raise


def _check_anchor(fd: int, origin: Optional[Origin], path: Path, *, at: int, opened: int) -> None:
    """根から `opened` 個の要素を開いた記述子が `origin.anchor` の位置（`at`）なら、解いた時点のディレクトリと突き合わせる。

    違えば `EntryUnsafe`（`replaced`）。解いてから開くまでに、あった親を別のディレクトリへ差し替えられると、
    無いと観測した子は差し替え先では既にあるかもしれず、そこへ報告の保存先を作らないため。
    """
    if origin is None or opened != at:
        return
    st = os.fstat(fd)
    if (st.st_dev, st.st_ino) != origin.anchor:
        raise EntryUnsafe(str(path), "replaced")


def _open_child(fd: int, part: str, flags: int, path: Path, *, create: bool, fresh: bool = False) -> tuple[int, bool]:
    """`fd` の直下の `part` をリンクをたどらずに開く。`create` が真で無ければ `fd` に対する `mkdir` で作ってから開く。

    作ってから開き直すまでに、共有の親を書き換えられる手が名前を別の実ディレクトリへ差し替えられる（`O_NOFOLLOW` は
    実ディレクトリを拒まない）。最初に開けなかった名前は既存と確かめられていないので、`mkdir` で作れたときも、間に
    現れた（`FileExistsError`）ときも、開いた記述子を `_check_created` で確かめ、落ちれば初期化せずに
    `EntryUnsafe`（`replaced`）で止める。`fresh`（呼ぶ側が解いた時点で無かった）なら最初に開けたときも同じく確かめる。
    最初に開けた既存の要素なら偽、作った・間に現れた・`fresh` なら真を記述子と組で返す。
    """
    try:
        child = os.open(part, flags, dir_fd=fd)
    except FileNotFoundError:
        if not create:
            raise
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR):
            raise EntryUnsafe(str(path), _refused_kind(fd, part)) from None
        raise
    else:
        if not fresh:
            return child, False
        return _checked(child, path), True
    with contextlib.suppress(FileExistsError):  # noqa: NDF402 ファイル: 間に現れた名前も下の確かめを通す
        os.mkdir(part, dir_fd=fd)
    child, _ = _open_child(fd, part, flags, path, create=False)
    return _checked(child, path), True


def _checked(child: int, path: Path) -> int:
    """`_check_created` を通った記述子を返す。落ちれば閉じてから上げる。"""
    try:
        _check_created(child, path)
    except BaseException:
        os.close(child)
        raise
    return child


def _check_created(fd: int, path: Path) -> None:
    """最初に無かった名前で開いたディレクトリが、呼んだ利用者の持ち物であることを確かめる。違えば `EntryUnsafe`（`replaced`）。

    ここで止まるのは、ほかの利用者が持ち込んだディレクトリである（ほかの利用者は呼んだ利用者がオーナーのディレクトリを作れない）。
    オーナーが一致しても同一とは言えない。sticky bit の無い共有の親では、別の利用者が呼んだ利用者の既存のディレクトリを
    同じ親の中でこの名前へ改名でき、オーナーは変わらない。中身の有無も根拠にしない（同じ保存先を同じ利用者が別々の
    worker の名前で並行して準備すると、先に終えた呼び出しの報告が置かれた正規のディレクトリを開く）。そのため経たことを
    `WorkDir.created` で返し、初期化する側が `check_entries` で自分の名前が無いことを確かめる。
    以後の操作はこの記述子に対する相対なので、確かめた後に名前を差し替えられても差し替え先には当たらない。
    """
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
        raise EntryUnsafe(str(path), "replaced")


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


def _refused_kind(fd: int, part: str) -> str:
    """開けなかった要素の種類。Linux は `O_DIRECTORY | O_NOFOLLOW` でリンクを開くと `ENOTDIR` を返すので、`lstat` で見分ける。"""
    try:
        st = os.stat(part, dir_fd=fd, follow_symlinks=False)
    except OSError:  # noqa: NDF401 ファイル: 見分けられなければリンクでもディレクトリでもない要素として拒む
        return "other"
    return "symlink" if stat.S_ISLNK(st.st_mode) else "other"


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
