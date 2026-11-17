"""マージの後の後片付けの判断（純粋な関数）: スプリントブランチを残すかと、メインディレクトリを取り込むか。

- `sprint_keep`: 選んだスプリントブランチを集めて出す取り込み方（ブランチ戦略の宣言の `release_strategy` が
  `keeps_sprint()` のどれか）では、スプリントブランチを本番ブランチに含まれるまで残す。取り込み方は**そのスプリントの
  開始時のスナップショット**で決め、スナップショットが無いときだけ今の宣言で決める（#3310）。含まれるかを確かめられない
  ときは残す側へ倒す（消すと戻せない）
- `long_lived_keep`: PR の head がベースブランチか本番ブランチ（リリースの PR）なら、worktree もブランチも消さない
- `pull_skip`: メインディレクトリがベースブランチにいなければ取り込まない（別のブランチへ取り込まない）
"""

from __future__ import annotations

from typing import Optional

from ndf import contracts
from ndf.contracts import facts
from ndf.core.branch_strategy import Strategy

# スプリントブランチの名前の接頭辞
SPRINT_PREFIX = "sprint/"
# 取り込み方の表（`contracts/domains/branch-flows.json`）
FLOWS = "branch-flows"


@contracts.cached_reader
def keeps_sprint() -> frozenset[str]:
    """本番ブランチに含まれるまでスプリントブランチを残す取り込み方（`release_strategy` の値）。

    取り込み方の表の `keeps_sprint` が真の行の `release_strategy` を集める（10 系の `lib/strategy_rows.py` の
    `keeps_sprint_branch` が真の行と同じ）。1 プロセスに 1 回だけ読む。
    """
    return frozenset(r["release_strategy"] for r in facts.rows(FLOWS, keeps_sprint=True))


def long_lived_keep(branch: Optional[str], base: Optional[str], production: Optional[str]) -> Optional[str]:
    """PR の head がベースブランチか本番チャネルなら残す理由（リリースの PR の後片付けで本流を消さない）。"""
    for name, label in ((base, "ベースブランチ"), (production, "本番ブランチ")):
        if branch and branch == name:
            return f"PR の head が{label} {branch} のため残す"
    return None


def sprint_keep(branch: Optional[str], strategy: Strategy, contained: Optional[bool], remote: str) -> Optional[str]:
    """スプリントブランチを残す理由。消してよければ `None`。

    `strategy` は開始時のスナップショットか今の宣言のセクション（`release_strategy` と `branches.production` を読む）。
    `contained` はブランチが `<remote>/<本番ブランチ>` に含まれるか（確かめられなければ `None`）。`remote` はリモートの
    名前（呼ぶ側が git の接続から渡す。判断のモジュールは接続を読まない）。
    """
    if not branch or not branch.startswith(SPRINT_PREFIX):
        return None
    if strategy.release_strategy not in keeps_sprint():
        return None
    production = f"{remote}/{strategy.production}"
    if contained is None:
        return f"{production} に含まれるかを確かめられないため残す"
    if contained:
        return None
    return f"取り込み方 {strategy.release_strategy} では {production} に含まれるまでスプリントブランチを残す"


def pull_skip(current: Optional[str], base: str) -> Optional[str]:
    """メインディレクトリを取り込まない理由。取り込むなら `None`。"""
    if current != base:
        return f"メインディレクトリが {base} でなく {current or 'detached HEAD'} のため取り込まない"
    return None
