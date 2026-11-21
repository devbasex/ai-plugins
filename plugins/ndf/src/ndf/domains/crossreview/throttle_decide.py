"""PR へ書く操作が GitHub の二次レート制限に当たったときの待ち（#3941。純粋な関数）。

二次レート制限は 403 か 429 で返り、本文に `secondary rate limit` を持つ（githubkit が `SecondaryRateLimitExceeded` に分けたときは
`github.GitHubError` の文が同じ語を持つ）。
待ちの秒は GitHub が返した値（`retry-after`）を使い、無ければ `cross-review.secondary-rate-limit` の `interval`。
打つ回数の上限は同じ方針の `attempts`（最初の 1 回を含む）。一次の上限（残りの回数の枯渇）は待たない（解除まで最大 1 時間）。
"""

from __future__ import annotations

from typing import Optional

from ndf.contracts import limits

POLICY = "cross-review.secondary-rate-limit"
SPACING = "cross-review.thread-spacing"
_STATUSES = (403, 429)
_PHRASE = "secondary rate limit"


def secondary_limited(status: int, detail: str) -> bool:
    """二次レート制限に当たった失敗か。"""
    return status in _STATUSES and _PHRASE in detail.lower()


def wait_before_retry(limited: bool, attempt: int, retry_after: Optional[float]) -> Optional[float]:
    """`attempt` 回目（1 から）の失敗の後に待つ秒。打ち直さないなら None。"""
    if not limited or attempt >= int(limits.value(POLICY, "attempts")):
        return None
    return retry_after if retry_after is not None and retry_after > 0 else limits.value(POLICY, "interval")


def spacing_wait(since_last: Optional[float]) -> float:
    """前に内容を作った要求から `since_last` 秒たったときに、次を作る前に待つ秒（初めてなら 0）。"""
    if since_last is None:
        return 0.0
    return max(0.0, limits.value(SPACING, "interval") - since_last)
