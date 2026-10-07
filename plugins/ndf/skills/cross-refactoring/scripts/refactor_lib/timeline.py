"""時間の上限を想定最大時間から逆算する（#933 決定 23・24、#1334 決定 8）。

**時間に関わる数値は、想定最大時間 B（`--budget-minutes`）・宣言の全体テストの所要 w・着手前に手元で走らせたテストの
実測 x・CI の壁時計 c からの算術だけで出す。** テストと CI の待ちの係数は共通層の `test_strategy` にあり、
supervise と同じ式で出す（I10）。手順の枠の係数はここに置く。式は `docs/02-plan-and-implement.md` の「締め切り」の
節にまとめてある。実行の途中で数値を決めるために LLM へ問わない。

値は `init`（改修計画の前の値）と `merge-plan`（改修計画の後の値）が `state["limits"]` へ書き出す。以後の手順は、
書き出した値と時計の比較だけで進み、止まる。

**純粋な処理だけを置く。** 今の時刻は引数で受ける。
"""

from __future__ import annotations

import datetime as _dt
import math
from typing import Any, Optional

import test_strategy as ts

from . import budget, clock

# 手順の枠の係数（決定 24）。B に掛ける比率で、秒や分の固定値は持たない。
PROPOSE_SHARE = 0.20  # 提案の枠の終わり = 開始 + o + 0.20·B（o は着手前のテストの実測 x。#1385）
PLAN_SHARE = 0.10  # 改修計画の枠の終わり = 提案の枠の終わり + 0.10·B
MARGIN_SHARE = 0.05  # 余裕 = 0.05·B（手順の上限と CLI の上限に足す）
MEASURE_SHARE = 0.05  # 指標の測定の上限 = 0.05·B（提案の枠の中から割く。#1319 の決定 7）
MEASURE_PROPOSE_CAP = 0.5  # 測定に使える時間は、提案の枠の終わりまでの残りの半分まで
# 枠が想定最大時間に収まらずに止めた後、人が `init` を打ち直すまでの見込み（秒）。要る想定最大時間の下限に足す（#1385 決定 2）。
RESUME_GRACE_SECONDS = 300
STOP_REVERT_SHARE = 0.20  # 打ち切りの後の取り消し（案 A）の締め切り = 最終ゲートの修正の打ち切り + 0.20·B（#1669 決定 8）
# テストと CI の待ちの係数は `test_strategy` が持つ（cross-refactoring と supervise で同じ値）。
INIT_TEST_SHARE = ts.INIT_TEST_SHARE
TEST_FACTOR = ts.TEST_FACTOR
TEST_FLOOR_SHARE = ts.TEST_FLOOR_SHARE
CI_WAIT_SHARE = ts.CI_WAIT_SHARE

# 固定のまま残す値（決定 24）。OS の後始末と通信の待ちで、予算と性質が違う。報告に並べる。
FIXED_VALUES = (
    ("gitfacts.run_with_timeout の kill_grace", "5 秒", "打ち切ったプロセスグループへ SIGKILL を送るまでの待ち（テストと指標の測定）"),
    ("monitor.py の SIGTERM の猶予", "3 秒", "監視が止めた CLI へ SIGKILL を送るまでの待ち"),
    ("monitor.py の RESULT_AGE_GRACE", "30 秒", "結果ファイルを書き終えたとみなす経過"),
    ("monitor.py の見回りの間隔", "15 秒", "監視の 1 周期"),
    ("jev.py の PROBE_TIMEOUT / ASK_TIMEOUT", "10 秒 / 20 秒", "改修計画までの Jev の通信 1 回の待ち"),
    ("auth.py の AUTH_PROBE_TIMEOUT", "120 秒", "init の参加者の認証の確認 1 回の待ち"),
)

# 手順ごとの終わりの時刻のキー。`start-phase` がここから監視の上限を出す。
PHASE_END_KEYS = {
    "propose": "propose_end_at",
    "plan": "plan_end_at",
    "add-tests": "add_tests_end_at",
    "implement": "implement_end_at",
    "fix": "fix_end_at",
    "final-fix": "final_end_at",
}


def _seconds(budget_minutes: int, share: float) -> int:
    return math.ceil(float(budget_minutes) * 60 * share)


def margin(budget_minutes: int) -> int:
    """余裕（秒）。手順の上限と CLI の上限に足す。"""
    return _seconds(budget_minutes, MARGIN_SHARE)


def measure_timeout(budget_minutes: int) -> int:
    """指標の測定の上限（秒）。"""
    return _seconds(budget_minutes, MEASURE_SHARE)


def measure_deadline(now: _dt.datetime, limits: dict[str, Any]) -> int:
    """その実行で測定に使える秒 = min(上限, 0.5·max(0, 提案の枠の終わり − 今))。

    **提案の枠の終わりは動かさない**（決定 7）。着手前のテストが長い実行でも、提案の時間を
    測定が食い尽くさない。
    """
    cap = int(limits.get("measure_timeout") or 0)
    end = clock.parse(limits.get("propose_end_at"))
    if end is None:
        return cap
    left = max(0.0, (end - now).total_seconds())
    return max(0, min(cap, math.floor(MEASURE_PROPOSE_CAP * left)))


def strategy_of(state: dict[str, Any]) -> ts.Strategy:
    """状態の戦略。無い（この変更より前の状態ファイル）ときは、記録の `baseline_test.command` を全体テスト、
    `round_test.command` をラウンドテストとして読む（値を読むだけで、文字列は解析しない）。"""
    data = state.get("strategy")
    if isinstance(data, dict) and data.get("name"):
        return ts.Strategy.from_state(data)
    whole = (state.get("baseline_test") or {}).get("command")
    round_command = (state.get("round_test") or {}).get("command")
    suites = [ts.Suite("baseline", str(whole), paths=["."])] if whole else []
    if round_command:
        return ts.Strategy(ts.ROUND_ONLY, "state:round_test", suites, round_command=str(round_command))
    return ts.Strategy(ts.LOCAL_FULL, "state:baseline_test", suites)


def test_limits(state: dict[str, Any]) -> dict[str, Any]:
    """テストと CI の待ちの上限（`test_strategy.limits`）。入力は着手前の記録から取る（範囲テストの所要 s を含む。I6）。"""
    baseline = state.get("baseline_test") or {}
    return ts.limits(
        strategy_of(state),
        int(state["budget_minutes"]),
        whole_seconds_value=baseline.get("whole_seconds"),
        whole_source=baseline.get("whole_source"),
        ci_seconds=baseline.get("ci_seconds"),
        measured_seconds=baseline.get("seconds"),
        scope_seconds=baseline.get("scope_seconds"),
        scope_source=baseline.get("scope_source"),
    )


def window_offset(state: dict[str, Any]) -> float:
    """手順の枠の起点のずれ o（秒）。着手前のテストの実測 x（無ければ 0）。枠が収まらずに止めた後の打ち直し
    （`resumed_at`）があれば `max(x, resumed_at − started_at)`（I2）。"""
    x = float((state.get("baseline_test") or {}).get("seconds") or 0.0)
    started = clock.parse(state.get("started_at"))
    resumed = clock.parse(state.get("resumed_at"))
    if started is not None and resumed is not None:
        x = max(x, (resumed - started).total_seconds())
    return max(0.0, x)


# 実装の終わりに検証の見積りを引く「採っていて未検証」の項目の状態（I13）。
UNVERIFIED = ("planned", "tested", "implemented", "failing")


def pending_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """採っていて未検証の項目。前の巡で検証を終えた・取り消した・見送った・持ち越した項目は数えない。"""
    return [i for i in items if i.get("status", "planned") in UNVERIFIED]


def _iso(value: Optional[_dt.datetime]) -> Optional[str]:
    return clock.iso(value) if value is not None else None


def compute(
    started_at: _dt.datetime,
    budget_minutes: int,
    tests: Any,
    items: Optional[list[dict[str, Any]]] = None,
    reserve: Optional[dict[str, Any]] = None,
    offset_seconds: Optional[float] = None,
    after_plan: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """実行時の値の表。`tests` は `test_strategy.limits` の表（数値か `None` なら、着手前の実測として `local-full` の表を組む）。
    `items` と `reserve`（改修計画の後）が無ければ、その行は `None`。`after_plan` は計画の後に要る時間の見込み
    （`{"reserve_minutes", "item_minutes", "table_source"}`。#1743 の F2）で、無ければ 0 分として扱う。

    実装の終わりは `final_end − R − Σ（採っていて未検証の項目の verify）`、テストの追加の終わりはそこから同じ項目の
    implement を引いた時刻である（I13）。項目ごとの期限は持たない。

    提案と改修計画の枠は `started_at + offset_seconds`（o。着手前のテストの終わり）から数える（#1385 I2）。
    `final_end_at` と `fix_end_at`・`stop_revert_end_at` は `started_at` から数える（I1・I3）。"""
    b = int(budget_minutes)
    if not isinstance(tests, dict):
        tests = ts.limits(ts.Strategy(ts.LOCAL_FULL, "args"), b, measured_seconds=tests)
    origin = started_at + _dt.timedelta(seconds=max(0.0, float(offset_seconds or 0.0)))
    propose_end = origin + _dt.timedelta(minutes=b * PROPOSE_SHARE)
    plan_end = propose_end + _dt.timedelta(minutes=b * PLAN_SHARE)
    planned = reserve is not None
    final_end = started_at + _dt.timedelta(minutes=b)
    pending = pending_items(list(items or []))
    implement_end = budget.implement_end(final_end, reserve, pending) if planned else None
    add_tests_end = budget.add_tests_end(implement_end, pending) if implement_end is not None else None
    basis = dict(tests.get("basis") or {})
    if after_plan:
        basis["after_plan"] = dict(after_plan)
    return {
        "budget_minutes": b,
        "margin_seconds": margin(b),
        "init_test_timeout": int(tests["init_test_timeout"]),
        "measure_timeout": measure_timeout(b),
        "test_timeout": int(tests["test_timeout"]),
        "whole_timeout": int(tests["whole_timeout"]),
        "ci_wait_timeout": int(tests["ci_wait_timeout"]),
        "basis": basis,
        "propose_end_at": _iso(propose_end),
        "plan_end_at": _iso(plan_end),
        "add_tests_end_at": _iso(add_tests_end),
        "implement_end_at": _iso(implement_end),
        "fix_end_at": _iso(budget.fix_end(started_at, b, reserve)) if planned else None,
        "final_end_at": _iso(final_end),
        "stop_revert_end_at": _iso(started_at + _dt.timedelta(minutes=b * (1 + STOP_REVERT_SHARE))) if planned else None,
        # 最終ゲートの修正の 1 回目に必ず渡す長さ（決定 26）。予備時間の `final_fix`。
        "final_fix_seconds": (math.ceil(float(reserve.get("final_fix") or 0.0) * 60) if planned else None),
        # 計画の後に要る時間の見込み（バッファの見込み R + 1 件の長さ L）。提案の前に止まる判定に使う（I8）。
        "after_plan_minutes": after_plan_minutes(after_plan),
    }


def after_plan_minutes(after_plan: Optional[dict[str, Any]]) -> float:
    """見込みの分（R + L）。無ければ 0（この変更より前の表。今の振る舞い）。"""
    if not after_plan:
        return 0.0
    return round(float(after_plan.get("reserve_minutes") or 0.0) + float(after_plan.get("item_minutes") or 0.0), 2)


def of_state(state: dict[str, Any], after_plan: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """状態の値から表を組む。改修計画の後なら項目と予備時間も使う。`after_plan` が無ければ、書き出した表の見込みを引き継ぐ。"""
    plan = state.get("plan") or None
    if after_plan is None:
        after_plan = ((state.get("limits") or {}).get("basis") or {}).get("after_plan")
    return compute(
        clock.parse(state["started_at"]),
        int(state["budget_minutes"]),
        test_limits(state),
        state.get("items") if plan else None,
        (plan or {}).get("reserve") if plan else None,
        window_offset(state),
        after_plan,
    )


def required_budget_minutes(offset_seconds: float, after_plan: float = 0.0) -> int:
    """枠の起点が o のとき、提案と改修計画の枠の後に見込み（R + L）が収まる想定最大時間の下限（分）。
    人が打ち直すまでの見込みを足す（I8）。見込みが 0 なら枠だけが収まる下限である。"""
    share = round(1.0 - PROPOSE_SHARE - PLAN_SHARE, 9)
    return math.ceil(round(((float(offset_seconds) + RESUME_GRACE_SECONDS) / 60 + float(after_plan or 0.0)) / share, 9))


def window_problem(limits: dict[str, Any]) -> Optional[str]:
    """提案と改修計画の枠の終わり（`plan_end_at`）に、計画の後に要る時間の見込み（`after_plan_minutes`）を足した時刻が
    想定最大時間の終わり（`final_end_at`）を越えるなら止める理由の文。収まれば `None`（#1385 I4・#1743 I8）。"""
    plan_end = clock.parse(limits.get("plan_end_at"))
    final_end = clock.parse(limits.get("final_end_at"))
    after = float(limits.get("after_plan_minutes") or 0.0)
    if plan_end is None or final_end is None or plan_end + _dt.timedelta(minutes=after) <= final_end:
        return None
    b = int(limits["budget_minutes"])
    started = final_end - _dt.timedelta(minutes=b)
    offset = max(0.0, (plan_end - started).total_seconds() - b * 60 * (PROPOSE_SHARE + PLAN_SHARE))
    x = (limits.get("basis") or {}).get("x")
    spent = f"{x} 秒" if x is not None else "時間が"
    later = f"（打ち直した時刻は開始から {math.ceil(offset)} 秒）" if x is None or offset > float(x) + 1 else ""
    window = _seconds(b, PROPOSE_SHARE + PLAN_SHARE)
    per_minute = round(60 / (60 * round(1.0 - PROPOSE_SHARE - PLAN_SHARE, 9)), 2)
    detail = ((limits.get("basis") or {}).get("after_plan")) or {}
    if after > 0:
        what = (
            f"提案とリファクタリング計画の枠（0.30·B = {window} 秒）の後に、バッファの見込み "
            f"{float(detail.get('reserve_minutes') or 0.0):.1f} 分と最短の項目 1 件 {float(detail.get('item_minutes') or 0.0):.1f} 分が"
        )
        floor = "下限では 1 件だけが入ります"
    else:
        what = f"提案とリファクタリング計画の枠（0.30·B = {window} 秒）が"
        floor = "下限では実装の時間が残りません"
    return (
        f"着手前のテストに {spent}かかり{later}、{what}想定最大時間 {b} 分に収まりません。\n"
        f"--budget-minutes を {required_budget_minutes(offset, after)} 以上にして {RESUME_GRACE_SECONDS // 60} 分以内に init を打ち直すと、"
        f"着手前のテストを走らせ直さずに続けます（{floor}。打ち直しが {RESUME_GRACE_SECONDS // 60} 分より"
        f" 1 分遅れるごとに、要る下限は約 {per_minute} 分増えます）"
    )


def limits_of(state: dict[str, Any]) -> dict[str, Any]:
    """書き出した表。無ければ（書き出す前の状態ファイル）その場で組む。"""
    return state.get("limits") or of_state(state)


def _limit_at(state: dict[str, Any], key: str) -> Optional[_dt.datetime]:
    """上限の表の時刻。書き出す前の状態ファイルはその場で組む（#1669 I9）。再開でずれた値もここから読む（#1743 決定 5）。"""
    value = limits_of(state).get(key)
    if value is None and state.get("plan") and state.get("started_at"):
        value = of_state(state).get(key)
    return clock.parse(value)


def stop_revert_end(state: dict[str, Any]) -> Optional[_dt.datetime]:
    """打ち切りの後の取り消し（案 A）の締め切り。"""
    return _limit_at(state, "stop_revert_end_at")


def fix_end_at(state: dict[str, Any]) -> Optional[_dt.datetime]:
    """直しの試行の打ち切り（`limits.fix_end_at`）。開始から計算し直さず、表の 1 か所から読む（決定 5）。"""
    return _limit_at(state, "fix_end_at")


def state_test_timeout(state: dict[str, Any]) -> int:
    """テスト 1 回の上限（秒）。"""
    return int(limits_of(state)["test_timeout"])


def state_whole_timeout(state: dict[str, Any]) -> int:
    """手元の全体テスト 1 回の上限（秒）。旧い表なら `test_timeout`。"""
    limits = limits_of(state)
    return int(limits.get("whole_timeout") or limits["test_timeout"])


def state_ci_wait_timeout(state: dict[str, Any]) -> int:
    """CI の待ちの上限（秒）。"""
    limits = limits_of(state)
    if limits.get("ci_wait_timeout") is not None:
        return int(limits["ci_wait_timeout"])
    return int(test_limits(state)["ci_wait_timeout"])


def phase_timeout(end: _dt.datetime, now: _dt.datetime, margin_seconds: int) -> int:
    """手順の監視の上限（秒）= 終わりの時刻までの残り + 余裕。終わりを過ぎていれば余裕だけ。"""
    return max(math.ceil((end - now).total_seconds()), 0) + int(margin_seconds)


def final_fix_timeout(
    end: _dt.datetime,
    now: _dt.datetime,
    margin_seconds: int,
    floor_seconds: Optional[int],
    first: bool,
) -> int:
    """最終ゲートの修正の監視の上限（秒）。

    **1 回目は、終わりまでの残りが予備時間（`final_fix_seconds`）より短くても予備時間の長さを渡す**
    （決定 26）。想定最大時間を使い切った後に落ちても、必ず 1 度は直しを試みる。
    2 回目からは他の手順と同じく残り + 余裕である。
    """
    left = max(math.ceil((end - now).total_seconds()), 0)
    if first:
        left = max(left, int(floor_seconds or 0))
    return left + int(margin_seconds)
