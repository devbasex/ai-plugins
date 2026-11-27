"""状態の保存先の JSON の記録を読み書きする（`runner`・`steps`・`queue_run`・`cli` が使う）。

書くときは一時ファイルへ書いてから置き換え、読む側に書きかけを見せない。一時ファイルは、記録と同じディレクトリの下の
NDF 専用の領域（`.ndf-writing/`）に、書き込みごとに一意な名前で排他的に新規作成し、書く間はロックを持つ。同じ記録への
書き込みが重なっても、作ったプロセスだけがそのファイルを記録へ置き換える。強制終了で残った一時ファイルは、同じディレクトリの
記録の次の書き込みで消す。掃除が列挙して消すのはこの領域の中の、ロックを誰も持たないファイルだけである。
"""

from __future__ import annotations

import fcntl
import json
import os
import uuid
from pathlib import Path
from typing import Any, Optional

WRITING_DIR = ".ndf-writing"  # NDF の一時ファイルだけを置く領域。ほかのものを置かない（掃除が消す）


def read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):  # noqa: NDF401 ファイル: 無い・書きかけの記録は無いものとして扱う
        return None
    return found if isinstance(found, dict) else None


def write_json(path: Path, value: dict[str, Any]) -> None:
    area = path.parent / WRITING_DIR
    area.mkdir(parents=True, exist_ok=True)
    _remove_left(area)
    text = json.dumps(value, ensure_ascii=False)
    while True:
        tmp = area / f"{path.name}.{uuid.uuid4().hex}.tmp"
        # "x" は排他的な新規作成（O_EXCL）。権限は umask に従う（`mkstemp` の 0600 にすると、読める人が変わる）
        with tmp.open("x", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            if os.fstat(fh.fileno()).st_nlink == 0:  # 作ってからロックを取るまでの間に、別の書き込みの掃除が消した
                continue
            fh.write(text)
            fh.flush()
            os.replace(tmp, path)
            return


def _remove_left(area: Path) -> None:
    """一時ファイルの領域に残ったファイルを消す。書き込み中（ロックがある）・リンク・開けないものは残す。"""
    for left in list(area.iterdir()):
        try:
            fd = os.open(left, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                left.unlink(missing_ok=True)
            finally:
                os.close(fd)
        except OSError:  # noqa: NDF401 ファイル: 掃除できない残りは次の書き込みに回し、記録の書き込みは続ける
            continue
