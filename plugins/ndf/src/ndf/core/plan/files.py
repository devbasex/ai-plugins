"""状態の保存先の JSON の記録を読み書きする（`runner`・`steps`・`queue_run`・`cli` が使う）。

書くときは一時の名前へ書いてから置き換え、読む側に書きかけを見せない。一時の名前は記録ごとに固定し、先にあるものを消してから
排他的に新規作成する。置かれたシンボリックリンクの先は書き換えず、強制終了で残った一時ファイルは同じ記録の次の書き込みで片付く。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional


def read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):  # noqa: NDF401 ファイル: 無い・書きかけの記録は無いものとして扱う
        return None
    return found if isinstance(found, dict) else None


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)  # 前の残り。リンクはリンク自体を消す
    # "x" は排他的な新規作成（O_EXCL）。権限は umask に従う（`mkstemp` の 0600 にすると、読める人が変わる）
    with tmp.open("x", encoding="utf-8") as fh:
        fh.write(json.dumps(value, ensure_ascii=False))
    os.replace(tmp, path)
