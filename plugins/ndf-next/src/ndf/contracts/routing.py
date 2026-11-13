"""止まった理由から行き先を選ぶ純粋な関数（`pick`）と、打ち直しの待ちの計算。

行き先を選ぶ `pick` は種類と回数だけを受け、`summary`・`detail` の文を読まない。
種類の既定の行き先は止まった理由の表（`causes`）、再試行の値は上限の宣言（`limits`）から引く。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import NamedTuple, Optional

from ndf.contracts import causes, limits

# 既定の行き先（表の route）と、選んだ行き先（Route.to）の語
RETRY, STEP, HUMAN, END, STOP = "retry", "step", "human", "end", "stop"
# プランの on_cause の値のうち、ステップの id でないもの
PLAN_TARGETS = (RETRY, END, STOP)


class Route(NamedTuple):
    """選んだ行き先。`to` が step なら `target` はステップの id、retry なら `wait` は待つ秒。"""

    to: str
    target: Optional[str]
    wait: float
    kind: str


def backoff(policy: limits.Policy, used: int, retry_after: Optional[float], cap: Optional[float]) -> float:
    """次の待ちの秒。retry_after があればその値、無ければ interval × factor^used。max_interval と cap で頭打ち。"""
    if retry_after is not None:
        wait = float(retry_after)
    else:
        wait = float(policy.interval or 0) * float(policy.factor or 1) ** used
    for ceiling in (policy.max_interval, cap):
        if ceiling is not None:
            wait = min(wait, float(ceiling))
    return wait


class RetryState(NamedTuple):
    """打ち直しの文脈。`used` はこれまでの回数、`policy` は種類の方針、`cap` は待ちの頭打ち。"""

    used: int
    policy: Optional[limits.Policy]
    retry_after: Optional[float]
    cap: Optional[float]


def _fallback(kind: str, on_fail: Optional[str]) -> Route:
    """打ち直さないときの落とし先: on_fail があればそのステップ、無ければ人へ。"""
    if on_fail:
        return Route(STEP, on_fail, 0.0, kind)
    return Route(HUMAN, None, 0.0, kind)


def _retry_wait(state: RetryState, *, requires_retry_after: bool) -> Optional[float]:
    """打ち直せるなら次の待ちの秒。方針が無い・回数の上限に当たった・待つべき retry_after が無ければ None。"""
    policy = state.policy
    if policy is None or policy.attempts is None or state.used >= policy.attempts:
        return None
    if requires_retry_after and state.retry_after is None:
        return None
    return backoff(policy, state.used, state.retry_after, state.cap)


def _retry_or_next(kind: str, on_fail: Optional[str], state: RetryState, *, requires_retry_after: bool) -> Route:
    """打ち直せるなら retry（待ち 0 秒も打ち直す）、上限に当たったら打ち直しを外して on_fail へ、無ければ人へ。"""
    wait = _retry_wait(state, requires_retry_after=requires_retry_after)
    if wait is not None:
        return Route(RETRY, None, wait, kind)
    return _fallback(kind, on_fail)


def _destination(row: causes.CauseRow, on_cause: Mapping[str, str]) -> tuple[str, Optional[str]]:
    """行き先の語と on_cause の指定（無ければ None）。語は指定があればそれ、無ければ種類の既定（retry・step・human）。"""
    target = on_cause.get(row.kind)
    return (row.route if target is None else target), target


def pick(kind: str, on_cause: Mapping[str, str], on_fail: Optional[str], state: RetryState) -> Route:
    """行き先を 1 つ選ぶ: on_cause → 種類の既定の行き先 → on_fail → 人へ。

    既定が人への種類（unknown を含む）は on_fail へ渡さない。文（summary・detail）を受けない。
    """
    if state.used < 0:
        raise ValueError(f"打ち直しの回数は 0 以上: {state.used}")
    found = causes.row(kind)
    to, target = _destination(found, on_cause)
    if to == RETRY:
        return _retry_or_next(kind, on_fail, state, requires_retry_after=found.requires_retry_after)
    if to in (END, STOP):
        return Route(to, None, 0.0, kind)
    if target is not None:
        # 指定はステップの id
        return Route(STEP, target, 0.0, kind)
    if to == STEP:
        return _fallback(kind, on_fail)
    # 既定が人への種類は on_fail を見ない
    return _fallback(kind, None)
