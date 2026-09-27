"""秘密の 2 段の表（決定 10）: 開かないファイルの名前（I6）と、書かない値の形（I7）。標準ライブラリだけで書く。"""

from __future__ import annotations

import fnmatch
import json
import re
from typing import Any

# 開かないファイル。パスだけを記録する。`.env.example` も開かない（雛形に実際の値が入っている例がある）
SECRET_NAMES = (".env*", "*.pem", "*.key", "id_rsa*", "*credential*", "*secret*", ".npmrc", ".pypirc", "auth.json")

# 書かない値の形
SECRET_VALUE = re.compile(
    r"AKIA[0-9A-Z]{16}"
    r"|gh[pousr]_[A-Za-z0-9]{16,}"
    r"|xox[abprs]-[A-Za-z0-9-]{8,}"
    r"|\bsk-[A-Za-z0-9_-]{16,}"
    r"|-----BEGIN"
    r"|://[^/\s:@]+:[^/\s@]+@"
)
MASK = "（伏せた）"


def is_secret_path(path: str) -> bool:
    """名前の表に当たるパスか。大文字と小文字は区別しない。"""
    name = path.rsplit("/", 1)[-1].lower()
    return any(fnmatch.fnmatchcase(name, pat) for pat in SECRET_NAMES)


def has_secret(value: Any) -> bool:
    """値（JSON にできるもの）のどこかに秘密の形の文字列があるか。"""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return bool(SECRET_VALUE.search(text))


def redact(text: str) -> str:
    """秘密の形に当たる行を伏せる（根拠の行・差分の行に当てる）。"""
    return MASK if SECRET_VALUE.search(text) else text
