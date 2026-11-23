"""作業ディレクトリを根から 1 要素ずつリンクをたどらずに解いて開くところ（`worker_paths` の下の層）。

解く・開く・突き合わせるの 3 つだけを持ち、名前の形と中の名前の読み書きは `worker_paths` が持つ。
公開の名前（`open_workdir`・`open_resolved`・`resolve_existing`・`dir_id`・`check_dir_id` と `WorkDir`・`Origin`・
`EntryUnsafe`）は `worker_paths` が引き直すので、使う側は `worker_paths` だけを読み込めばよい。
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import NamedTuple, Optional


class EntryError(ValueError):
    """作業ディレクトリの中の名前を安全に読み書きできない（`EntryUnsafe`・`EntryUnreadable` の親）。"""


class EntryUnsafe(EntryError):
    """名前がリンクか、リンク数が 1 の通常のファイルでない。`kind` は symlink・hardlink・directory・other・replaced・owner・acl・foreign。

    `foreign` は `read_file(owned=True)` が読むファイルを呼んだ利用者が持たないか、グループかほかの利用者が書き込めること。

    `owner` は `write_file` が置き換える既存のファイルの所有者・所有グループを新しい inode で保てないこと（`_inherit_access`）。
    `acl` は `write_file` が置き換える既存のファイルの拡張 ACL を保てないので置き換えないこと（`_refuse_acl`。ACL の値は写さない）。

    `replaced` は `open_workdir(create=True)` が最初に無かった名前を開いたとき、呼んだ利用者の持ち物でないディレクトリが現れたこと、
    または最初に無かった要素を経て開いた作業ディレクトリに、新しく作ったディレクトリには無いはずの名前があったこと（`check_entries`）。
    """

    def __init__(self, name: str, kind: str) -> None:
        super().__init__(f"{name} はリンク数が 1 の通常のファイルでない（{kind}）")
        self.name = name
        self.kind = kind


class WorkDir(NamedTuple):
    """開いた作業ディレクトリ。`path` は表示とパスを組むためだけに使い、操作は `fd` に対する相対で行う。

    `created` は、根からたどる途中で最初の `open` に無かった要素（作った・間に現れた）を経て開いたこと。
    """

    path: Path
    fd: int
    created: bool = False


class Origin(NamedTuple):
    """`prepare_paths` が解いた時点の観測。`open_workdir(origin=...)` へ渡し、開くときに突き合わせる。

    `fresh` は解いた時点で無かった末尾の要素の数（0 なら保存先があった）。`anchor` はその手前の、解いた時点で
    あった最も深いディレクトリ（`fresh` が 0 なら保存先そのもの）の `(st_dev, st_ino)` である。
    """

    fresh: int
    anchor: tuple[int, int]


@contextlib.contextmanager
def open_resolved(directory: Path) -> Iterator[WorkDir]:
    """祖先のリンクを解きながら作業ディレクトリを 1 回だけ開き、抜けるときに閉じる。`WorkDir.path` は解いた場所である。

    利用者が渡した保存先（`ndf worker run --result-file` の親）を、リンクを経た作業ディレクトリ（macOS の `/var`→`/private/var`
    など）でも開くため。解くときに開いた記述子をそのまま返すので、解いてから開き直すまでに名前を別の実ディレクトリへ
    差し替えられても差し替え先には当たらない。worker へは `WorkDir.path` を渡すので、worker の `ndf contracts emit --out`
    もリンクを経ずに開け、`check_dir_id` で同じディレクトリかを突き合わせられる。
    リンクとリンクの先の `..` は `resolve_existing` と同じく確かめた親の記述子から 1 要素ずつ厳密に解き、`..` は記述子から
    カーネルに引かせる（検索権限の無いディレクトリを `..` で通れない）。`..` があれば、最初の `..` が打ち消す要素の手前までを
    そのように解き、打ち消す要素から先は `_open_dir_through_dotdot` と同じくリンクをたどらずに開く（打ち消す要素がリンクなら
    `EntryUnsafe`（`symlink`）、無い・ディレクトリでなければ `EntryUnsafe`（`other`））。作らない。無い要素は `FileNotFoundError`。
    参照先の無いリンク・リンクの循環は `EntryUnsafe`（`symlink`）、ディレクトリでない要素は `EntryUnsafe`（`other`）。
    たどるのは信頼できる祖先のリンクだけで（`_trusted_link`）、ほかの利用者が書き込める共有の領域に置かれたリンクは
    `EntryUnsafe`（`symlink`）で拒む（共有の領域の保存先の名前を差し替えたリンクの先を、呼んだ利用者の権限で開かない）。
    """
    probe = Path(directory).absolute()
    parts = probe.parts
    cut = max(parts.index("..") - 1, 1) if ".." in parts else len(parts)
    walk = _Walk(probe.anchor)
    try:
        _enter_existing(walk, parts[1:cut], probe)
        path = Path(os.path.abspath(walk.path.joinpath(*parts[cut:])))
        _walk_cancelling(walk.fds, [p for p in parts[cut:] if p not in ("", ".")], probe, create=False)
        fd = walk.fds.pop()
    finally:
        walk.close()
    try:
        yield WorkDir(path, fd)
    finally:
        os.close(fd)


def _enter_existing(walk: "_Walk", parts: Iterable[str], probe: Path) -> None:
    """`parts` を `walk` の位置から順に厳密に解く。無い要素は `FileNotFoundError`（`open_resolved` は作らない）。"""
    for part in parts:
        if part not in ("", ".") and not walk.enter(part, probe, strict=False):
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(walk.path / part))


def _walk_cancelling(fds: list[int], parts: list[str], probe: Path, *, create: bool) -> bool:
    """`parts` を `fds[-1]` から 1 要素ずつリンクをたどらずに開き、`..` は開いた記述子から引いて 1 つ手前へ戻る。

    開いた記述子は `fds` へ積む（最後の 1 つを取り出し、残りを閉じるのは呼ぶ側である）。`..` が打ち消す要素は
    `create` でも作らず、無い・ディレクトリでなければ `EntryUnsafe`（`other`）。最初に無かった要素を経たかを返す。
    """
    cancelled = _cancelled(parts)
    created = False
    for i, part in enumerate(parts):
        if part == "..":
            if len(fds) > 1:  # 根の親は根
                _leave_dir(fds, probe)
            continue
        child, made = _open_cancelling(fds[-1], part, probe, create=create and i not in cancelled, cancels=i in cancelled)
        created = created or made
        fds.append(child)
    return created


def _open_cancelling(fd: int, part: str, probe: Path, *, create: bool, cancels: bool) -> tuple[int, bool]:
    """`fd` の直下の `part` を `_open_child` で開く。`cancels`（後の `..` が打ち消す要素）が無ければ `EntryUnsafe`（`other`）。"""
    flags = _SEARCH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        return _open_child(fd, part, flags, probe, create=create)
    except FileNotFoundError:
        if cancels:
            raise EntryUnsafe(str(probe), "other") from None
        raise


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


def resolve_existing(directory: Path) -> tuple[Path, int, tuple[int, int]]:
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
    リンクは `open_resolved` と同じく信頼できる祖先のものだけをたどり、共有の領域のリンクは `EntryUnsafe`（`symlink`）で拒む。
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
    """`resolve_existing` が解いている位置。`fds[i]` は根から `names[:i]` を開いた記述子で、抜けるまで開いたまま持つ。

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
            if not _trusted_link(parent, part):
                raise EntryUnsafe(str(probe), "symlink") from None
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


def _trusted_link(parent: int, part: str) -> bool:
    """親 `parent` の直下のリンク `part` をたどってよいか。ほかの利用者が差し替えられないリンクだけをたどる。

    親が root か呼んだ利用者の持ち物で、グループとほかの利用者が書き込めないなら真（macOS の `/`→`/tmp`・`/var` など）。
    書き込めても sticky bit があり（`/tmp` など）、リンク自体が root か呼んだ利用者の持ち物なら真（ほかの利用者は消せない）。
    それ以外（共有の領域に置かれた保存先の名前）は偽。たどると、名前を差し替えた利用者が選んだ場所を呼んだ利用者の権限で開く。
    """
    me = os.geteuid()
    up = os.fstat(parent)
    if up.st_uid not in (0, me):
        return False
    if not up.st_mode & 0o022:
        return True
    return bool(up.st_mode & stat.S_ISVTX) and os.stat(part, dir_fd=parent, follow_symlinks=False).st_uid in (0, me)


def _lookup(parent: int, part: str) -> Optional[int]:
    """確かめた親 `parent` の直下の `part` を、リンクをたどらずにディレクトリとして開く。無ければ None。

    リンクか、ディレクトリでなければ `OSError`（`ELOOP`・`ENOTDIR`）を上げる。
    """
    try:
        return os.open(part, _SEARCH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 無い要素は無い要素として残す
        return None


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
    fds = [os.open(probe.anchor, _SEARCH | os.O_DIRECTORY | os.O_CLOEXEC)]
    try:
        created = _walk_cancelling(fds, parts, probe, create=create)
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


def _refused_kind(fd: int, part: str) -> str:
    """開けなかった要素の種類。Linux は `O_DIRECTORY | O_NOFOLLOW` でリンクを開くと `ENOTDIR` を返すので、`lstat` で見分ける。"""
    try:
        st = os.stat(part, dir_fd=fd, follow_symlinks=False)
    except OSError:  # noqa: NDF401 ファイル: 見分けられなければリンクでもディレクトリでもない要素として拒む
        return "other"
    return "symlink" if stat.S_ISLNK(st.st_mode) else "other"
