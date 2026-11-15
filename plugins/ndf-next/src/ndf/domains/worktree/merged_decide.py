"""マージの後の後片付けの判断（純粋な関数）: スプリントブランチを残すかと、メインディレクトリをマージするか。

- `sprint_keep`: 選んで集めるリリース方式（事実の表 `branch-flows` の `keeps_sprint` が真の行）では、スプリントブランチを
  本番チャネルに含まれるまで残す。リリース方式は**そのスプリントの開始時の記録**で決め、記録が無いスプリントだけ今の設定で
  決める（#3310）。記録か設定のリリース方式が表に無い・含まれるかを確かめられないときは残す側へ倒す（消すと戻せない）
- `long_lived_keep`: PR の head がベースブランチか本番チャネル（リリースの PR）なら、worktree もブランチも消さない
- `pull_skip`: メインディレクトリがベースブランチにいなければマージしない（別のブランチへマージしない）
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from ndf.contracts import facts

# スプリントブランチの名前の接頭辞
SPRINT_PREFIX = "sprint/"
# リリース方式の表
FLOWS = "branch-flows"


def _flow_row(release_strategy: Optional[str]) -> Optional[dict[str, Any]]:
    """リリース方式の行。`release_strategy` が無ければ既定の行、表に無ければ `None`。"""
    if release_strategy is None:
        rows = facts.rows(FLOWS, default=True)
    else:
        rows = facts.rows(FLOWS, release_strategy=release_strategy)
    return rows[0] if rows else None


def long_lived_keep(branch: Optional[str], base: Optional[str], production: Optional[str]) -> Optional[str]:
    """PR の head がベースブランチか本番チャネルなら残す理由（リリースの PR の後片付けで本流を消さない）。"""
    for name, label in ((base, "ベースブランチ"), (production, "本番チャネル")):
        if branch and branch == name:
            return f"PR の head が{label} {branch} のため残す"
    return None


def sprint_keep(branch: Optional[str], flow: Optional[Mapping[str, Any]], contained: Optional[bool], remote: str) -> Optional[str]:
    """スプリントブランチを残す理由。消してよければ `None`。

    `flow` は開始時の記録か今の設定の `branch_flow`（`release_strategy` と `production_branch` を読む）。`contained` は
    ブランチが `<remote>/<本番チャネル>` に含まれるか（確かめられなければ `None`）。`remote` はリモートの名前（呼ぶ側が
    git の接続から渡す。判断のモジュールは接続を読まない）。
    """
    if not branch or not branch.startswith(SPRINT_PREFIX):
        return None
    if flow is None:
        flow = {}
    release_strategy = flow.get("release_strategy")
    row = _flow_row(release_strategy if isinstance(release_strategy, str) else None)
    if row is None:
        return f"リリース方式 {release_strategy!r} が表 {FLOWS} に無いため残す"
    if not row["keeps_sprint"] or not flow.get("production_branch"):
        return None
    production = f"{remote}/{flow['production_branch']}"
    if contained is None:
        return f"{production} に含まれるかを確かめられないため残す"
    if contained:
        return None
    return f"リリース方式 {row['release_strategy']} では {production} に含まれるまでスプリントブランチを残す"


def pull_skip(current: Optional[str], base: Optional[str]) -> Optional[str]:
    """メインディレクトリをマージしない理由。マージするなら `None`。"""
    if base is None:
        return "ベースブランチが決まらない（設定の base_branch も origin の既定ブランチも無い）ためマージしない"
    if current != base:
        return f"メインディレクトリが {base} でなく {current or 'detached HEAD'} のためマージしない"
    return None
