"""`ndf gws` の結果へ載せる値の切り出し（純粋な判断）。認証済みかの判定は持たない（records の gws の部品が持つ）。"""

from __future__ import annotations

import subprocess
from typing import Optional

# 失敗した外部のコマンドの出力から結果へ載せる末尾の行の数
DETAIL_LINES = 20
# gws の状態（結果の metrics.state。結果のスキーマ gws.result の列挙と同じ 4 つ）
AUTHENTICATED = "authenticated"
MISSING = "missing"
UNAUTHENTICATED = "unauthenticated"
UNINSTALLABLE = "uninstallable"


def tail(text: Optional[str]) -> str:
    """出力の末尾 `DETAIL_LINES` 行（前後の空白を除く）。`None` と空は空文字。"""
    if not text:
        return ""
    return "\n".join(text.strip().splitlines()[-DETAIL_LINES:])


def output_tail(done: subprocess.CompletedProcess[str]) -> str:
    """終わった外部のコマンドの標準出力と標準エラーをつないだ末尾（`tail`）。"""
    return tail(done.stdout + "\n" + done.stderr)
