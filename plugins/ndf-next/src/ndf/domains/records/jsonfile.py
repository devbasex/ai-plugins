"""JSON のファイルを読む（宣言・用語集の正本が使う）。読めなければ `JsonFileError`。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonFileError(ValueError):
    """JSON のファイルを読めない・object でない。"""


def read(path: Path) -> Any:
    """JSON のファイルの値。読めない・JSON でなければ `JsonFileError`（UTF-8 でなければ `UnicodeDecodeError`）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JsonFileError(f"{path} を読めない: {exc}") from exc


def read_object(path: Path) -> dict[str, Any]:
    """JSON のファイルの object。読めない・object でなければ `JsonFileError`。"""
    raw = read(path)
    if not isinstance(raw, dict):
        raise JsonFileError(f"{path} が object でない")
    return raw
