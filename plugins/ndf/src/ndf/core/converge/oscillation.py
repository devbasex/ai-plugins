"""指摘の同じさのキーと、振動の判定（純粋な関数）。

- 同じさは本文の先頭（`review.body-match` の文字数）を、空白をまとめ大文字小文字をそろえて比べる。投稿の重なりの
  判定と同じ値を使う
- 振動は、前々ラウンドにあった指摘が前ラウンドで消え、今のラウンドで戻ったこと（A → B → A）。直しては戻す往復を
  上限まで回さずに止める
"""

from __future__ import annotations

import re
from collections.abc import Sequence, Set

from ndf.contracts import limits

BODY_MATCH = "review.body-match"
_SPACES = re.compile(r"\s+")


def whole(text: str) -> str:
    """指摘の本文の全体を、空白をまとめ大文字小文字をそろえた文字列。"""
    return _SPACES.sub(" ", text).strip().casefold()


def finding_key(text: str) -> str:
    """指摘の本文から同じさのキーを作る。"""
    width = int(limits.value(BODY_MATCH, "count"))
    return whole(text)[:width]


def returned(history: Sequence[Set[str]]) -> frozenset[str]:
    """ラウンドごとの指摘のキーの並びから、前々ラウンドにあり前ラウンドで消えて今のラウンドに戻ったキー。"""
    if len(history) < 3:
        return frozenset()
    return frozenset((history[-1] & history[-3]) - history[-2])
