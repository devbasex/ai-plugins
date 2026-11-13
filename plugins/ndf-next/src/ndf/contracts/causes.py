"""止まった理由の表（`contracts/causes.json`）の読み手と、行き先を選ぶ純粋な関数。

種類の語彙・終了コード・既定の行き先は表だけが持ち、再試行の値は上限の宣言だけが持つ。
コードは種類を `cause("<種類>", ...)` の引数の文字列でだけ書き、種類の定数を置かない。
行き先を選ぶ `pick` は種類と回数だけを受け、`summary`・`detail` の文を読まない。
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from typing import Any, NamedTuple, Optional

from ndf import contracts
from ndf.contracts import facts, limits

TABLE = "causes.json"
SCHEMA = contracts.schema_path("causes")
EXIT_CODES = "exit-codes"
# 既定の行き先（表の route）と、選んだ行き先（Route.to）の語
RETRY, STEP, HUMAN, END, STOP = "retry", "step", "human", "end", "stop"
# プランの on_cause の値のうち、ステップの id でないもの
PLAN_TARGETS = (RETRY, END, STOP)


class CausesError(contracts.ContractError):
    """表が無い・形が違う・表に無い種類を引いた。"""


class CauseRow(NamedTuple):
    kind: str
    meaning: str
    std_type: str
    exit: int
    route: str
    retry: Optional[str]
    requires_retry_after: bool = False


class Route(NamedTuple):
    """選んだ行き先。`to` が step なら `target` はステップの id、retry なら `wait` は待つ秒。"""

    to: str
    target: Optional[str]
    wait: float
    kind: str


def exit_code_bounds(row: Mapping[str, Any]) -> tuple[int, int]:
    """事実の表 exit-codes の 1 行の終了コードの範囲（始点と終点。last が無ければ code だけ）。"""
    return row["code"], row.get("last", row["code"])


def codes_for_status(status: str) -> frozenset[int]:
    """`status` の結果が返してよい終了コード（事実の表 exit-codes から）。"""
    out: set[int] = set()
    for r in facts.rows(EXIT_CODES, status=status):
        first, last = exit_code_bounds(r)
        out.update(range(first, last + 1))
    return frozenset(out)


def stopped_codes() -> frozenset[int]:
    """`stopped` の結果が返してよい終了コード。"""
    return codes_for_status("stopped")


def _policy_errors(row: CauseRow) -> list[str]:
    if row.retry is None:
        return []
    try:
        p = limits.policy(row.retry)
    except limits.LimitsError as exc:
        return [f"{row.kind} の再試行の方針: {exc}"]
    errs = [] if p.attempts is not None else [f"{row.kind} の方針 {row.retry} に attempts が無い"]
    if p.interval is None and not row.requires_retry_after:
        errs.append(f"{row.kind} の方針 {row.retry} に interval が無い（retry_after を待つ種類でもない）")
    return errs


def validate(raw: Any) -> dict[str, CauseRow]:
    """表の形・種類の重なり・終了コードの区分・再試行の方針の有無を確かめ、種類ごとの行を返す。"""
    errs = contracts.schema_errors(raw, SCHEMA)
    if errs:
        raise CausesError(f"止まった理由の表 {TABLE} の形が違う: {'; '.join(errs)}")
    out: dict[str, CauseRow] = {}
    codes = stopped_codes()
    for item in raw["causes"]:
        row = CauseRow(**item)
        if row.kind in out:
            errs.append(f"種類 {row.kind} が 2 つある")
        if row.exit not in codes:
            errs.append(f"{row.kind} の終了コード {row.exit} は stopped の終了コード {sorted(codes)} に無い")
        errs += _policy_errors(row)
        out[row.kind] = row
    if errs:
        raise CausesError(f"止まった理由の表 {TABLE} の誤り: {'; '.join(errs)}")
    return out


@cache
def table() -> dict[str, CauseRow]:
    """表を 1 プロセスに 1 回読み、種類ごとの行を返す。"""
    with contracts.raised_as(CausesError):
        return validate(contracts.load_json(TABLE))


contracts.on_reset(table.cache_clear)


def row(kind: str) -> CauseRow:
    found = table().get(kind)
    if found is None:
        raise CausesError(f"止まった理由の表に種類 {kind!r} が無い")
    return found


def build(kind: str, detail: str, *, instance: Optional[str] = None, retry_after: Optional[float] = None) -> dict[str, Any]:
    """結果 JSON の `cause` を確かめずに組む。`instance`・`retry_after` は渡したときだけ置く。"""
    out: dict[str, Any] = {"kind": kind, "detail": detail}
    if instance is not None:
        out["instance"] = instance
    if retry_after is not None:
        out["retry_after"] = retry_after
    return out


def cause(kind: str, detail: str, *, instance: Optional[str] = None, retry_after: Optional[float] = None) -> dict[str, Any]:
    """結果 JSON の `cause` を組む。表に無い種類・空の説明は `ValueError`。"""
    out = build(kind, detail, instance=instance, retry_after=retry_after)
    bad = errors(out)
    if bad:
        raise ValueError("; ".join(bad))
    return out


def _kind_errors(value: dict) -> list[str]:
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in table():
        return [f"cause.kind {kind!r} は止まった理由の表に無い"]
    return []


def _detail_errors(value: dict) -> list[str]:
    detail = value.get("detail")
    if not isinstance(detail, str) or not detail.strip():
        return ["cause.detail は空でない文字列で書く"]
    return []


def _optional_key_errors(value: dict) -> list[str]:
    """知らないキーと、任意のキー（instance・retry_after）の型の誤り。"""
    errs: list[str] = []
    unknown = sorted(set(value) - {"kind", "detail", "instance", "retry_after"})
    if unknown:
        errs.append(f"cause に知らないキーがある: {', '.join(unknown)}")
    if "instance" in value and (not isinstance(value["instance"], str) or not value["instance"]):
        errs.append("cause.instance は空でない文字列で書く")
    ra = value.get("retry_after")
    if "retry_after" in value and (isinstance(ra, bool) or not isinstance(ra, (int, float)) or ra < 0):
        errs.append("cause.retry_after は 0 以上の数で書く")
    return errs


def errors(value: object) -> list[str]:
    """`cause` の値の形の誤りを並べる。空なら、種類が表にあり、説明が空でなく、任意のキーの型が合う。"""
    if not isinstance(value, dict):
        return ["cause はオブジェクトで書く"]
    return _kind_errors(value) + _detail_errors(value) + _optional_key_errors(value)


def exit_code(kind: str) -> int:
    """種類の終了コード。"""
    return row(kind).exit


def policy_of(kind: str) -> Optional[limits.Policy]:
    """種類の再試行の方針。既定の行き先が打ち直しでない種類は None。"""
    name = row(kind).retry
    return limits.policy(name) if name else None


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


def _retry_or_next(kind: str, on_fail: Optional[str], state: RetryState) -> Route:
    """打ち直せるなら retry、上限に当たったら打ち直しを外して on_fail へ、無ければ人へ。"""
    policy = state.policy
    can = policy is not None and policy.attempts is not None and state.used < policy.attempts
    if can and row(kind).requires_retry_after and state.retry_after is None:
        can = False
    if can:
        wait = backoff(policy, state.used, state.retry_after, state.cap)  # type: ignore[arg-type]
        if wait > 0:
            return Route(RETRY, None, wait, kind)
    if on_fail:
        return Route(STEP, on_fail, 0.0, kind)
    return Route(HUMAN, None, 0.0, kind)


def pick(kind: str, on_cause: Mapping[str, str], on_fail: Optional[str], state: RetryState) -> Route:
    """行き先を 1 つ選ぶ: on_cause → 種類の既定の行き先 → on_fail → 人へ。

    既定が人への種類（unknown を含む）は on_fail へ渡さない。文（summary・detail）を受けない。
    """
    if state.used < 0:
        raise ValueError(f"打ち直しの回数は 0 以上: {state.used}")
    default = row(kind).route
    target = on_cause.get(kind)
    # 行き先の語: on_cause の指定があればそれ、無ければ種類の既定（retry・step・human）
    to = default if target is None else target
    if to == RETRY:
        return _retry_or_next(kind, on_fail, state)
    if to in (END, STOP):
        return Route(to, None, 0.0, kind)
    if target is not None:
        # 指定はステップの id
        return Route(STEP, target, 0.0, kind)
    if to == STEP and on_fail:
        return Route(STEP, on_fail, 0.0, kind)
    return Route(HUMAN, None, 0.0, kind)
