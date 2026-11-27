"""`ndf cross-review run` の出力（`--reviewed-out`・`--reviewed-pr-out`・`--reviewed-base-out`。中身は 1 行）の片付けと書き込み。

出力を書くとき、書く中身と出力のファイルの同一性（デバイス番号と inode）を、出力の隣の記録（`.<名前>.ndf-output.json`）に
残す。次の起動の始めには、出力が記録と同じファイルで、中身のバイト列全体が記録の中身と一致するときだけ空に戻す（前の起動の
値を今回の結果と取り違えない）。中身が一部でも違うファイルと、同じ中身でも別のファイルは、利用者が作ったか編集したかもしれない
ので触らずに `Unowned` を上げる（PR 4638 の C4・r1-2・r2-5）。空に戻すのは、消して作り直すと権限が umask で決まり、呼び手が
0600 で用意した出力が他の利用者に読めるようになるためである（r1-3）。記録は所有者だけが読める権限（0600）で作る。

書く順序は、出力を空にして開く → 記録を 1 回の書き込みで書く → 出力の中身を 1 回の書き込みで書く、である。出力が空か、記録と
出力全体が一致していれば、同じ引数で打ち直して続く（r2-6・r2-7）。記録を作った直後に終了して空の記録が残っても、失う中身が無い
ので記録が無いのと同じに扱う。一時のファイルは作らない（名前の長さの上限と、強制終了で残るファイルを増やさない。r3-2・r3-5）。
書き込みの失敗・書き切れなかったことに気づいた起動は、その場で出力を空に戻し記録を消してから失敗を返す（r3-3・r3-4）。
1 回の書き込みの途中で電源断などにより止まり、出力か記録の一部だけが残った場合は、変えずに `precondition` で止まる。

同じ出力先を同時に使う起動・記録の書き換え・記録を残さない前のバージョンの出力は扱わない。NDF は単位の回ごとに新しい
出力先を渡し、どの場合も利用者のファイルは消さずに止まるためである（PR 4638 で利用者が選んだ判断 A）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from ndf.core.plan import files

KIND_KEY = files.RECORD_KIND_KEY
RECORD_KIND = "cross-review-output"
TEXT_KEY = "written"
FILE_KEY = "identity"
# 記録には出力と同じ値（レビューした SHA・PR・ブランチ名）を書くので、出力の権限によらず所有者だけに読ませる（r1-3）
RECORD_MODE = 0o600
# 無い出力を作るときの権限。umask が 002 でも、承認の記録の読み手が拒むグループの書き込みを付けない
OUTPUT_MODE = 0o644


class Unowned(Exception):
    """出力先か隣の記録に、NDF が書いたと確かめられないファイルがある。"""


def owner_record(path: Path) -> Path:
    """出力の隣の記録のパス。"""
    return path.with_name(f".{path.name}.ndf-output.json")


def _file_of(st: os.stat_result) -> list[int]:
    return [st.st_dev, st.st_ino]


def _recorded(record: Path) -> Optional[dict[str, Any]]:
    """前の起動の記録（書いた中身と出力の同一性）。記録が無ければ None。NDF の記録と読めなければ `Unowned`。"""
    if not os.path.lexists(record):
        return None
    if record.is_symlink() or not record.is_file():
        raise Unowned(str(record))
    if record.stat().st_size == 0:
        # 記録を作った直後に終了した起動が残す。失う中身が無いので、記録が無いのと同じに扱う（片付けでは消す。r2-6）
        return None
    try:
        held = json.loads(record.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Unowned(str(record)) from exc
    if (
        not isinstance(held, dict)
        or held.get(KIND_KEY) != RECORD_KIND
        or not isinstance(held.get(TEXT_KEY), str)
        or not isinstance(held.get(FILE_KEY), list)
    ):
        raise Unowned(str(record))
    return held


def _probe_create(path: Path) -> None:
    """無いファイルを実際に作れるかを、作って消して確かめる（作れなければ `OSError`）。"""
    path.open("x", encoding="utf-8").close()
    path.unlink()


def check(path: Path) -> None:
    """何も変えずに、出力先を片付けて書けるかを確かめる（親が無い・出力か記録を書けなければ `OSError`）。

    出力先がリンク・通常のファイルでないもの・前の起動の記録と同じファイルで中身全体が一致すると確かめられない中身のあるファイルなら
    `Unowned`。
    呼び手は、すべての出力先を確かめてから `clear_previous` を呼ぶ（1 つでも止まるなら何も変えない。r1-9）。
    """
    if not path.parent.is_dir():
        raise OSError(f"出力先 {path} の親のディレクトリが無い")
    record = owner_record(path)
    held = _recorded(record)
    if os.path.lexists(path):
        if path.is_symlink() or not path.is_file():
            raise Unowned(str(path))
        # バイト列で比べる（UTF-8 として読めない中身も、記録と一致しない中身として扱う。r2-8）
        data = path.read_bytes()
        if data and not (held is not None and held[FILE_KEY] == _file_of(os.stat(path)) and data == held[TEXT_KEY].encode("utf-8")):
            raise Unowned(str(path))
        if not os.access(path, os.W_OK):
            raise OSError(f"出力先 {path} に書けない")
    else:
        _probe_create(path)
    # 記録は書いた後でなく書く前に作るので、記録を作れない出力先はレビューの前に止める（r1-6）
    if not os.path.lexists(record):
        _probe_create(record)
    elif not os.access(path.parent, os.W_OK | os.X_OK):
        raise OSError(f"出力先 {path} の記録 {record} を作り直せない")


def clear_previous(path: Path) -> None:
    """`check` を通った出力先の、前の起動が書いた出力を空に戻し（inode と権限は保つ）、記録を消す。"""
    record = owner_record(path)
    if os.path.lexists(path) and path.stat().st_size:
        with path.open("r+b") as out:
            out.truncate(0)
    if os.path.lexists(record):
        record.unlink()


def _write_once(fd: int, data: bytes, path: Path) -> None:
    """1 回の書き込みで書き切る（中身は数百バイトまで）。書き切れなければ `OSError`。"""
    if os.write(fd, data) != len(data):
        raise OSError(f"{path} に書き切れない")


def write_recorded(path: Path, text: str) -> None:
    """出力を空にして開き、記録を残してから、出力の中身を書く（既存のファイルへは inode と権限を保って書く）。

    書けなければ、出力を空に戻し記録を消してから `OSError` を返す。次の起動は同じ引数で続く（r3-3・r3-4）。
    """
    record = owner_record(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, OUTPUT_MODE)
    try:
        body = json.dumps({KIND_KEY: RECORD_KIND, TEXT_KEY: text, FILE_KEY: _file_of(os.fstat(fd))}, ensure_ascii=False)
        rfd = os.open(record, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, RECORD_MODE)
        try:
            os.fchmod(rfd, RECORD_MODE)
            _write_once(rfd, body.encode("utf-8"), record)
        finally:
            os.close(rfd)
        _write_once(fd, text.encode("utf-8"), path)
    except OSError:
        os.ftruncate(fd, 0)
        record.unlink(missing_ok=True)
        raise
    finally:
        os.close(fd)
