"""値の形の判定と、層をまたいで読む外部の出力の項目名。標準ライブラリだけを使い、hook のエントリポイントの import の閉包にも入る（contracts/hooks/imports.json の stdlib_only）。"""

from __future__ import annotations

# `claude -p` の結果の行が持つ、その起動の費用（米ドル）の項目
CLAUDE_COST_KEY = "total_cost_usd"


def is_int(value: object) -> bool:
    """`value` が整数か（真偽値は整数に数えない）。"""
    return isinstance(value, int) and not isinstance(value, bool)
