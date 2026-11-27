"""判定役の起動の記録（標準出力・標準エラーなど）を残し、期限を過ぎたものを片付ける（#4363）。

記録は状態の保存先の下の記録専用のディレクトリ（`RECORD_DIR`）に置く。記録を公開する前に、記録ごとの登録ファイル（名前が
記録の名前と inode 番号を持つ空のファイル）を同じディレクトリに作る。片付けで消すのは、登録ファイルと inode 番号が合う記録
だけである（NDF が作っていないファイルは、同じ形の名前でも消さない）。登録ファイルは記録ごとに独立しているので、並行する
実行が互いの登録を失わず、途中で止まっても登録の無い記録は残らない。登録ファイルは中身を開かない。専用のディレクトリは
リンクをたどらずに開き、保存と削除は開いたディレクトリを基準にする（確かめた後でリンクへ差し替えられても、先を触らない）。
失敗は過程に残すだけで、判定の結果と駆動は変えない。
"""

from __future__ import annotations

import os
import re
import shutil
import stat
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
# 登録ファイルの名前は `<記録の名前>.owned-<記録の inode 番号>`。記録の名前の接尾辞は `tempfile.mkdtemp` の 8 字
_MARK_INFIX = ".owned-"
_MARK = re.compile(
    r"(?P<record>round\d+-verify-[a-z0-9_]{8}(?:\.out|\.err\.log|-launch\.json|-progress\.log))"
    + re.escape(_MARK_INFIX)
    + r"(?P<inode>\d+)"
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


def _link_into(run: Noting, round_no: int, held: Path, stem: Path, into: int) -> None:
    """`held` の通常のファイルを、登録ファイルを作ってから、開いたディレクトリ `into` へハードリンクする。"""
    for record in held.iterdir():
        if record.is_symlink() or not record.is_file():
            continue
        name = held.name + record.name.removeprefix(stem.name)
        try:
            record.chmod(0o600)
            # 公開より先に登録する（間で止まっても、片付けの対象でない記録は残らない）
            mark = f"{name}{_MARK_INFIX}{record.stat().st_ino}"
            os.close(os.open(mark, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=into))
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
    """登録ファイル `mark` の記録が `cutoff` より古ければ、記録と登録ファイルを消す。登録ファイルと合う記録が無ければ
    （保存の途中で止まった・消された・別のファイルがある）、古い登録ファイルだけを消す。新しい登録ファイルは、並行する保存が
    まだ公開していない場合があるので残す。"""
    name = mark["record"]
    try:
        try:
            found = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            found = None
        ours = found is not None and stat.S_ISREG(found.st_mode) and found.st_ino == int(mark["inode"])
        dated = found if ours else os.stat(mark.string, dir_fd=directory, follow_symlinks=False)
        if dated.st_mtime >= cutoff:
            return
        if ours:
            os.unlink(name, dir_fd=directory)
        os.unlink(mark.string, dir_fd=directory)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 並行する片付けが先に消した
        return
    except OSError as exc:
        run.note(round_no, "verify", f"古い判定役の記録 {name} を消せない（{exc}）")


def drop_old(run: Noting, round_no: int, state_dir: Path) -> None:
    """登録した記録のうち、残す期間を過ぎた通常のファイルを、登録ファイルと一緒に `RECORD_DIR` から消す。"""
    cutoff = time.time() - limits.seconds(RECORD_KEEP)
    try:
        with _opened(state_dir / RECORD_DIR) as directory:
            for mark in filter(None, map(_MARK.fullmatch, os.listdir(directory))):
                _drop_if_expired(run, round_no, directory, mark, cutoff)
    except FileNotFoundError:  # noqa: NDF401 ファイル: まだ記録を保存していない
        return
    except OSError as exc:
        run.note(round_no, "verify", f"判定役の記録のディレクトリ {RECORD_DIR} を開けず、古い記録を片付けない（{exc}）")
