"""判定役の起動の記録（標準出力・標準エラーなど）を残し、期限を過ぎたものを片付ける（#4363）。

記録は状態の保存先の下の記録専用のディレクトリ（`RECORD_DIR`）に置く。記録を公開する前に、記録ごとの登録ファイル（名前が
記録の名前と、記録の inode 番号・更新時刻（ナノ秒）・サイズを持ち、中身がその名前と同じファイル）を同じディレクトリに作る。
inode 番号は記録を消すと別のファイルに使われることがあるので、番号だけでは記録を見分けない。起動ごとの出力のディレクトリには、
中身がディレクトリの名前と同じ所有ファイルを置く。片付けで消すのは、中身まで合う登録ファイル、その 3 つの値がすべて合う記録、
中身の合う所有ファイルを持つ出力のディレクトリ（起動中か保存の途中でプロセスが止まって残ったもの）だけである。名前の形が同じ
だけのファイル・リンク・ディレクトリは消さない。登録ファイルは記録ごとに独立しているので、並行する実行が互いの登録を
失わない。専用のディレクトリはリンクをたどらずに開き、保存と削除は開いたディレクトリを基準にする（確かめた後でリンクへ
差し替えられても、先を触らない）。失敗は過程に残すだけで、判定の結果と駆動は変えない。
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Protocol

from ndf.contracts import limits

# 判定役の記録だけを置く、状態の保存先の下のディレクトリ
RECORD_DIR = "ndf-judge-records"
# 判定役の記録を残す秒
RECORD_KEEP = "cross-review.judge-record.keep"
# 起動ごとの出力のディレクトリの名前（接尾辞は `tempfile.mkdtemp` の 8 字）と、その中の所有ファイル
_HELD_NAME = r"round\d+-verify-[a-z0-9_]{8}"
_HELD = re.compile(_HELD_NAME)
_OWNER = "ndf-owner"
# 登録ファイルの名前は `<記録の名前>.owned-<inode 番号>-<更新時刻のナノ秒>-<サイズ>`（後ろの 3 つは `_record_identity`）
_MARK_INFIX = ".owned-"
_MARK = re.compile(
    rf"(?P<record>{_HELD_NAME}(?:\.out|\.err\.log|-launch\.json|-progress\.log))" + re.escape(_MARK_INFIX) + r"(?P<identity>\d+-\d+-\d+)"
)


class Noting(Protocol):
    """過程へ 1 行足せるもの（`drive._Run`）。"""

    def note(self, round_no: int, who: str, result: str, warning: Optional[str] = None) -> None: ...


@contextmanager
def _opened(directory: Path) -> Iterator[int]:
    """`directory` をリンクをたどらずに開いたファイル記述子。リンク・ディレクトリでないもの・読めないものは `OSError`。"""
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


def _sign(path: Path, signature: str) -> None:
    """中身が `signature` の、実行者だけが読めるファイルを新しく作る（前からあるファイルやリンクは開かない）。"""
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as out:
        out.write(signature)


def _signed(directory: int, name: str, signature: str) -> Optional[os.stat_result]:
    """開いたディレクトリ `directory` の `name` が、中身が `signature` の通常のファイルならその状態。無い・違うものは None。
    通常のファイルだと確かめてから開き、差し替えられてもリンクをたどらず、名前付きパイプを待たない。"""
    try:
        if not stat.S_ISREG(os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode):
            return None
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 無いものは登録も所有もしていない
        return None
    try:
        found, expected = os.fstat(fd), signature.encode()
        return found if stat.S_ISREG(found.st_mode) and os.read(fd, len(expected) + 1) == expected else None
    finally:
        os.close(fd)


def _record_identity(found: os.stat_result) -> str:
    """記録を見分ける値。消した記録の inode 番号を別のファイルが使っても、更新時刻は引き継がれない。"""
    return f"{found.st_ino}-{found.st_mtime_ns}-{found.st_size}"


def new_held(stem: Path) -> Path:
    """起動ごとの出力のディレクトリ（0700）を `stem` の隣に作り、所有ファイルを置く。作れなければ `OSError`。"""
    held = Path(tempfile.mkdtemp(prefix=f"{stem.name}-", dir=stem.parent))
    try:
        _sign(held / _OWNER, held.name)
    except OSError:
        shutil.rmtree(held, ignore_errors=True)
        raise
    return held


def _link_into(run: Noting, round_no: int, held: Path, stem: Path, into: int) -> None:
    """`held` の通常のファイルを、登録ファイルを置いてから、開いたディレクトリ `into` へハードリンクする。"""
    for record in list(held.iterdir()):  # 下で作る登録ファイルの元は、記録として数えない
        if record.name == _OWNER or record.is_symlink() or not record.is_file():
            continue
        name = held.name + record.name.removeprefix(stem.name)
        try:
            record.chmod(0o600)
            # 公開より先に登録する（間で止まっても、片付けの対象でない記録は残らない）
            mark = f"{name}{_MARK_INFIX}{_record_identity(record.stat())}"
            _sign(held / mark, mark)
            os.link(held / mark, mark, dst_dir_fd=into)  # 中身を書き終えたものだけが現れる
            os.link(record, name, dst_dir_fd=into)
        except OSError as exc:  # 診断の記録を移せなくても、判定の結果と駆動は変えない
            run.note(round_no, "verify", f"判定役の記録 {name} を移せない（{exc}）")


def keep(run: Noting, round_no: int, held: Path, stem: Path) -> None:
    """起動ごとのディレクトリ `held` の記録を、実行者だけが読めるファイルにして `stem` の隣の `RECORD_DIR` へ移し、`held` を消す。
    移し先の名前は、`stem` の名前の部分を `held` の名前（起動ごとに違う）に替えたものである。前からある同じ名前のファイルやリンクは
    置き換えない（`os.link` は移し先があれば失敗する）。移せない記録はディレクトリと一緒に消える。"""
    directory = stem.parent / RECORD_DIR
    try:
        directory.mkdir(mode=0o700, exist_ok=True)
        with _opened(directory) as into:
            _link_into(run, round_no, held, stem, into)
    except OSError as exc:
        run.note(round_no, "verify", f"判定役の記録のディレクトリ {RECORD_DIR} を使えず、記録を残さない（{exc}）")
    shutil.rmtree(held, ignore_errors=True)


def _drop_if_expired(run: Noting, round_no: int, directory: int, mark: re.Match[str], cutoff: float) -> None:
    """登録ファイル `mark` の記録が `cutoff` より古ければ、記録と登録ファイルを消す。中身が名前と違うものは NDF の登録では
    ないので残す。登録ファイルと合う記録が無ければ（保存の途中で止まった・消された・別のファイルがある）、古い登録ファイル
    だけを消す。新しい登録ファイルは、並行する保存がまだ公開していない場合があるので残す。"""
    name = mark["record"]
    try:
        registered = _signed(directory, mark.string, mark.string)
        if registered is None:
            return
        try:
            found = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            found = None
        ours = found is not None and stat.S_ISREG(found.st_mode) and _record_identity(found) == mark["identity"]
        dated = found if ours else registered
        if dated.st_mtime >= cutoff:
            return
        if ours:
            os.unlink(name, dir_fd=directory)
        os.unlink(mark.string, dir_fd=directory)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 並行する片付けが先に消した
        return
    except OSError as exc:
        run.note(round_no, "verify", f"古い判定役の記録 {name} を消せない（{exc}）")


def _drop_stopped(run: Noting, round_no: int, state_dir: Path, cutoff: float) -> None:
    """起動中か保存の途中でプロセスが止まって残った出力のディレクトリのうち、所有ファイルが `cutoff` より古いものを消す。"""
    try:
        found = [path for path in state_dir.iterdir() if _HELD.fullmatch(path.name) and not path.is_symlink() and path.is_dir()]
    except OSError as exc:
        run.note(round_no, "verify", f"状態の保存先を読めず、残った出力のディレクトリを片付けない（{exc}）")
        return
    for held in found:
        try:
            with _opened(held) as directory:
                owned = _signed(directory, _OWNER, held.name)
            if owned is not None and owned.st_mtime < cutoff:
                shutil.rmtree(held)
        except OSError as exc:
            if not isinstance(exc, FileNotFoundError):  # 無いのは、並行する片付けが先に消したため
                run.note(round_no, "verify", f"残った出力のディレクトリ {held.name} を消せない（{exc}）")


def drop_old(run: Noting, round_no: int, state_dir: Path) -> None:
    """残す期間を過ぎた、登録した記録（`RECORD_DIR`）と、残った出力のディレクトリを消す。"""
    cutoff = time.time() - limits.seconds(RECORD_KEEP)
    _drop_stopped(run, round_no, state_dir, cutoff)
    try:
        with _opened(state_dir / RECORD_DIR) as directory:
            for mark in filter(None, map(_MARK.fullmatch, os.listdir(directory))):
                _drop_if_expired(run, round_no, directory, mark, cutoff)
    except FileNotFoundError:  # noqa: NDF401 ファイル: まだ記録を保存していない
        return
    except OSError as exc:
        run.note(round_no, "verify", f"判定役の記録のディレクトリ {RECORD_DIR} を開けず、古い記録を片付けない（{exc}）")
