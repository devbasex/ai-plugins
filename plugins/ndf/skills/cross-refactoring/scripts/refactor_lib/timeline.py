"""テストと CI の待ちと指標の測定の上限の表（#933 決定 23・24、#2457 決定 11）。

**時間の上限は予算から導かない**（#2457 の原則 1・I3）。テストと CI の待ちの上限は共通層の `test_strategy.limits` を
予算なし（`budget_minutes=None`）で呼んで出す（supervise と同じ `3·max(w, m)`・`3·c`・所要が無ければ既定の秒）。
上限はハングの検知にだけ使い、選定にも打ち切りにも使わない。手順の枠・締め切りの時刻は持たない。

値は `init` と `merge-plan` が `state["limits"]` へ書き出す。`state_*_timeout` は子プロセスを起動する直前に呼ばれるため、
その上限の分だけ親のステップの締め切りを延ばす（`step_extend.ahead`。#2457 決定 8）。
"""

from __future__ import annotations

from typing import Any

import limits as limit_decl  # 上限の宣言（#2343）。引数の limits と分ける
import test_strategy as ts

# テスト 1 回の上限の下限（秒）。着手前のテストがほぼ 0 秒で終わると `3·x` が 0 秒になり、以後のテストが走らない。
MIN_TEST_SECONDS = 60
# 指標の測定の上限（秒）。提案の工程の監視の上限の半分（提案の時間を測定が食い尽くさない。#1319 の決定 7）。
MEASURE_SHARE_OF_PROPOSE = 0.5

# 固定のまま残す値（決定 24）。OS の後始末と通信の待ちで、予算と性質が違う。報告に並べる。
FIXED_VALUES = (
    ("gitfacts.run_with_timeout の kill_grace", "5 秒", "打ち切ったプロセスグループへ SIGKILL を送るまでの待ち（テストと指標の測定）"),
    ("monitor.py の SIGTERM の猶予", "3 秒", "監視が止めた CLI へ SIGKILL を送るまでの待ち"),
    ("monitor.py の RESULT_AGE_GRACE", "30 秒", "結果ファイルを書き終えたとみなす経過"),
    ("monitor.py の見回りの間隔", "15 秒", "監視の 1 周期"),
    ("jev.py の PROBE_TIMEOUT / ASK_TIMEOUT", "10 秒 / 20 秒", "改修計画までの Jev の通信 1 回の待ち"),
    ("auth.py の AUTH_PROBE_TIMEOUT", "120 秒", "init の参加者の認証の確認 1 回の待ち"),
)


def measure_timeout() -> int:
    """指標の測定の上限（秒）。"""
    return int(float(limit_decl.seconds("monitor.phase.propose")) * MEASURE_SHARE_OF_PROPOSE)


def measure_deadline(limits: dict[str, Any]) -> int:
    """その実行で測定に使える秒（上限の表の `measure_timeout`。無ければ既定）。"""
    value = limits.get("measure_timeout")
    return int(value) if value is not None else measure_timeout()


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
    """テストと CI の待ちの上限（`test_strategy.limits` の予算なしの表）。入力は着手前の記録から取る。"""
    baseline = state.get("baseline_test") or {}
    return ts.limits(
        strategy_of(state),
        None,
        whole_seconds_value=baseline.get("whole_seconds"),
        whole_source=baseline.get("whole_source"),
        ci_seconds=baseline.get("ci_seconds"),
        measured_seconds=baseline.get("seconds"),
        recent_seconds=baseline.get("recent_seconds"),
        recent_source="recent" if baseline.get("recent_seconds") is not None else None,
    )


def compute(tests: dict[str, Any]) -> dict[str, Any]:
    """上限の表。`tests` は `test_strategy.limits` の表。"""

    def floor(key: str) -> int:
        return max(int(tests[key]), MIN_TEST_SECONDS)

    return {
        "init_test_timeout": floor("init_test_timeout"),
        "measure_timeout": measure_timeout(),
        "test_timeout": floor("test_timeout"),
        "whole_timeout": floor("whole_timeout"),
        "ci_wait_timeout": floor("ci_wait_timeout"),
        "basis": dict(tests.get("basis") or {}),
    }


def of_state(state: dict[str, Any]) -> dict[str, Any]:
    """状態の値から表を組む。"""
    return compute(test_limits(state))


def limits_of(state: dict[str, Any]) -> dict[str, Any]:
    """書き出した表。無ければ（書き出す前の状態ファイル）その場で組む。"""
    return state.get("limits") or of_state(state)


def state_test_timeout(state: dict[str, Any]) -> int:
    """テスト 1 回の上限（秒）。"""
    return _ahead(int(limits_of(state)["test_timeout"]), "テスト 1 回の上限")


def state_whole_timeout(state: dict[str, Any]) -> int:
    """手元の全体テスト 1 回の上限（秒）。旧い表なら `test_timeout`。"""
    limits = limits_of(state)
    return _ahead(int(limits.get("whole_timeout") or limits["test_timeout"]), "全体テスト 1 回の上限")


def state_ci_wait_timeout(state: dict[str, Any]) -> int:
    """CI の待ちの上限（秒）= `3·c`（c が無ければ既定の秒。#2457 決定 11）。"""
    limits = limits_of(state)
    wait = limits.get("ci_wait_timeout")
    return _ahead(int(wait) if wait is not None else int(test_limits(state)["ci_wait_timeout"]), "CI の待ちの上限")


def _ahead(seconds: int, what: str) -> int:
    from . import step_extend

    step_extend.ahead(seconds, f"{what} {seconds} 秒")
    return seconds
