"""修正担当が前のラウンドの会話を続けるかの判断（#4013。純粋な関数）。

前の修正担当の記録（`Previous`）・その起動の 1 ラウンドの文脈の増分の最大・窓の手前の余白から、続けるかと
続けない理由を決める。上の条件から順に見て、最初に当たった理由で新しい会話にする。
"""

from __future__ import annotations

from typing import Any, NamedTuple, Optional

FIRST = "その起動で初めて"
FAILED = "前の修正担当が正常に終わらなかった"
NOT_TAKEN = "前の修正の結果をマージできなかった"
COMPACTED = "前の会話で自動の要約が起きた"
UNREAD = "前の会話の文脈を読めない"
LARGE = "前の会話の文脈が窓に収まらない"


class Previous(NamedTuple):
    """その起動で最後に起動した修正担当の会話の ID・DONE で終わったか・結果をマージできたか・出力に自動の要約の行が
    あったか・最後の文脈のトークン数・主モデルの文脈の窓。`carry` は続けるときに引き継ぐ値（会話の最初に選んだモデルと effort と
    使用量ログの行の書き手。#2944）で、判断には使わない。"""

    session_id: str
    finished: bool
    taken: bool
    compacted: bool
    context: Optional[int]
    window: Optional[int]
    carry: Any = None


class Choice(NamedTuple):
    """続けるか。続けないときは理由（続けるときは None）。"""

    resume: bool
    reason: Optional[str]


def choose(previous: Optional[Previous], growth: int, margin: int) -> Choice:
    """前の記録・1 ラウンドの増分の最大（`growth`）・余白（`margin`）から、続けるかと理由を決める。"""
    if previous is None:
        return Choice(False, FIRST)
    if not previous.finished:
        return Choice(False, FAILED)
    if not previous.taken:
        return Choice(False, NOT_TAKEN)
    if previous.compacted:
        return Choice(False, COMPACTED)
    if previous.context is None or previous.window is None:
        return Choice(False, UNREAD)
    if previous.context + growth + margin > previous.window:
        return Choice(False, LARGE)
    return Choice(True, None)


def grown(context: Optional[int], carried: Optional[int], compacted: bool, window: Optional[int]) -> int:
    """1 ラウンドで増えた文脈。自動の要約が起きたら増分を測れないので窓（読めなければ 0）。新しい会話（`carried` が
    None）は `context`、続けた会話は前の文脈からの差（負なら 0）。`context` を読めなければ 0。"""
    if compacted:
        return 0 if window is None else window
    if context is None:
        return 0
    if carried is None:
        return context
    return max(0, context - carried)
