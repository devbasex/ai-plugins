"""止まった理由から行き先を選ぶ純粋な関数（`pick`）と、打ち直しの待ちの計算。

行き先の選び方はここだけが持つ（プランの実行・進行表は `pick` を呼び、別の選び方を持たない）。
`pick` は種類と回数だけを受け、`summary`・`detail` の文を読まない。
種類の既定の行き先と打ち直さないときの行き先は止まった理由の表（`causes`）、再試行の値は上限の宣言（`limits`）から引く。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import NamedTuple, Optional, Union

from ndf.contracts import causes, limits

# 行き先の語。retry・step・conductor・human は表の route と exhausted の語、end・stop はプランの on_cause だけが書く
RETRY, STEP, CONDUCTOR, HUMAN, END, STOP = "retry", "step", "conductor", "human", "end", "stop"
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
        interval = policy.interval if policy.interval is not None else 0.0
        factor = policy.factor if policy.factor is not None else 1.0
        wait = float(interval) * float(factor) ** used
    for ceiling in (policy.max_interval, cap):
        if ceiling is not None:
            wait = min(wait, float(ceiling))
    return wait


def _to_step(kind: str, on_fail: Optional[str]) -> Route:
    """on_fail のステップへ。on_fail が無ければ conductor が次の手を選ぶ。"""
    if on_fail:
        return Route(STEP, on_fail, 0.0, kind)
    return Route(CONDUCTOR, None, 0.0, kind)


def _to(dest: str, kind: str, on_fail: Optional[str]) -> Route:
    """表の語（step・conductor・human）の行き先。"""
    return _to_step(kind, on_fail) if dest == STEP else Route(dest, None, 0.0, kind)


class RetryState(NamedTuple):
    """打ち直しの文脈。

    `used` はこれまでに打ち直した回数、`policy` は呼び手が宣言（プロジェクトの causes・ステップの retry）から解決した
    再試行の方針（無ければ種類の既定）、`retry_after` は結果の `cause.retry_after`、`cap` は待ちの頭打ちの秒。
    """

    used: int = 0
    policy: Optional[limits.Policy] = None
    retry_after: Optional[float] = None
    cap: Optional[float] = None


def _retry_wait(row: causes.CauseRow, state: RetryState) -> Optional[float]:
    """打ち直せるなら次の待ちの秒。方針が無い・回数の上限に当たった・待つべき retry_after が無ければ None。

    方針は `state.policy`、無ければ種類の既定（causes.policy_of）。
    """
    policy = state.policy if state.policy is not None else causes.policy_of(row.kind)
    if policy is None or policy.attempts is None or state.used >= policy.attempts:
        return None
    if row.requires_retry_after and state.retry_after is None:
        return None
    return backoff(policy, state.used, state.retry_after, state.cap)


def _destination(row: causes.CauseRow, kind: str, on_cause: Optional[Mapping[str, str]]) -> Union[str, Route]:
    """on_cause の指定（無ければ表の route）から行き先の語を決める。指定がステップの id ならその Route を返す。"""
    target = on_cause.get(kind) if on_cause is not None else None
    if target is None:
        return row.route
    if target in PLAN_TARGETS:
        return target
    # 指定はステップの id
    return Route(STEP, target, 0.0, kind)


def pick(
    kind: str,
    *,
    on_cause: Optional[Mapping[str, str]] = None,
    on_fail: Optional[str] = None,
    retry: RetryState = RetryState(),
) -> Route:
    """行き先を 1 つ選ぶ: on_cause → 種類の既定の行き先（表の route）→ 打ち直さないときの行き先（表の exhausted）。

    - `retry` は打ち直しの文脈（回数・方針・retry_after・待ちの頭打ち。`RetryState`）
    - 打ち直す（待ち 0 秒も打ち直す）のは、方針の回数が残り、retry_after を待つ種類なら retry_after があるとき
    - step は on_fail のステップ、無ければ conductor。既定が conductor・human の種類は on_fail を見ない
    - 文（summary・detail）を受けない
    """
    if retry.used < 0:
        raise ValueError(f"打ち直しの回数は 0 以上: {retry.used}")
    row = causes.row(kind)
    to = _destination(row, kind, on_cause)
    if isinstance(to, Route):
        return to
    if to == RETRY:
        wait = _retry_wait(row, retry)
        if wait is not None:
            return Route(RETRY, None, wait, kind)
        # 表の exhausted が無い種類（on_cause で retry を指定した種類）は step と同じく on_fail へ
        return _to(row.exhausted or STEP, kind, on_fail)
    if to in (END, STOP):
        return Route(to, None, 0.0, kind)
    return _to(to, kind, on_fail)
