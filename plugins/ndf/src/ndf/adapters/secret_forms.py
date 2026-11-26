"""秘密の値の形の表と、当たる文字列を伏せる処理（#1959 で `domains/project/secret.py` から移した）。

起動の設定の検査（adapters）と `ndf project` の出力（domains）が同じ表を使う。下の層に置くのは、adapters が domains を読めないためである。
"""

from __future__ import annotations

import re

SECRET_VALUE = re.compile(
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}"  # AWS のアクセスキー
    r"|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}"  # GitHub
    r"|\bglpat-[A-Za-z0-9_-]{16,}|\bgl(?:dt|rt|ptt|cbt|oas)-[A-Za-z0-9_-]{16,}"  # GitLab
    r"|xox[abprse]-[A-Za-z0-9-]{8,}"  # Slack
    r"|\bsk-[A-Za-z0-9_-]{16,}|\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"  # OpenAI・Anthropic・Stripe
    r"|\bAIza[0-9A-Za-z_-]{30,}|\bya29\.[0-9A-Za-z_-]{20,}"  # Google
    r"|\bnpm_[A-Za-z0-9]{30,}|\bpypi-[A-Za-z0-9_-]{30,}"  # npm・PyPI
    r"|\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"  # JWT
    r"|-----BEGIN"  # キー
    r"|://[^/\s:@]+:[^/\s@]+@"  # URL に埋めた資格情報
)
# 代入の形。テストの固定値にも当たるため、設定の値の判定（`has_secret`）には使わず、表示と出力で伏せるときだけ当てる
ASSIGNED_SECRET = re.compile(r"(?i:\b(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)\b\s*[=:]\s*[\"']?[^\s\"']{8,})")
MASK = "（伏せた）"


def redact(text: str) -> str:
    """秘密の形に当たる行を伏せる（根拠の行・差分の行に当てる）。"""
    return MASK if SECRET_VALUE.search(text) or ASSIGNED_SECRET.search(text) else text


def holds_secret(text: str) -> bool:
    """秘密の形（`redact` が伏せる形）を含むか。代入の値が `$` で始まる環境変数の参照（`token=$API_TOKEN`）は値が現れないので当てない。"""
    if SECRET_VALUE.search(text):
        return True
    return any(
        not re.split(r"[=:]", m.group(0), maxsplit=1)[1].lstrip().lstrip("\"'").startswith("$") for m in ASSIGNED_SECRET.finditer(text)
    )
