"""`ndf cross-review run` の出力（`--reviewed-out`・`--reviewed-pr-out`・`--reviewed-base-out`。中身は 1 行）の片付けと書き込み。

出力を書くとき、書いた中身を出力の隣の記録（`.<名前>.ndf-output.json`）に残す。次の起動の始めには、出力の中身が記録と
一致するときだけ消す（前の起動の値を今回の結果と取り違えない）。一致しない中身のあるファイルは利用者のデータかもしれないので
消さずに `Unowned` を上げる（PR 4638 の C4）。空のファイルは失う中身が無いので、残してそこへ書く。

同じ出力先を同時に使う起動・書く途中の停止・記録の書き換え・記録を残さない前のバージョンの出力は扱わない。NDF は単位の
回ごとに新しい出力先を渡し、どの場合も利用者のファイルは消さずに止まるためである（PR 4638 で利用者が選んだ判断 A）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

KIND_KEY = "ndf_record"
RECORD_KIND = "cross-review-output"
TEXT_KEY = "written"


class Unowned(Exception):
    """出力先か隣の記録に、NDF が書いたと確かめられないファイルがある。"""


def owner_record(path: Path) -> Path:
    """出力の隣の記録のパス。"""
    return path.with_name(f".{path.name}.ndf-output.json")


def _recorded_text(record: Path) -> Optional[str]:
    """記録にある前の起動が書いた中身。記録が無ければ None。NDF の記録と読めなければ `Unowned`。"""
    if not os.path.lexists(record):
        return None
    if record.is_symlink() or not record.is_file():
        raise Unowned(str(record))
    try:
        held = json.loads(record.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Unowned(str(record)) from exc
    if not isinstance(held, dict) or held.get(KIND_KEY) != RECORD_KIND or not isinstance(held.get(TEXT_KEY), str):
        raise Unowned(str(record))
    return held[TEXT_KEY]


def _held_output(path: Path) -> str:
    """出力先の今の中身（テキストと読めなければ `Unowned`）。"""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise Unowned(str(path)) from exc


def clear_previous(path: Path) -> None:
    """前の起動が書いた出力と記録を消し、出力先へ書けることを確かめる（親が無い・書けなければ `OSError`）。

    出力先がリンク・通常のファイルでないもの・記録と一致しない中身のあるファイルなら、何も消さずに `Unowned` を上げる。
    """
    if not path.parent.is_dir():
        raise OSError(f"出力先 {path} の親のディレクトリが無い")
    record = owner_record(path)
    text = _recorded_text(record)
    if os.path.lexists(path):
        if path.is_symlink() or not path.is_file():
            raise Unowned(str(path))
        held = _held_output(path)
        if held and held != text:
            raise Unowned(str(path))
        if held:
            path.unlink()
    if text is not None:
        record.unlink()
    if os.path.lexists(path):
        if not os.access(path, os.W_OK):
            raise OSError(f"出力先 {path} に書けない")
        return
    path.open("x", encoding="utf-8").close()
    path.unlink()


def write_recorded(path: Path, text: str) -> None:
    """出力を書き、書いた中身を隣の記録に残す。"""
    path.write_text(text, encoding="utf-8")
    owner_record(path).write_text(json.dumps({KIND_KEY: RECORD_KIND, TEXT_KEY: text}, ensure_ascii=False), encoding="utf-8")
