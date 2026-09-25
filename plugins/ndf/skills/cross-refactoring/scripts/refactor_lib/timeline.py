"""時間の上限を想定最大時間から逆算する（#933 決定 23・24、実装計画 I15 I16）。

**時間に関わる数値は、想定最大時間 B（`--budget-minutes`）と着手前の全体のテストの
実測 w（`baseline_test.seconds`）からの算術だけで出す。** 係数はここにだけ置き、式は
`docs/02-plan-and-implement.md` の「締め切り」の節にまとめてある。実行の途中で数値を
決めるために LLM へ問わない。

値は `init`（計画の前の値）と `merge-plan`（計画の後の値）が `state["limits"]` へ書き
出す。以後の手順は、書き出した値と時計の比較だけで進み、止まる。

**純粋な処理だけを置く。** 今の時刻は引数で受ける。
"""
from __future__ import annotations

import datetime as _dt
import math
from typing import Any, Optional

from . import budget, clock

# 係数（決定 24）。B と w に掛ける比率で、秒や分の固定値は持たない。
PROPOSE_SHARE = 0.20      # 提案の枠の終わり = 開始 + 0.20·B
PLAN_SHARE = 0.10         # 計画の枠の終わり = 提案の枠の終わり + 0.10·B
INIT_TEST_SHARE = 0.10    # 着手前のテスト 1 回の上限 = 0.10·B（w はまだ測れていない）
MARGIN_SHARE = 0.05       # 余裕 = 0.05·B（手順の上限と CLI の上限に足す）
TEST_FACTOR = 3.0         # テスト 1 回の上限 = max(3·w, 0.01·B)
TEST_FLOOR_SHARE = 0.01

# 固定のまま残す値（決定 24）。OS の後始末と通信の待ちで、予算と性質が違う。報告に並べる。
FIXED_VALUES = (
    ("gitfacts.run_with_timeout の kill_grace", "5 秒", "打ち切ったプロセスグループへ SIGKILL を送るまでの待ち"),
    ("monitor.py の SIGTERM の猶予", "3 秒", "監視が止めた CLI へ SIGKILL を送るまでの待ち"),
    ("monitor.py の RESULT_AGE_GRACE", "30 秒", "結果ファイルを書き終えたとみなす経過"),
    ("monitor.py の見回りの間隔", "15 秒", "監視の 1 周期"),
    ("jev.py の PROBE_TIMEOUT / ASK_TIMEOUT", "10 秒 / 20 秒", "計画までの Jev の通信 1 回の待ち"),
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


def init_test_timeout(budget_minutes: int) -> int:
    """着手前の全体のテストとラウンドのテスト 1 回の上限（秒）。"""
    return _seconds(budget_minutes, INIT_TEST_SHARE)


def test_timeout(budget_minutes: int, baseline_seconds: Optional[float]) -> int:
    """着手の後のテスト 1 回の上限（秒）。測れていなければ着手前の上限を使う。"""
    if baseline_seconds is None:
        return init_test_timeout(budget_minutes)
    return math.ceil(max(TEST_FACTOR * float(baseline_seconds),
                         float(budget_minutes) * 60 * TEST_FLOOR_SHARE))


def _completion(items: list[dict[str, Any]], start_key: str, estimate_key: str) -> Optional[_dt.datetime]:
    """その手順の最後の項目の完了の締め切り（着手の締め切り + 見積り）。項目が無ければ `None`。"""
    ends = []
    for item in items:
        start = clock.parse(item.get(start_key))
        if start is None:
            continue
        minutes = float((item.get("estimate") or {}).get(estimate_key) or 0.0)
        ends.append(start + _dt.timedelta(minutes=minutes))
    return max(ends) if ends else None


def _iso(value: Optional[_dt.datetime]) -> Optional[str]:
    return clock.iso(value) if value is not None else None


def compute(
    started_at: _dt.datetime, budget_minutes: int, baseline_seconds: Optional[float],
    items: Optional[list[dict[str, Any]]] = None, reserve: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """実行時の値の表。`items` と `reserve`（計画の後）が無ければ、その行は `None`。"""
    b = int(budget_minutes)
    propose_end = started_at + _dt.timedelta(minutes=b * PROPOSE_SHARE)
    plan_end = propose_end + _dt.timedelta(minutes=b * PLAN_SHARE)
    planned = reserve is not None
    return {
        "budget_minutes": b,
        "margin_seconds": margin(b),
        "init_test_timeout": init_test_timeout(b),
        "test_timeout": test_timeout(b, baseline_seconds),
        "propose_end_at": _iso(propose_end),
        "plan_end_at": _iso(plan_end),
        "add_tests_end_at": _iso(_completion(list(items or []), "test_start_deadline", "test")),
        "implement_end_at": _iso(_completion(list(items or []), "start_deadline", "implement")),
        "fix_end_at": _iso(budget.fix_end(started_at, b, reserve)) if planned else None,
        "final_end_at": _iso(started_at + _dt.timedelta(minutes=b)),
        # 最終ゲートの修正の 1 回目に必ず渡す長さ（決定 26）。予備時間の `final_fix`。
        "final_fix_seconds": (math.ceil(float(reserve.get("final_fix") or 0.0) * 60)
                              if planned else None),
    }


def of_state(state: dict[str, Any]) -> dict[str, Any]:
    """状態の値から表を組む。計画の後なら項目と予備時間も使う。"""
    plan = state.get("plan") or None
    return compute(
        clock.parse(state["started_at"]), int(state["budget_minutes"]),
        (state.get("baseline_test") or {}).get("seconds"),
        state.get("items") if plan else None,
        (plan or {}).get("reserve") if plan else None,
    )


def limits_of(state: dict[str, Any]) -> dict[str, Any]:
    """書き出した表。無ければ（書き出す前の状態ファイル）その場で組む。"""
    return state.get("limits") or of_state(state)


def state_test_timeout(state: dict[str, Any]) -> int:
    """テスト 1 回の上限（秒）。"""
    return int(limits_of(state)["test_timeout"])


def phase_timeout(end: _dt.datetime, now: _dt.datetime, margin_seconds: int) -> int:
    """手順の監視の上限（秒）= 終わりの時刻までの残り + 余裕。終わりを過ぎていれば余裕だけ。"""
    return max(math.ceil((end - now).total_seconds()), 0) + int(margin_seconds)


def final_fix_timeout(
    end: _dt.datetime, now: _dt.datetime, margin_seconds: int,
    floor_seconds: Optional[int], first: bool,
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
