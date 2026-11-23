"""PR へ書く操作が GitHub の二次レート制限に当たったときの待ち（#3941・#4016 の決定 8。純粋な関数）。

二次レート制限かどうかと、GitHub が待ちの秒を示したか（`hinted`）は、`github.GitHubError` の `secondary`・`hinted` が持つ
（見分けは `github_limits.limit_of` の 1 か所）。待ちの秒は、GitHub が示したら（`hinted`）その値、示さなければ
`cross-review.secondary-rate-limit` の `interval × factor^(回数 - 1)`（`max_interval` で頭打ち）。
打つ回数の上限は同じ方針の `attempts`（最初の 1 回を含む）。一次の上限（残りの回数の枯渇）は待たない（解除まで最大 1 時間）。
"""

from __future__ import annotations

from typing import Optional

from ndf.contracts import limits, routing

POLICY = "cross-review.secondary-rate-limit"


def wait_before_retry(limited: bool, attempt: int, retry_after: Optional[float], hinted: bool) -> Optional[float]:
    """`attempt` 回目（1 から）の失敗の後に待つ秒。打ち直さないなら None。"""
    policy = limits.policy(POLICY)
    if not limited or attempt >= int(limits.value(POLICY, "attempts")):
        return None
    if hinted and retry_after is not None:
        return retry_after
    return routing.backoff(policy, attempt - 1, None, None)
