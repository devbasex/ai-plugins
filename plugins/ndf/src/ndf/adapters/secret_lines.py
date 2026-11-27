"""書かない値の形と、当たる行を丸ごと伏せる伏せ字（`ndf.domains.project.secret` から移した。#4566）。

設定の解析（`ndf.domains.project`）と、cross-refactoring の手元の最終ゲートのログと指示文（`ndf.domains.crossrefactor.local_gate`）が
同じ形を使う。標準ライブラリだけで書く。
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
