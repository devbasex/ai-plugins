"""入口で Pull Request の状態を確かめる（#1658）。

`init` が既に読んでいる `repos/{repo}/pulls/{pr}` の応答から状態を読み、続けてよいかを決める。
**git も GitHub も呼ばず、終了もしない。** 止めるかどうかは呼ぶ側（`setup._prepare_init`）が決める。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class PrStatus:
    """Pull Request の応答の 3 項目。値は応答のまま持ち、想定の値かは `refusal` だけが決める。"""

    state: Any
    draft: Any
    merged_at: Any

    @classmethod
    def of(cls, body: dict[str, Any]) -> "PrStatus":
        return cls(state=body.get("state"), draft=body.get("draft"), merged_at=body.get("merged_at"))


def refusal(pr: int, repo: str, status: PrStatus, resuming: bool) -> Optional[str]:
    """止める理由の 1 行を返す。続けてよければ `None`。

    判定は上から順に当て、最初に当たった行で止める（設計の「`refactor.py init` の止め方」）。
    Draft であることを求めるのは新しい実行だけである。再開は開いていれば続ける（要求の前提 1）。
    """
    check = f"gh api repos/{repo}/pulls/{pr} の応答を確かめる"
    if status.state not in ("open", "closed"):
        return f"Pull Request #{pr} の状態を判定できない（state: {status.state}）。{check}"
    if status.state == "closed":
        if status.merged_at:
            return f"Pull Request #{pr} はマージ済み（merged_at: {status.merged_at}）。新しい Pull Request を Draft で作って打ち直す"
        return f"Pull Request #{pr} は閉じている（state: closed）。開き直すか、新しい Pull Request を Draft で作って打ち直す"
    if resuming:
        return None
    if not isinstance(status.draft, bool):
        return f"Pull Request #{pr} の状態を判定できない（draft: {status.draft}）。{check}"
    if not status.draft:
        return f"Pull Request #{pr} は Draft でない（draft: false）。gh pr ready {pr} --undo で Draft に戻して打ち直す"
    return None
