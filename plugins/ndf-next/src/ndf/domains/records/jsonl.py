"""記録の JSONL を読む・排他つきで 1 行足す（使用量の帳簿と MVV の記録が使う）。

記録は追記だけで持つ。読むときは壊れた行（書きかけ・辞書でない）を数えて除き、その理由で止まらない。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from filelock import FileLock

from ndf.contracts import limits


def read_rows(path: Path) -> tuple[list[dict[str, Any]], int]:
    """行と、読めなかった行の数。ファイルが無ければ空。"""
    if not path.is_file():
        return [], 0
    rows: list[dict[str, Any]] = []
    broken = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            broken += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            broken += 1
    return rows, broken


def lock(path: Path) -> FileLock:
    """記録のファイルの錠（`<ファイル>.lock`）。待ちの上限は `locks.append`。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(path) + ".lock", timeout=limits.seconds("locks.append"))


def append(path: Path, row: Mapping[str, Any]) -> None:
    """1 行足す（錠は呼び手が持つ）。"""
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_locked(path: Path, row: Mapping[str, Any]) -> None:
    """錠を取って 1 行足す。"""
    with lock(path):
        append(path, row)
