"""claude -p の起動先の決定: 観測のスナップショット → 起動先の決定（#2930・#2931 を取り込む）。

I/O を持たない（時刻・ファイル・API を読まない）。同じスナップショットからは同じ決定が出る（I1）。
規則は「名前と小さな関数」の並び（`RULES`）で、上から当てて最初に決定を返したものを採る。アカウントの選ぶ順の組も
「名前・述語・組の中の並べ方」の並び（`GROUPS`）で持つ。設計の表は issues/issue-2930-design.md の「処理の流れ」にある。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

FIVE_HOUR_SECONDS = 5 * 3600.0
WEEK_SECONDS = 7 * 24 * 3600.0
MIN_ELAPSED = 60.0  # 枠の開始からの経過の下限（秒）。開始の直後に速さが無限へ振れないため
WEEKLY_AT_DEFAULT = 85.0  # 閾値 T の既定値（%。決定 10）
BUDGET_DAYS = 7.0  # 週の利用枠の日数（直近 7 日）
MONTH_DAYS = 30.0  # 月の額を日割りする日数

ROUTE_ACCOUNT, ROUTE_KEY, ROUTE_WAIT = "account", "api_key", "wait"
KEEP_CURRENT = "keep_current"


@dataclass(frozen=True)
class AccountObs:
    """1 つの登録済みアカウントの観測。`five_hour` は (使用率, リセット時刻) で、応答に 5 時間の枠が無ければ None。

    `weekly` は週の使用率（週の枠とモデル別の週の枠の最大。読めなければ None）。`limited_until` は上限にあるならその時刻
    （支出上限で時刻が無ければ無限）。`launchable` は起動できるか（認証の失敗の観測・この呼び出しの除外を含む）。"""

    name: str
    five_hour: tuple[float, float | None] | None = None
    weekly: float | None = None
    weekly_resets: float | None = None
    limited_until: float | None = None
    launchable: bool = True
    relogin: bool = False


@dataclass(frozen=True)
class KeyObs:
    """1 本の API キーの観測（値を持たない）。`monthly_usd` は月の額の宣言（無ければ None）、`spent_7d` は直近 7 日の使用額。"""

    name: str
    monthly_usd: float | None = None
    spent_7d: float = 0.0


@dataclass(frozen=True)
class Snapshot:
    """1 回の起動先の決定の入力。決定の時点の値で固めたもの。"""

    now: float
    use: str = "off"
    weekly_at: float = WEEKLY_AT_DEFAULT
    current: str | None = None
    accounts: tuple[AccountObs, ...] = ()
    keys: tuple[KeyObs, ...] = ()
    ledger_ok: bool = True
    exclude_keys: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Decision:
    """起動先の決定。`route` は `account`・`api_key`・`wait`。`account_rule` はアカウントを選んだときの組の名前か
    `keep_current`。`wait_until` は待つときの最も早い解除の時刻（None なら待つ時刻が無い）。"""

    route: str
    target: str | None
    rule: str
    account_rule: str | None = None
    evidence: dict = field(default_factory=dict)
    wait_until: float | None = None

    def row(self) -> dict:
        """記録と一覧へ写す形（値を持たない）。"""
        return {"route": self.route, "target": self.target, "rule": self.rule, "account_rule": self.account_rule, "evidence": self.evidence}


# ---------------------------------------------------------------- 週の利用枠


def weekly_budget(monthly_usd: float) -> float:
    """週の利用枠 = 月の額 × 7 ÷ 30。"""
    return monthly_usd * BUDGET_DAYS / MONTH_DAYS


def declared(k: KeyObs) -> bool:
    return k.monthly_usd is not None and k.monthly_usd > 0


def usage_ratio(k: KeyObs) -> float:
    """使用の割合 = 直近 7 日の使用額 ÷ 週の利用枠（宣言が無ければ無限）。"""
    return k.spent_7d / weekly_budget(k.monthly_usd) if declared(k) else math.inf


def budget_left(k: KeyObs) -> bool:
    return declared(k) and k.spent_7d < weekly_budget(k.monthly_usd)


# ---------------------------------------------------------------- 使い切るまでの時間


def lasts_until_reset(util: float, resets: float | None, length: float, now: float) -> tuple[bool, float]:
    """(リセットまで持つか, 使い切るまでの秒)。速さ = 使用率 ÷ 枠の開始からの経過（前提 6）。

    使用率が 0 ならリセットまで持つ。リセット時刻が無ければ、枠の長さだけ経ったものとして見積もり、リセットは来ない。"""
    until_reset = resets - now if resets is not None else math.inf
    elapsed = max(now - (resets - length), MIN_ELAPSED) if resets is not None else length
    speed = util / elapsed
    to_empty = (100.0 - util) / speed if speed > 0 else math.inf
    return to_empty >= until_reset, to_empty


def _window_key(util: float, resets: float | None, length: float, now: float, name: str) -> tuple:
    """組 1・2 の並べ方: リセットまで持つものを先に、その中でリセットの早い順。持たないものは使い切るまでの長い順。"""
    lasts, to_empty = lasts_until_reset(util, resets, length, now)
    return (0, resets if resets is not None else math.inf, name) if lasts else (1, -to_empty, name)


def _five_hour_key(a: AccountObs, now: float) -> tuple:
    util, resets = a.five_hour
    return _window_key(util, resets, FIVE_HOUR_SECONDS, now, a.name)


def _weekly_key(a: AccountObs, now: float) -> tuple:
    return _window_key(a.weekly, a.weekly_resets, WEEK_SECONDS, now, a.name)


# ---------------------------------------------------------------- 選ぶ順の組


def _readable(a: AccountObs) -> bool:
    return a.weekly is not None


def _below(a: AccountObs, t: float) -> bool:
    return _readable(a) and a.weekly < t


@dataclass(frozen=True)
class Group:
    name: str
    member: object  # (AccountObs, 閾値 T) -> bool
    order: object  # (AccountObs, 今) -> 並べ方の鍵


GROUPS: tuple[Group, ...] = (
    Group("five_hour_first", lambda a, t: _below(a, t) and a.five_hour is not None, _five_hour_key),
    Group("weekly_only", lambda a, t: _below(a, t) and a.five_hour is None, _weekly_key),
    Group("over_threshold", lambda a, t: _readable(a), lambda a, now: (a.weekly, a.name)),
    Group("usage_unknown", lambda a, t: True, lambda a, now: (a.name,)),
)


def candidate(a: AccountObs) -> bool:
    """候補に入るか（I4）: 上限になく、起動でき、再登録が要らない。"""
    return a.limited_until is None and a.launchable and not a.relogin


def group_of(a: AccountObs, t: float) -> int | None:
    """候補なら入る組の番号（0 始まり）。候補でなければ None。"""
    if not candidate(a):
        return None
    return next(i for i, g in enumerate(GROUPS) if g.member(a, t))


def ranked(snap: Snapshot) -> list[tuple[int, AccountObs]]:
    """候補を (組の番号, 観測) で、組の番号の小さい順・組の中の並べ方の順に並べたもの。"""
    t = snap.weekly_at
    pairs = [(group_of(a, t), a) for a in snap.accounts]
    pairs = [(g, a) for g, a in pairs if g is not None]
    return sorted(pairs, key=lambda p: (p[0], GROUPS[p[0]].order(p[1], snap.now)))


def _account_evidence(a: AccountObs, group: int, snap: Snapshot) -> dict:
    return {
        "group": GROUPS[group].name,
        "weekly": a.weekly,
        "five_hour": a.five_hour[0] if a.five_hour else None,
        "five_hour_resets": a.five_hour[1] if a.five_hour else None,
        "weekly_resets": a.weekly_resets,
        "weekly_at": snap.weekly_at,
        "weekly_min": _weekly_min(snap),
    }


def _weekly_min(snap: Snapshot) -> float | None:
    vals = [a.weekly for a in snap.accounts if not a.relogin and a.weekly is not None]
    return min(vals) if vals else None


def _wait_until(snap: Snapshot) -> float | None:
    vals = [a.limited_until for a in snap.accounts if not a.relogin and a.limited_until is not None]
    return min(vals) if vals else None


def account_decision(snap: Snapshot, rule: str | None = None) -> Decision:
    """アカウントの選び方（規則 6・7）。今のアカウントが最良の候補と同じ組にあれば保つ（切り替えの余裕。I5）。

    `rule` を渡すと規則の名前だけを差し替える（規則 3〜5）。"""
    order = ranked(snap)
    if not order:
        return Decision(ROUTE_WAIT, None, rule or "wait_reset", evidence={"weekly_min": _weekly_min(snap)}, wait_until=_wait_until(snap))
    best_group = order[0][0]
    kept = next(((g, a) for g, a in order if a.name == snap.current and g == best_group), None)
    group, pick = kept or order[0]
    how = KEEP_CURRENT if kept is not None else GROUPS[group].name
    return Decision(ROUTE_ACCOUNT, pick.name, rule or "account", how, _account_evidence(pick, group, snap))


# ---------------------------------------------------------------- 規則


def near_limit_due(snap: Snapshot) -> bool:
    """near-limit の発動: 再登録が要るものを除いた登録済みアカウントが 1 つ以上あり、すべての週の使用率が読めて T 以上（I2）。"""
    accs = [a for a in snap.accounts if not a.relogin]
    return snap.use == "near-limit" and bool(accs) and all(a.weekly is not None and a.weekly >= snap.weekly_at for a in accs)


def _open_keys(snap: Snapshot) -> list[KeyObs]:
    return [k for k in snap.keys if k.name not in snap.exclude_keys]


def _key_evidence(k: KeyObs, snap: Snapshot) -> dict:
    return {
        "monthly_usd": k.monthly_usd,
        "spent_7d": round(k.spent_7d, 4),
        "weekly_budget": round(weekly_budget(k.monthly_usd), 4) if declared(k) else None,
        "ratio": round(usage_ratio(k), 4) if declared(k) else None,
        "weekly_at": snap.weekly_at,
        "weekly_min": _weekly_min(snap),
    }


def _always_key(snap: Snapshot) -> Decision | None:
    keys = sorted(_open_keys(snap), key=lambda k: k.name)
    if snap.use != "always" or not keys:
        return None
    return Decision(ROUTE_KEY, keys[0].name, "always_key", evidence=_key_evidence(keys[0], snap))


def _near_limit_key(snap: Snapshot) -> Decision | None:
    left = sorted((k for k in _open_keys(snap) if budget_left(k)), key=lambda k: (usage_ratio(k), k.name))
    if not (near_limit_due(snap) and snap.ledger_ok and left):
        return None
    return Decision(ROUTE_KEY, left[0].name, "near_limit_key", evidence=_key_evidence(left[0], snap))


def _fallback_rule(name: str, applies):
    """規則 3〜5: 前提の条件がそろい `applies` が真なら、アカウントの選び方で決め、規則の名前だけを差し替える。"""

    def rule(snap: Snapshot) -> Decision | None:
        if not (near_limit_due(snap) and _open_keys(snap) and applies(snap)):
            return None
        return account_decision(snap, name)

    return rule


RULES: tuple[tuple[str, object], ...] = (
    ("always_key", _always_key),
    ("near_limit_key", _near_limit_key),
    ("near_limit_ledger_unreadable", _fallback_rule("near_limit_ledger_unreadable", lambda s: not s.ledger_ok)),
    ("near_limit_no_declared_key", _fallback_rule("near_limit_no_declared_key", lambda s: not any(declared(k) for k in _open_keys(s)))),
    ("near_limit_budget_used_up", _fallback_rule("near_limit_budget_used_up", lambda s: not any(budget_left(k) for k in _open_keys(s)))),
    ("account", account_decision),
)


def decide(snap: Snapshot) -> Decision:
    """起動先の決定。規則の表を上から当て、最初に決定を返した規則を採る（最後の規則は必ず決定を返す）。"""
    return next(d for d in (fn(snap) for _, fn in RULES) if d is not None)
