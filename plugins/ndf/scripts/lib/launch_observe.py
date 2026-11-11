"""claude -p の起動先の決定: 観測の収集と決定のエントリポイント（#2930）。

アカウントの使用率（控えを通す）・上限の観測・起動できるか・API キーの宣言・帳簿の直近 7 日の使用額・時刻を集めて
`launch_rules.Snapshot` を組み、`launch_rules.decide` を呼ぶ。判定のための状態を読まず、書かない（I8）。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import claude_accounts as ca
import claude_api_keys as ak
import usage_ledger
from launch_rules import WEEK_SECONDS, AccountObs, Decision, KeyObs, Snapshot, decide  # noqa: F401  decide は呼び手が使う


def _five_hour(u) -> tuple[float, float | None] | None:
    """5 時間の枠の (使用率, リセット時刻)。応答に 5 時間の枠が無ければ None（5 時間の枠の無いアカウント）。"""
    w = u.five_hour if u is not None and u.windows_known() else None
    if not isinstance(w, dict) or not isinstance(w.get("utilization"), (int, float)):
        return None
    return float(w["utilization"]), u.resets("five_hour")


def account_obs(name: str, exclude, keep, now: float) -> AccountObs:
    """1 つの登録済みアカウントの観測。使用率の取得は控えを通して多くて 1 回（`claude_accounts.usage`）。"""
    ca.usage(name, name not in keep, now)
    launchable = name not in exclude and ca.usable(name, now)
    acc = ca.load_account(name)
    if acc is None:
        return AccountObs(name, launchable=False, relogin=True)
    u = acc.usage if acc.known() else None
    return AccountObs(
        name,
        five_hour=_five_hour(u),
        weekly=u.weekly_score() if u is not None else None,
        weekly_resets=u.resets("seven_day") if u is not None else None,
        limited_until=acc.limited_until(now),
        launchable=launchable and not acc.auth_held(now),
        relogin=acc.needs_relogin,
    )


def _since(now: float) -> str:
    return datetime.fromtimestamp(now - WEEK_SECONDS, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def key_obs(keys: list, use: str, now: float) -> tuple[tuple[KeyObs, ...], bool]:
    """API キーの観測と、帳簿を読めたか。帳簿は near-limit でキーがあるときだけ読む（直近 7 日の使用額）。"""
    spent, ok = {}, True
    if use == "near-limit" and keys:
        try:
            spent = usage_ledger.api_key_spent(_since(now))
        except OSError:
            ok = False
    return tuple(KeyObs(k.name, k.monthly_usd, spent.get(k.name, 0.0)) for k in keys), ok


def _book(use: str | None) -> tuple[str, float, list]:
    """(使い方の宣言, 閾値 T, 登録済み API キー)。登録簿が壊れていれば `off`・既定の T・0 本（I6）。"""
    try:
        keys = ak.keys()
    except ak.BookError:
        return "off", ak.weekly_at_setting(), []
    return (use if use is not None else ak.use_setting()), ak.weekly_at_setting(), keys


def observe(current=None, exclude_accounts=(), exclude_keys=(), keep=(), use: str | None = None, now: float | None = None) -> Snapshot:
    """観測のスナップショットを組む。`use` が None なら登録簿の宣言を読む（relay と `choose` は `off` を渡す）。"""
    now = time.time() if now is None else now
    how, weekly_at, keys = _book(use)
    accounts = tuple(account_obs(n, set(exclude_accounts), set(keep), now) for n in ca.names())
    kobs, ledger_ok = key_obs(keys, how, now)
    return Snapshot(now, how, weekly_at, current, accounts, kobs, ledger_ok, frozenset(exclude_keys))


def decide_now(current=None, exclude_accounts=(), exclude_keys=(), keep=(), use: str | None = None, now: float | None = None) -> Decision:
    """観測を集めて起動先を決める（決定のエントリポイント）。例外を投げない形で観測を組む。"""
    return decide(observe(current, exclude_accounts, exclude_keys, keep, use, now))
