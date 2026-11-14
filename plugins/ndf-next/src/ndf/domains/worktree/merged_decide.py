"""マージの後の後片付けの判断（純粋な関数）: スプリントブランチを残すかと、メインディレクトリを取り込むか。

- `sprint_keep`: 選んで集める取り込み方（事実の表 `branch-flows` の `keeps_sprint` が真の行）では、スプリントブランチを
  本番チャネルに含まれるまで残す。取り込み方は**そのスプリントの開始時の記録**で決め、記録が無いスプリントだけ今の宣言で
  決める（#3310）。記録か宣言の取り込み方が表に無い・含まれるかを確かめられないときは残す側へ倒す（消すと戻せない）
- `pull_skip`: メインディレクトリがベースブランチにいなければ取り込まない（別のブランチへ取り込まない）
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from ndf.adapters.git import ORIGIN
from ndf.contracts import facts

# スプリントブランチの名前の接頭辞
SPRINT_PREFIX = "sprint/"
# 取り込み方の表
FLOWS = "branch-flows"


def _flow_row(promotion: Optional[str]) -> Optional[dict[str, Any]]:
    """取り込み方の行。`promotion` が無ければ既定の行、表に無ければ `None`。"""
    if promotion is None:
        rows = facts.rows(FLOWS, default=True)
    else:
        rows = facts.rows(FLOWS, promotion=promotion)
    return rows[0] if rows else None


def sprint_keep(branch: Optional[str], flow: Optional[Mapping[str, Any]], contained: Optional[bool]) -> Optional[str]:
    """スプリントブランチを残す理由。消してよければ `None`。

    `flow` は開始時の記録か今の宣言の `branch_flow`（`promotion` と `production_branch` を読む）。`contained` は
    ブランチが `origin/<本番チャネル>` に含まれるか（確かめられなければ `None`）。
    """
    if not branch or not branch.startswith(SPRINT_PREFIX):
        return None
    if flow is None:
        flow = {}
    promotion = flow.get("promotion")
    row = _flow_row(promotion if isinstance(promotion, str) else None)
    if row is None:
        return f"取り込み方 {promotion!r} が表 {FLOWS} に無いため残す"
    if not row["keeps_sprint"] or not flow.get("production_branch"):
        return None
    production = f"{ORIGIN}/{flow['production_branch']}"
    if contained is None:
        return f"{production} に含まれるかを確かめられないため残す"
    if contained:
        return None
    return f"取り込み方 {row['promotion']} では {production} に含まれるまでスプリントブランチを残す"


def pull_skip(current: Optional[str], base: Optional[str]) -> Optional[str]:
    """メインディレクトリを取り込まない理由。取り込むなら `None`。"""
    if base is None:
        return "ベースブランチが決まらない（宣言の base_branch も origin の既定ブランチも無い）ため取り込まない"
    if current != base:
        return f"メインディレクトリが {base} でなく {current or 'detached HEAD'} のため取り込まない"
    return None
