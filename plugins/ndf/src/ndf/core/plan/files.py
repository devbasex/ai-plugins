"""状態の保存先の JSON の記録を読み書きする（`runner`・`steps`・`queue_run`・`cli` が使う）。

書くときは一時の名前へ書いてから置き換え、読む側に書きかけを見せない。一時ファイルは一意な名前で排他的に新規作成し、
先に置かれたファイルやシンボリックリンクの先を書き換えない。
"""

from __future__ import annotations

import json
import os
import uuid
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
    text = json.dumps(value, ensure_ascii=False)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    # "x" は排他的な新規作成（O_EXCL）。権限は umask に従う（`mkstemp` の 0600 にすると、読める人が変わる）
    fh = tmp.open("x", encoding="utf-8")
    try:
        with fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
