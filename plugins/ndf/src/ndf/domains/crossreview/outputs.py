"""`cross-review run` の出力ファイル（`--reviewed-out`・`--reviewed-pr-out`・`--reviewed-base-out`）の片付けと書き出し（#3702）。

始めに前の起動の出力を片付け、書けなかった起動の後に前の値を残さない。ただし自動で消すのは NDF が書いたと確かめられたファイル
だけである（PR 4638 の C4）。書いた記録は出力の隣の `.<名前>.ndf-output.json` に置く。状態の保存先には置かないので、状態の
保存先が違う起動（通常のレビューと差分のレビュー）も同じ出力の記録を読む（PR 4638 の r1-1）。記録には中身の hash と、
ファイルの同一性（デバイス・inode・大きさ・更新時刻）を残す。消した利用者が同じパスへ同じ中身のファイルを作り直しても、
NDF が書いたファイルとはみなさない（PR 4638 の r1-4）。消すときは記録も消す。

記録を残さない前のバージョンが書いた出力は、記録が無く、中身が状態ファイルに残る値（ラウンドの HEAD・読んだ PR）と同じときだけ
NDF が書いたとみなす（同じコマンドを版上げの後に打ち直して続ける。PR 4638 の r2-5）。出力は一時のファイルへ書き、記録を残してから
出力の名前へ置き換える。記録を残せなければ出力も残らないので、次の起動は止まらずに続く（PR 4638 の r2-4）。
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Collection
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


def clear_stale(path: Path, earlier: Collection[str] = ()) -> None:
    """前の起動の出力を片付け、出力と記録の保存先に書けるかを確かめる（親が無い・書けない・記録の保存先がファイルでなければ
    `OSError`）。`earlier` は、記録を残さない前のバージョンがこの出力へ書いたかもしれない中身（状態ファイルに残る値）。

    - NDF が書いたままのファイルは、記録とともに消す
    - 記録が無く、中身が `earlier` のどれかと同じファイルは、前のバージョンが書いた出力として消す
    - 空のファイル（呼び手が先に作った出力先など）は失う中身が無いので、消さずに残し、書けるかだけを確かめる。レビューの後に上書きする
    - ほかの既存のファイル（記録の無い・作り直された・書き換えられたもの）は、利用者のデータかもしれないので消さずに `Unowned`
    """
    record = owner_record(path)
    if record.exists() and not record.is_file():
        # レビューの後に記録を残せないと分かっても遅いので、相手役を起動する前に止める（PR 4638 の r2-4）
        raise OSError(f"出力の記録の保存先 {record} がファイルでない")
    if path.exists():
        if path.is_file() and path.stat().st_size == 0:
            path.open("a", encoding="utf-8").close()
            return
        if not (_owned(path) or _written_earlier(path, earlier)):
            raise Unowned(str(path))
        path.unlink()
        record.unlink(missing_ok=True)
    path.open("x", encoding="utf-8").close()
    path.unlink()


def _written_earlier(path: Path, earlier: Collection[str]) -> bool:
    """記録が無く、中身が前のバージョンの書いたかもしれない値と同じか。"""
    if not earlier or owner_record(path).exists():
        return False
    try:
        return path.read_text(encoding="utf-8") in earlier
    except (OSError, UnicodeDecodeError):  # noqa: NDF401 ファイル: 読めなければ、NDF が書いたと確かめられないとして消さない
        return False


def write_output(path: Path, text: str) -> None:
    """出力を一時のファイルへ書き、その記録を隣に残してから出力の名前へ置き換える（次の起動が消してよい出力かを確かめる）。
    置き換えは inode と更新時刻を変えない。途中で失敗すれば一時のファイルを消し、出力を残さない。"""
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(name)
    try:
        with open(fd, "w", encoding="utf-8") as out:
            out.write(text)
        # `mkstemp` は所有者だけの権限で作るので、これまでの `write_text` と同じ umask に従う権限にそろえる
        tmp.chmod(0o666 & ~_umask())
        store.write_object(owner_record(path), _file_identity(tmp))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask
