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
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}"  # AWS のアクセスキー
    r"|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}"  # GitHub
    r"|\bglpat-[A-Za-z0-9_-]{16,}|\bgl(?:dt|rt|ptt|cbt|oas)-[A-Za-z0-9_-]{16,}"  # GitLab
    r"|xox[abprse]-[A-Za-z0-9-]{8,}"  # Slack
    r"|\bsk-[A-Za-z0-9_-]{16,}|\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"  # OpenAI・Anthropic・Stripe
    r"|\bAIza[0-9A-Za-z_-]{30,}|\bya29\.[0-9A-Za-z_-]{20,}"  # Google
    r"|\bnpm_[A-Za-z0-9]{30,}|\bpypi-[A-Za-z0-9_-]{30,}"  # npm・PyPI
    r"|\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"  # JWT
    r"|-----BEGIN"  # 鍵
    r"|://[^/\s:@]+:[^/\s@]+@"  # URL に埋めた資格情報
)
# 代入の形。テストの固定値（`PASSWORD=testpassword`）にも当たるため、宣言の値の判定（`has_secret`）には使わず、
# 表示と出力で伏せる（`redact` / `mask`）ときだけ当てる
ASSIGNED_SECRET = re.compile(r"(?i:\b(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)\b\s*[=:]\s*[\"']?[^\s\"']{8,})")
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
    return MASK if SECRET_VALUE.search(text) or ASSIGNED_SECRET.search(text) else text


def mask(value: Any) -> Any:
    """出力の直前に当てる。値（JSON にできるもの）の中の秘密の形の文字列をどれも伏せる。"""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: mask(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask(v) for v in value]
    return value
