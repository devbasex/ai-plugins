"""アカウントを替えるかの判断（#3336・#3337）。時計とファイルを読まない純粋な関数。

- 利用上限の前（#3336）: カットポイント（子が次の区間の印を書いた時点）で、今のアカウントの使用率が閾値以上で、
  閾値より下のアカウントがあれば、そのアカウントで次の区間を始める。下のアカウントが無ければ替えない
- 利用上限の後（#3337）: 上限に当たったら、背景の待ち（`wait`・`merge-when-green` など）が残っていても替える。
  背景の待ちは会話の記録に残り、`--resume` した先のセッションが受け取るので、替えない理由にしない
- 替える先が無ければ `wait_reset` を返し、呼ぶ側はリセットの後に同じアカウントで起こし直す（止まったまま残さない）
"""

from __future__ import annotations

from typing import NamedTuple, Optional


class Usage(NamedTuple):
    """アカウント 1 つの使用率（5 時間と週の枠の大きいほうの百分率）と、上限に当たっているか。"""

    name: str
    percent: float
    limited: bool


class Pick(NamedTuple):
    """`kind` は `keep`（替えない）・`switch`（`account` へ替える）・`wait_reset`（替える先が無い）。"""

    kind: str
    account: Optional[str]
    reason: str


def _best(current: Optional[str], rows: list[Usage], below: float) -> Optional[Usage]:
    """今のアカウントを除き、上限に当たっておらず使用率が `below` より下で、最も空いているもの。"""
    usable = [u for u in rows if u.name != current and not u.limited and u.percent < below]
    return min(usable, key=lambda u: (u.percent, u.name)) if usable else None


def before_limit(current: Optional[str], rows: list[Usage], threshold: float) -> Pick:
    """カットポイントで、上限に当たる前に替えるか（#3336）。"""
    now = next((u for u in rows if u.name == current), None)
    if now is None:
        return Pick("keep", current, "今のアカウントの使用率が分からない（センサーの記録が無い）")
    if now.percent < threshold and not now.limited:
        return Pick("keep", current, f"{current} の使用率 {now.percent:g}% は閾値 {threshold:g}% より下")
    best = _best(current, rows, threshold)
    if best is None:
        return Pick("keep", current, f"{current} は {now.percent:g}% だが、閾値より空いたアカウントが無い")
    return Pick("switch", best.name, f"{current} の使用率 {now.percent:g}% が閾値 {threshold:g}% 以上。{best.name}（{best.percent:g}%）へ替える")


def after_limit(current: Optional[str], rows: list[Usage]) -> Pick:
    """上限に当たった後に替えるか（#3337）。背景の待ちが残っていても替える。

    今のアカウントの上限が解けていれば（リセットの後）、同じアカウントで再開する。
    """
    now = next((u for u in rows if u.name == current), None)
    if now is not None and not now.limited:
        return Pick("switch", current, f"{current} の上限が解けた。同じアカウントで再開する")
    best = _best(current, rows, 100.0)
    if best is None:
        return Pick("wait_reset", current, f"{current} が上限に当たり、替える先が無い。リセットの後に同じアカウントで再開する")
    return Pick("switch", best.name, f"{current} が上限に当たった。{best.name}（{best.percent:g}%）で再開する。背景の待ちは再開した先のセッションが受け取る")
