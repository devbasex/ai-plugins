"""入口で Pull Request の状態を確かめる（#1658）。

`init` が既に読んでいる `repos/{repo}/pulls/{pr}` の応答から状態を読み、続けてよいかを決める。
**git も GitHub も呼ばず、終了もしない。** 止めるかどうかは呼ぶ側（`setup._prepare_init`）が決める。
Draft かどうかは見ない。Draft でない Pull Request でも続ける（2026-10-07 の差し戻し）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class PrStatus:
    """Pull Request の応答の 2 項目。値は応答のまま持ち、想定の値かは `init_refusal` だけが決める。"""

    state: Any
    merged_at: Any

    @classmethod
    def of(cls, body: dict[str, Any]) -> "PrStatus":
        return cls(state=body.get("state"), merged_at=body.get("merged_at"))


def init_refusal(pr: int, repo: str, status: PrStatus) -> Optional[str]:
    """止める理由の 1 行を返す。続けてよければ `None`。

    判定は上から順に当て、最初に当たった行で止める（設計の「`refactor.py init` の止め方」）。
    新しい実行と再開を分けない。
    """
    if status.state not in ("open", "closed"):
        return f"Pull Request #{pr} の状態を判定できない（state: {status.state}）。gh api repos/{repo}/pulls/{pr} の応答を確かめる"
    if status.state == "closed":
        if status.merged_at:
            return f"Pull Request #{pr} はマージ済み（merged_at: {status.merged_at}）。新しい Pull Request を作って打ち直す"
        return f"Pull Request #{pr} は閉じている（state: closed）。開き直すか、新しい Pull Request を作って打ち直す"
    return None
