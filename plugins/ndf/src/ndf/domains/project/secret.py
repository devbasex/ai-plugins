"""秘密の 2 ステップの表（#3569。旧い `project_lib/secret.py` の移し先）: 開かないファイルの名前と、書かない値の形。"""

from __future__ import annotations

import fnmatch
import json
from typing import Any

# 書かない値の形と行ごとの伏せ字は、cross-refactoring の手元の最終ゲートも使うので下の層に置く（#4566）
from ndf.adapters.secret_lines import ASSIGNED_SECRET, MASK, SECRET_VALUE, redact  # noqa: F401

# 秘密の 2 ステップの表: 開かないファイルの名前と、書かない値の形。`.env.example` も開かない（テンプレートに実際の値が入っている例がある）
SECRET_NAMES = (".env*", "*.pem", "*.key", "id_rsa*", "*credential*", "*secret*", ".npmrc", ".pypirc", "auth.json")


def is_secret_path(path: str) -> bool:
    """名前の表に当たるパスか。大文字と小文字は区別しない。"""
    name = path.rsplit("/", 1)[-1].lower()
    return any(fnmatch.fnmatchcase(name, pat) for pat in SECRET_NAMES)


def has_secret(value: Any) -> bool:
    """値（JSON にできるもの）のどこかに秘密の形の文字列があるか。"""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return bool(SECRET_VALUE.search(text))


def mask(value: Any) -> Any:
    """出力の直前に当てる。値（JSON にできるもの）の中の秘密の形の文字列をどれも伏せる。"""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: mask(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask(v) for v in value]
    return value
