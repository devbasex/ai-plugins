"""`cross-review run` の出力ファイル（`--reviewed-out`・`--reviewed-pr-out`・`--reviewed-base-out`）の片付けと書き出し（#3702）。

始めに前の起動の出力を片付け、書けなかった起動の後に前の値を残さない。ただし自動で消すのは NDF が書いたと確かめられたファイル
だけである（PR 4638 の C4）。書いた記録は出力の隣の `.<名前>.ndf-output.json` に置く。状態の保存先には置かないので、状態の
保存先が違う起動（通常のレビューと差分のレビュー）も同じ出力の記録を読む（PR 4638 の r1-1）。記録には中身の hash と、
ファイルの同一性（デバイス・inode・大きさ・更新時刻）を残す。消した利用者が同じパスへ同じ中身のファイルを作り直しても、
NDF が書いたファイルとはみなさない（PR 4638 の r1-4）。消すときは記録も消す。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from ndf.domains.crossreview import store

# 出力の記録のファイル名（`.<出力の名前><RECORD_SUFFIX>`）
RECORD_SUFFIX = ".ndf-output.json"


class Unowned(Exception):
    """出力先に、NDF が書いたと確かめられない中身のあるファイルがある（消さずに止まる）。"""


def owner_record(path: Path) -> Path:
    return path.with_name(f".{path.name}{RECORD_SUFFIX}")


def _file_identity(path: Path) -> dict[str, Any]:
    st = os.stat(path)
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": st.st_size,
        "dev": st.st_dev,
        "ino": st.st_ino,
        "mtime_ns": st.st_mtime_ns,
    }


def _owned(path: Path) -> bool:
    """`path` が、記録のとおり NDF が書いたままのファイルか。"""
    try:
        return store.read_object(owner_record(path)) == _file_identity(path)
    except (store.StateBroken, OSError):  # noqa: NDF401 ファイル: 記録か出力を読めなければ、NDF が書いたと確かめられないとして消さない
        return False


def clear_stale(path: Path) -> None:
    """前の起動の出力を片付け、出力先に書けるかを確かめる（親が無い・書けない出力先は `OSError`）。

    - NDF が書いたままのファイルは、記録とともに消す
    - 空のファイル（呼び手が先に作った出力先など）は失う中身が無いので、消さずに残し、書けるかだけを確かめる。レビューの後に上書きする
    - ほかの既存のファイル（記録の無い・作り直された・書き換えられたもの）は、利用者のデータかもしれないので消さずに `Unowned`
    """
    if path.exists():
        if path.is_file() and path.stat().st_size == 0:
            path.open("a", encoding="utf-8").close()
            return
        if not _owned(path):
            raise Unowned(str(path))
        path.unlink()
        owner_record(path).unlink(missing_ok=True)
    path.open("x", encoding="utf-8").close()
    path.unlink()


def write_output(path: Path, text: str) -> None:
    """出力を書き、書いたファイルの記録を隣に残す（次の起動が消してよい出力かを確かめる）。"""
    path.write_text(text, encoding="utf-8")
    store.write_object(owner_record(path), _file_identity(path))
