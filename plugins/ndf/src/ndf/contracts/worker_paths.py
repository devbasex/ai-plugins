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
    """名前がリンクか、リンク数が 1 の通常のファイルでない。`kind` は symlink・hardlink・directory・other・replaced・owner・acl。

    `owner` は `write_file` が置き換える既存のファイルの所有者・所有グループを新しい inode で保てないこと（`_inherit_access`）。
    `acl` は `write_file` が置き換える既存のファイルの拡張 ACL を新しい inode へ保てないこと（`_inherit_access`）。

    `replaced` は `open_workdir(create=True)` が最初に無かった名前を開いたとき、呼んだ利用者の持ち物でないディレクトリが現れたこと、
    または最初に無かった要素を経て開いた作業ディレクトリに、新しく作ったディレクトリには無いはずの名前があったこと（`check_entries`）。
    """

    def __init__(self, name: str, kind: str) -> None:
        super().__init__(f"{name} はリンク数が 1 の通常のファイルでない（{kind}）")
        self.name = name
        self.kind = kind


class RetiredKept(EntryError):
    """`staged_write` が退けた名前を、公開に落ちた後に元へ戻せなかった。`left` は名前と回収先のパスの組の並び。"""

    def __init__(self, left: list[tuple[str, Path]]) -> None:
        shown = "・".join(f"{name} は {path}" for name, path in left)
        super().__init__(f"退けた名前を元へ戻せなかった（元の名前が埋まっているなど。上書きせずに残した: {shown}）")
        self.left = left


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


def names_a_directory(raw: str) -> bool:
    """利用者が渡したファイルのパスの生の文字列が、ディレクトリを指す形（空・末尾の区切り・末尾の `.`・`..`）なら真。

    `Path` は末尾の `/`・`/.` を落とすので、`Path` にした後では `saved.json/` と `saved.json` を見分けられない。
    """
    last = raw
    for sep in (os.sep, os.altsep):
        if sep:
            last = last.rsplit(sep, 1)[-1]
    return last in ("", ".", "..")


@contextlib.contextmanager
def open_resolved(directory: Path) -> Iterator[WorkDir]:
    """祖先のリンクを解きながら作業ディレクトリを 1 回だけ開き、抜けるときに閉じる。`WorkDir.path` は解いた場所である。

    利用者が渡した保存先（`ndf worker run --result-file` の親）を、リンクを経た作業ディレクトリ（macOS の `/var`→`/private/var`
    など）でも開くため。解くときに開いた記述子をそのまま返すので、解いてから開き直すまでに名前を別の実ディレクトリへ
    差し替えられても差し替え先には当たらない。worker へは `WorkDir.path` を渡すので、worker の `ndf contracts emit --out`
    もリンクを経ずに開け、`check_dir_id` で同じディレクトリかを突き合わせられる。
    リンクとリンクの先の `..` は `_resolve_existing` と同じく確かめた親の記述子から 1 要素ずつ厳密に解き、`..` は記述子から
    カーネルに引かせる（検索権限の無いディレクトリを `..` で通れない）。`..` があれば、最初の `..` が打ち消す要素の手前までを
    そのように解き、打ち消す要素から先は `_open_dir_through_dotdot` と同じくリンクをたどらずに開く（打ち消す要素がリンクなら
    `EntryUnsafe`（`symlink`）、無い・ディレクトリでなければ `EntryUnsafe`（`other`））。作らない。無い要素は `FileNotFoundError`。
    参照先の無いリンク・リンクの循環は `EntryUnsafe`（`symlink`）、ディレクトリでない要素は `EntryUnsafe`（`other`）。
    """
    probe = Path(directory).absolute()
    parts = probe.parts
    cut = max(parts.index("..") - 1, 1) if ".." in parts else len(parts)
    walk = _Walk(probe.anchor)
    try:
        for part in parts[1:cut]:
            if part not in ("", ".") and not walk.enter(part, probe, strict=False):
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(walk.path / part))
        path = Path(os.path.abspath(walk.path.joinpath(*parts[cut:])))
        rest = [p for p in parts[cut:] if p not in ("", ".")]
        cancelled = _cancelled(rest)
        fds = walk.fds
        for i, part in enumerate(rest):
            if part == "..":
                if len(fds) > 1:
                    _leave_dir(fds, probe)
                continue
            flags = _SEARCH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            try:
                child, _ = _open_child(fds[-1], part, flags, probe, create=False)
            except FileNotFoundError:
                if i in cancelled:
                    raise EntryUnsafe(str(probe), "other") from None
                raise
            fds.append(child)
        fd = fds.pop()
    finally:
        walk.close()
    try:
        yield WorkDir(path, fd)
    finally:
        os.close(fd)


def _cancelled(parts: list[str]) -> set[int]:
    """`parts` のうち、後の `..` が打ち消す要素の位置。"""
    cancelled: set[int] = set()
    stack: list[int] = []
    for i, part in enumerate(parts):
        if part != "..":
            stack.append(i)
        elif stack:  # 根の親は根
            cancelled.add(stack.pop())
    return cancelled


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
    （`new/../new` で、観測の後に既存のディレクトリが `new` へ移されても既存とみなさない）。リンクの先として同じ場所へ
    戻ったとき（`new/../alias` の `alias` が `new` を指す）は厳密にたどれないので `EntryUnsafe`（`replaced`）で拒む。
    絶対パスのリンクなどで一度開いた場所を開き直したときは、前に開いたディレクトリと突き合わせ、違えば同じく拒む（`_Walk`）。
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
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            if missing:
                missing.pop()
            else:  # 解いた位置はリンクを含まないので、記述子から引いた `..` が持っている親と同じかを確かめて戻る
                walk.up()
            continue
        if not missing and walk.enter(part, walk.path / part, strict=False):
            continue
        missing.append(part)
    return missing


# リンクをたどる回数の上限（Linux の MAXSYMLINKS と同じ）。超えたらリンクの循環とみなす
_MAX_LINKS = 40


class _Walk:
    """`_resolve_existing` が解いている位置。`fds[i]` は根から `names[:i]` を開いた記述子で、抜けるまで開いたまま持つ。

    次の要素は `fds[-1]`（確かめた親）に対してだけ開き、`..` は 1 つ手前の記述子へ戻る。
    `seen` は開いた場所ごとの `(st_dev, st_ino)`、`absent` は無いと観測した場所で、リンクの先を解くときも共有する。
    絶対パスのリンクや `..` の後に同じ場所を開き直したとき、前に開いたディレクトリでなければ（間に親を差し替えられた）
    `EntryUnsafe`（`replaced`）で拒み、無いと観測した場所をリンクの先として厳密にたどるときも同じく拒む。
    """

    def __init__(self, root: str) -> None:
        self.root = root
        self.fds = [os.open(root, _SEARCH | os.O_DIRECTORY | os.O_CLOEXEC)]
        self.names: list[str] = []
        self.links = _MAX_LINKS
        st = os.fstat(self.fds[0])
        self.seen: dict[Path, tuple[int, int]] = {Path(root): (st.st_dev, st.st_ino)}
        self.absent: set[Path] = set()

    @property
    def path(self) -> Path:
        return Path(self.root).joinpath(*self.names)

    def up(self) -> None:
        """`..` で 1 つ手前へ戻る。`..` は今の記述子からカーネルに引かせ（検索権限が無ければ `PermissionError`）、
        手前の記述子と同じディレクトリでなければ `EntryUnsafe`（`replaced`）（`_leave_dir`）。"""
        if self.names:  # 根の親は根
            _leave_dir(self.fds, self.path)
            self.names.pop()

    def to_root(self) -> None:
        """絶対パスのリンクの先を解く前に根へ戻る（カーネルも `..` を通らずに根から引き直す）。"""
        while self.names:
            os.close(self.fds.pop())
            self.names.pop()

    def close(self) -> None:
        for fd in self.fds:
            os.close(fd)
        self.fds.clear()

    def enter(self, part: str, probe: Path, *, strict: bool) -> bool:
        """確かめた親の直下の `part` へ進む。リンクなら確かめた親から先を厳密に解く。

        無ければ、`strict` なら `EntryUnsafe`（`symlink`。リンクの先が無い）、そうでなければ偽を返し、無いと観測した場所に記録する。
        記録した場所は後に現れても開かず、`strict` なら `EntryUnsafe`（`replaced`）、そうでなければ偽にする。
        前に開いた場所を別のディレクトリとして開いたら `EntryUnsafe`（`replaced`）。
        ディレクトリでなければ（リンクの先を含む）`EntryUnsafe`（`other`）。
        """
        key = self.path / part
        if key in self.absent:
            if strict:
                raise EntryUnsafe(str(probe), "replaced")
            return False
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
            self.absent.add(key)
            return False
        self.fds.append(child)
        self.names.append(part)
        st = os.fstat(child)
        if self.seen.setdefault(key, (st.st_dev, st.st_ino)) != (st.st_dev, st.st_ino):
            raise EntryUnsafe(str(probe), "replaced")
        return True

    def _follow(self, target: str, probe: Path) -> None:
        """リンクの中身 `target` を、リンクのある親（絶対なら根）から 1 要素ずつ厳密に解く。"""
        self.links -= 1
        if self.links < 0:
            raise EntryUnsafe(str(probe), "symlink")
        if target.startswith("/"):
            self.to_root()
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
    `directory` に `..` があれば、字面で畳まずに `_open_dir_through_dotdot` で一度の走査で開く（利用者の `--out` などを受けるとき）。
    `origin` は `..` を含まないパス（`prepare_paths` が返したもの）にだけ渡す。
    """
    probe = Path(directory).absolute()
    path = Path(os.path.abspath(probe))
    if ".." in probe.parts:
        if origin is not None:
            raise ValueError(f"origin は `..` を含まないパスにだけ渡す: {str(directory)!r}")
        fd, created = _open_dir_through_dotdot(probe, create=create)
    else:
        fd, created = _open_dir_nofollow(path, create=create, origin=origin)
    try:
        yield WorkDir(path, fd, created)
    finally:
        os.close(fd)


def _open_dir_through_dotdot(probe: Path, *, create: bool) -> tuple[int, bool]:
    """`..` を含む絶対パスを、根から 1 要素ずつ確かめた親の記述子に対して開き、`..` もその記述子から引いて一度で開く。

    `os.path.abspath` は `alias/..` を字面で畳むが、カーネルは `alias` の指す先の親へ進み、検索権限の無い `private/..` では
    止まる。字面で畳んだ場所を開くと利用者のパスが指す先とは別の既存のファイルを書き換えるので、打ち消す要素も
    リンクをたどらずに開き（リンクなら `symlink`、無い・ディレクトリでなければ `other` の `EntryUnsafe`）、`..` は開いた
    記述子に対する `openat` で引く。カーネルがその要素の検索権限を確かめ、無ければ `PermissionError` で止まる。
    引いた親が手前で開いた記述子と同じディレクトリでなければ（間に要素を移された）`EntryUnsafe`（`replaced`）。
    確かめた記述子から先だけを開くので、確かめてから開くまでの差し替えは起きない。
    打ち消す要素は `create` でも作らない。打ち消さない要素は `_open_dir_nofollow` と同じく `_open_child` で開く（作る）。
    最後の位置も途中と同じ検索権限だけの記述子のまま返す（一覧の読み取り権限を求めない）。開いた記述子と、無かった要素を作ったかを返す。
    """
    parts = [p for p in probe.parts[1:] if p not in ("", ".")]
    cancelled = _cancelled(parts)
    fds = [os.open(probe.anchor, _SEARCH | os.O_DIRECTORY | os.O_CLOEXEC)]
    created = False
    try:
        for i, part in enumerate(parts):
            if part == "..":
                if len(fds) > 1:
                    _leave_dir(fds, probe)
                continue
            flags = _SEARCH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            try:
                child, made = _open_child(fds[-1], part, flags, probe, create=create and i not in cancelled)
            except FileNotFoundError:
                if i in cancelled:
                    raise EntryUnsafe(str(probe), "other") from None
                raise
            created = created or made
            fds.append(child)
        return fds.pop(), created
    finally:
        for fd in fds:
            os.close(fd)


def _leave_dir(fds: list[int], probe: Path) -> None:
    """`fds[-1]` から `..` を引き、`fds[-2]` と同じディレクトリなら `fds[-1]` を閉じて外す。違えば `EntryUnsafe`（`replaced`）。"""
    up = os.open("..", _SEARCH | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=fds[-1])
    try:
        now, before = os.fstat(up), os.fstat(fds[-2])
    finally:
        os.close(up)
    if (now.st_dev, now.st_ino) != (before.st_dev, before.st_ino):
        raise EntryUnsafe(str(probe), "replaced")
    os.close(fds.pop())


def dir_id(wd: WorkDir) -> tuple[int, int]:
    """開いた作業ディレクトリの `(st_dev, st_ino)`。書き込む別のプロセスへ渡し、開き直した場所と突き合わせる。"""
    st = os.fstat(wd.fd)
    return st.st_dev, st.st_ino


def check_dir_id(wd: WorkDir, expected: tuple[int, int]) -> None:
    """開いた作業ディレクトリが `expected` のディレクトリでなければ `EntryUnsafe`（`replaced`）。

    渡した側が保持している保存先を、名前が同じ別の実ディレクトリへ差し替えられたことを書き込む前に知るため。
    """
    if dir_id(wd) != expected:
        raise EntryUnsafe(str(wd.path), "replaced")


# 要素は検索権限だけで開ける記述子にする（読み取り権限の無い共有ディレクトリの配下でも、一覧を読めない書き込み用の保存先でも開ける）。
# `O_PATH` の記述子は `*at` の `dir_fd` に使える。`O_PATH` の無い OS は `O_SEARCH`、それも無ければ `O_RDONLY` に落とす
_SEARCH = getattr(os, "O_PATH", getattr(os, "O_SEARCH", os.O_RDONLY))


def _open_dir_nofollow(path: Path, *, create: bool = False, origin: Optional[Origin] = None) -> tuple[int, bool]:
    """絶対パスのディレクトリを根から 1 要素ずつ `O_NOFOLLOW` で開く。リンクか、ディレクトリでない要素は `EntryUnsafe`。

    どの要素も `_SEARCH` で開く（最後の要素も、名前を指定した作成・削除に要らない一覧の読み取り権限を求めない）。`create` が真なら、無い要素を親の記述子に対する `mkdir` で作る。
    `origin` があれば、末尾の `origin.fresh` 個の要素は呼ぶ側が解いた時点で無かったので、最初の `open` で開けても最初に
    無かった要素として扱い、その手前の要素（無ければ根）の記述子を `origin.anchor` と突き合わせる。
    `create` で作るのは末尾の `origin.fresh` 個だけで、その手前の解いた時点であった要素は作らずに開く。無くなっていれば
    `EntryUnsafe`（`replaced`）で止める（移された親の元の名前に空のディレクトリを作らない）。
    開いた記述子と、最初の `open` に無かった要素を経たか（`WorkDir.created`）を返す。
    """
    parts = path.parts[1:]
    fd = os.open(path.anchor, _SEARCH | os.O_DIRECTORY | os.O_CLOEXEC)
    created = False
    at = len(parts) - (origin.fresh if origin is not None else 0)
    try:
        _check_anchor(fd, origin, path, at=at, opened=0)
        for i, part in enumerate(parts):
            flags = _SEARCH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
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
    既存のファイルを置き換えるときは、確かめた時点のアクセス権（setuid などを除くモードとグループ）を新しい inode へ
    公開する前に写す（`_inherit_access`）。グループ権限で読んでいた利用者が、置き換えの後に読めなくならないため。
    所有者か所有グループを新しい inode で保てないときは、既存のファイルを置き換える前に `EntryUnsafe`（`owner`）を上げる
    （所有者に与えていた権限を別の利用者へ、グループに与えていた権限を別のグループへ与えない）。拡張 ACL のある
    ファイルは ACL ごと写し、ACL を写せなければ同じく `EntryUnsafe`（`acl`）を上げる（アクセスを広げない）。
    """
    with staged_write(wd, name, text):
        pass


@contextlib.contextmanager
def staged_write(wd: WorkDir, name: str, text: str, *, retire: Iterable[str] = ()) -> Iterator[dict[str, str]]:
    """`write_file` の一時ファイルを作ってアクセス権・ACL まで設定し、`retire` の名前を退けてから本体を走らせ、本体が終えたら `name` へ公開する。

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
    existing = entry_state(wd, name)
    if existing is not None and not os.access(name, os.W_OK, dir_fd=wd.fd, follow_symlinks=False):
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(wd.path / name))
    # 名前の長さは出力名に依らない（出力名が上限に近くても作れる）
    tmp = f".ndf-{secrets.token_hex(8)}"
    os.mkdir(tmp, 0o700, dir_fd=wd.fd)
    # 開けなければ作ったディレクトリだと確かめる記述子が無いので、名前を消さずに上げる（差し替えられた別の手の
    # 空のディレクトリを消さない）
    tmp_fd = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=wd.fd)
    created = False
    private = False
    published = False
    retired: list[tuple[str, str]] = []
    kept = True
    st: Optional[os.stat_result] = None
    tmp_path = wd.path / tmp
    try:
        st = os.fstat(tmp_fd)
        # setgid のディレクトリの下では 0o2700 になるので、権限は所有者のほかに与えていないことだけを見る
        private = stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid() and not st.st_mode & 0o077
        if not private:
            raise EntryUnsafe(tmp, "other")
        fd = os.open("data", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=tmp_fd)
        created = True
        with os.fdopen(fd, "wb") as f:
            if existing is not None:
                _inherit_access(f.fileno(), name, existing, _access_acl(wd, name, existing))
            f.write(data)
        actions: dict[str, str] = {}
        for entry in retire:
            actions[entry] = _retire(wd, entry, tmp_fd, f"r{len(retired)}", retired)
        yield actions
        os.replace("data", name, src_dir_fd=tmp_fd, dst_dir_fd=wd.fd)
        created = False
        published = True
    except BaseException as exc:
        left, kept = _restore_retired(wd, tmp_fd, tmp_path, retired)
        if left:
            raise RetiredKept(left) from exc
        raise
    finally:
        if published:
            _drop_retired(tmp_fd, retired)
        # 所有者・モードの確かめに落ちた記述子は作ったディレクトリだと確かめられないので、名前を消さずに閉じるだけにする。
        # 戻せなかった名前の中身を持つ一時ディレクトリも消さない
        _clean_tmp(wd, tmp_fd, tmp, st if private and kept else None, created=created, published=published)


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


def _restore_retired(wd: WorkDir, tmp_fd: int, tmp_path: Path, retired: list[tuple[str, str]]) -> tuple[list[tuple[str, Path]], bool]:
    """公開できなかったときに、退けた名前を元の名前が空いているときだけ戻す。

    `os.link` は名前が埋まっていれば `FileExistsError` で失敗するので、空いているかの確かめと戻すことが 1 回の
    呼び出しで済む（`rename` は埋まった名前を黙って上書きする）。リンクをたどらない（退けた後にリンクへ差し替えられても
    リンク自体を戻す）。戻せた名前は一時ディレクトリの側の名前を消す。
    戻せなかった名前と回収先のパスの組の並びと、一時ディレクトリを消してよいか（中身が残っていなければ真）を返す。
    元の失敗を上げるため、ここでは上げない。
    """
    left: list[tuple[str, Path]] = []
    empty = True
    for name, slot in reversed(retired):
        try:
            os.link(slot, name, src_dir_fd=tmp_fd, dst_dir_fd=wd.fd, follow_symlinks=False)
        except OSError:  # noqa: NDF401 ファイル: 戻せなければ一時ディレクトリに中身を残し、回収先を知らせる
            left.append((name, tmp_path / slot))
            empty = False
            continue
        try:
            os.unlink(slot, dir_fd=tmp_fd)
        except OSError:  # noqa: NDF401 ファイル: 元の名前へは戻せた。残った側の名前ごと一時ディレクトリを残す
            empty = False
    return left, empty


_ACL_ACCESS = "system.posix_acl_access"


class _AclUnknown:
    """既存のファイルに拡張 ACL があるかを確かめられなかった（`_access_acl`）。"""


_ACL_UNKNOWN = _AclUnknown()


def _access_acl(wd: WorkDir, name: str, existing: os.stat_result) -> "Optional[bytes] | _AclUnknown":
    """置き換える既存のファイルの POSIX ACL（`system.posix_acl_access` の値）。無ければ None。

    拡張 ACL のあるファイルでは、モードのグループのビットは所有グループの権限ではなく ACL のマスクなので、
    `_inherit_access` はビットだけを写さず ACL ごと写す。リンクをたどらずに `O_PATH` で開いた記述子を `/proc/self/fd`
    から引き、確かめた inode（`existing`）でなければ `EntryUnsafe`（`replaced`）。POSIX ACL を引けない OS
    （`O_PATH` か `os.getxattr` が無い）と、ACL に対応しないファイルシステムは None。それ以外で引けなければ
    `_ACL_UNKNOWN` を返し、`_inherit_access` は公開する前に止める（アクセスを広げない）。
    """
    if not hasattr(os, "O_PATH") or not hasattr(os, "getxattr"):
        return None
    fd = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=wd.fd)
    try:
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino) != (existing.st_dev, existing.st_ino):
            raise EntryUnsafe(name, "replaced")
        try:
            return os.getxattr(f"/proc/self/fd/{fd}", _ACL_ACCESS)
        except OSError as e:
            if e.errno in (errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP):
                return None
            return _ACL_UNKNOWN
    finally:
        os.close(fd)


def _inherit_access(fd: int, name: str, existing: os.stat_result, acl: "Optional[bytes] | _AclUnknown" = None) -> None:
    """置き換える既存のファイルのグループとモード（setuid・setgid・sticky を除く）を新しいファイル `fd` へ写す。

    モードの所有者・グループのビットは所有者・所有グループに当たるので、写すのは新しいファイルの所有者と所有グループが
    元と同じときだけである。所有者が違う（別の利用者のファイルをグループの書き込み権限で置き換える）か、グループを
    変えられない（呼んだ利用者が属さない）ときは、公開する前に `EntryUnsafe`（`owner`）を上げる（元の所有者が読めなかった
    内容を `group` の権限で読めるようにしない・グループの権限を元と違うグループへ与えない）。
    拡張 ACL の無いファイルでは、一時ファイルが親のデフォルト ACL から継いだ ACL を先に外す（継いだ名前付きの項目が
    元のグループのビットをマスクにして読めるようにならないため）。外せなければ `EntryUnsafe`（`acl`）。
    既存のファイルに拡張 ACL（`acl`）があれば ACL ごと写す。ACL を設定できない・確かめられなかったときは、公開する前に
    `EntryUnsafe`（`acl`）を上げる（元の ACL で拒まれていた利用者へ `other` の権限を与えない）。
    """
    if os.fstat(fd).st_uid != existing.st_uid:
        raise EntryUnsafe(name, "owner")
    if existing.st_gid != os.fstat(fd).st_gid:
        try:
            os.fchown(fd, -1, existing.st_gid)
        except PermissionError:  # 属さないグループへは変えられない
            raise EntryUnsafe(name, "owner") from None
    mode = stat.S_IMODE(existing.st_mode) & 0o777
    if acl is None:
        _drop_inherited_acl(fd, name)
        os.fchmod(fd, mode)
        return
    if not isinstance(acl, bytes):
        raise EntryUnsafe(name, "acl")
    os.fchmod(fd, mode)
    try:
        os.setxattr(fd, _ACL_ACCESS, acl)
    except OSError:
        raise EntryUnsafe(name, "acl") from None


def _drop_inherited_acl(fd: int, name: str) -> None:
    """新しいファイル `fd` が親のデフォルト ACL から継いだ拡張 ACL を外す。外せなければ `EntryUnsafe`（`acl`）。

    ACL を外せない OS（`os.removexattr` が無い）と、ACL が無い・ACL に対応しないファイルシステムは何もしない。
    """
    if not hasattr(os, "removexattr"):
        return
    try:
        os.removexattr(fd, _ACL_ACCESS)
    except OSError as e:
        if e.errno not in (errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP):
            raise EntryUnsafe(name, "acl") from None


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
