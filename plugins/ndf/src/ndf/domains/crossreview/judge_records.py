"""判定役の起動の記録（標準出力・標準エラーなど）を残し、期限を過ぎたものを片付ける（#4363）。

記録は状態の保存先の下の記録専用のディレクトリ（`RECORD_DIR`）に置き、保存できた名前を一覧のファイル（`RECORD_LIST`）に足す。
片付けで消すのは一覧に載る記録だけである（NDF が作っていないファイルは、同じ形の名前でも消さない）。専用のディレクトリは
リンクをたどらずに開き、保存と削除は開いたディレクトリを基準にする（確かめた後でリンクへ差し替えられても、先を触らない）。
失敗は過程に残すだけで、判定の結果と駆動は変えない。
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Protocol

from ndf.contracts import limits
from ndf.domains.crossreview import store

# 判定役の記録だけを置く、状態の保存先の下のディレクトリ
RECORD_DIR = "ndf-judge-records"
# 保存できた記録の名前の一覧（状態の保存先の直下）と、その中のキー
RECORD_LIST = "ndf-judge-records.json"
_NAMES_KEY = "record_names"
# 判定役の記録を残す秒
RECORD_KEEP = "cross-review.judge-record.keep"
# 記録の名前（接尾辞は `tempfile.mkdtemp` の 8 字）。一覧が書き換えられても、パスの区切りを含む名前などは扱わない
_RECORD = re.compile(r"round\d+-verify-[a-z0-9_]{8}(\.out|\.err\.log|-launch\.json|-progress\.log)")


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


def _saved_names(run: Noting, round_no: int, state_dir: Path) -> Optional[list[str]]:
    """一覧に載る記録の名前（一覧が無ければ空）。一覧を読めなければ過程に残して None（片付けも書き足しもしない）。"""
    path = state_dir / RECORD_LIST
    try:
        found = store.read_object(path) if path.exists() else {}
    except (OSError, store.StateBroken) as exc:
        run.note(round_no, "verify", f"判定役の記録の一覧 {RECORD_LIST} を読めない（{exc}）")
        return None
    return [name for name in store.list_of(found, _NAMES_KEY) if isinstance(name, str) and _RECORD.fullmatch(name)]


def _write_list(run: Noting, round_no: int, state_dir: Path, names: Sequence[str]) -> None:
    """一覧を `names` にする。"""
    try:
        store.write_object(state_dir / RECORD_LIST, {_NAMES_KEY: list(names)})
    except OSError as exc:
        run.note(round_no, "verify", f"判定役の記録の一覧 {RECORD_LIST} を書けない（{exc}）")


def _link_into(run: Noting, round_no: int, held: Path, stem: Path, into: int) -> list[str]:
    """`held` の通常のファイルを、開いたディレクトリ `into` へハードリンクし、移せた名前を返す。"""
    saved: list[str] = []
    for record in held.iterdir():
        if record.is_symlink() or not record.is_file():
            continue
        name = held.name + record.name.removeprefix(stem.name)
        try:
            record.chmod(0o600)
            os.link(record, name, dst_dir_fd=into)
        except OSError as exc:  # 診断の記録を移せなくても、判定の結果と駆動は変えない
            run.note(round_no, "verify", f"判定役の記録 {name} を移せない（{exc}）")
        else:
            saved.append(name)
    return saved


def keep(run: Noting, round_no: int, held: Path, stem: Path) -> None:
    """起動ごとのディレクトリ `held` の記録を、実行者だけが読めるファイルにして `stem` の隣の `RECORD_DIR` へ移し、`held` を消す。
    移し先の名前は、`stem` の名前の部分を `held` の名前（起動ごとに違う）に替えたものである。前からある同じ名前のファイルやリンクは
    置き換えない（`os.link` は移し先があれば失敗する）。移せた記録の名前を一覧に足し、移せない記録はディレクトリと一緒に消える。"""
    saved: list[str] = []
    directory = stem.parent / RECORD_DIR
    try:
        directory.mkdir(mode=0o700, exist_ok=True)
        with _opened(directory) as into:
            saved = _link_into(run, round_no, held, stem, into)
    except OSError as exc:
        run.note(round_no, "verify", f"判定役の記録のディレクトリ {RECORD_DIR} を使えず、記録を残さない（{exc}）")
    shutil.rmtree(held, ignore_errors=True)
    if saved and (listed := _saved_names(run, round_no, stem.parent)) is not None:
        _write_list(run, round_no, stem.parent, [*listed, *saved])


def _gone_if_expired(run: Noting, round_no: int, directory: int, name: str, cutoff: float) -> bool:
    """開いたディレクトリ `directory` の記録 `name` が `cutoff` より古い通常のファイルなら消す。一覧から外してよければ真
    （消した・もう無い）。"""
    try:
        found = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(found.st_mode) or found.st_mtime >= cutoff:
            return False
        os.unlink(name, dir_fd=directory)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 無い記録は一覧から外すだけでよい
        return True
    except OSError as exc:
        run.note(round_no, "verify", f"古い判定役の記録 {name} を消せない（{exc}）")
        return False
    return True


def drop_old(run: Noting, round_no: int, state_dir: Path) -> None:
    """一覧に載る記録のうち、残す期間を過ぎた通常のファイルを `RECORD_DIR` から消し、一覧から外す。"""
    listed = _saved_names(run, round_no, state_dir)
    if not listed:
        return
    cutoff = time.time() - limits.seconds(RECORD_KEEP)
    try:
        with _opened(state_dir / RECORD_DIR) as directory:
            left = [name for name in listed if not _gone_if_expired(run, round_no, directory, name, cutoff)]
    except OSError as exc:
        run.note(round_no, "verify", f"判定役の記録のディレクトリ {RECORD_DIR} を開けず、古い記録を片付けない（{exc}）")
        return
    if left != listed:
        _write_list(run, round_no, state_dir, left)
