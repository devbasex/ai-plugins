"""半端なファイルを残さない書き込み: 同じディレクトリの一時ファイル（`.<名前>.<pid>`）へ書いてから付け替える。"""

from __future__ import annotations

import os
from pathlib import Path


def write_text(path: Path, text: str) -> None:
    """`path` へ `text` を書く。書いている途中で落ちても、元のファイルか書き終えたファイルのどちらかが残る。"""
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
