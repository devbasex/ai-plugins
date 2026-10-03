"""外へ出す文（コマンドの失敗の出力など）から認証情報を伏せ、短く縮める。

push の失敗の出力を Pull Request のコメントへ出すとき、URL に埋め込んだトークンなどを含めないために使う（#1692）。
cross-refactoring と cross-review が同じ規則を使うよう、規則はここ 1 か所に置く。純粋な処理で、外の世界に触らない。
"""

from __future__ import annotations

import re

MASK = "***"
MAX_LINES = 5
MAX_CHARS = 500

_PATTERNS = (
    # URL の資格情報（`https://user:pass@host`・`https://x-access-token:<トークン>@host`・`https://<トークン>@host`）
    re.compile(r"(?<=://)[^\s/@]+@"),
    # GitHub のトークンの形
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,})"),
    # `x-access-token:<値>` が URL の外に現れたとき
    re.compile(r"(?<=x-access-token:)[^\s@]+"),
)


def redact_output(text: str, max_lines: int = MAX_LINES, max_chars: int = MAX_CHARS) -> str:
    """認証情報を `***` に置き換え、空でない末尾の `max_lines` 行・`max_chars` 字までに縮める。"""
    out = str(text or "")
    for pattern in _PATTERNS:
        out = pattern.sub(lambda m: MASK + ("@" if m.group(0).endswith("@") else ""), out)
    lines = [line.rstrip() for line in out.splitlines() if line.strip()]
    out = "\n".join(lines[-max_lines:]) if max_lines > 0 else ""
    return out if len(out) <= max_chars else "…" + out[-(max_chars - 1) :]
