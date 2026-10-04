"""`init` の着手前のテストの周り: 範囲テストの所要の見込み・配分の履歴への追記・打ち切りと手順の枠での停止（#1385 #1555）。

`commands/setup.py` から呼ぶ。`prep` は `setup._InitPreparation`（`strategy`・`work`・`repo` を持つもの）を受ける。
"""

from __future__ import annotations

import pathlib
from typing import Any, Optional

import run_metrics
import statefile
import test_strategy as ts

from . import allocation, die, info, timeline
from . import baseline as baseline_lib


def history_file(repo: str) -> pathlib.Path:
    return allocation.history_path(run_metrics.metrics_dir(), repo)


def resolve_scope_seconds(prep: Any, scope: list[str], w: Optional[float]) -> tuple[Optional[float], Optional[str]]:
    """CI に任せる戦略の範囲テストの所要 s と出所（I7・決定 5）。履歴の同じ戦略・同じ置き場所の実測（`history`）→
    置き場所が全体テストの suite のパスを覆うときの w（`whole`）→ 無し。ほかの戦略では履歴を読まない。"""
    if not prep.strategy.whole_on_ci:
        return None, None
    locations = baseline_lib.locations_of(prep.strategy, scope, prep.work)
    if not locations:
        return None, None
    measured = allocation.scope_seconds(allocation.read_history(history_file(prep.repo)), prep.strategy.name, locations)
    if measured is not None:
        return measured, "history"
    if w is not None and ts.covers_whole(prep.strategy, locations):
        return float(w), "whole"
    return None, None


def append_init_test(prep: Any, budget_minutes: int, pr: Any, baseline: dict[str, Any]) -> None:
    """着手前のテストの行を履歴へ 1 行足す（E9）。打ち切りでも足す。書けなければ知らせて続ける。"""
    try:
        target = history_file(prep.repo)
        allocation.append_row(target, allocation.init_test_row(baseline, prep.strategy.name, budget_minutes, pr))
    except OSError as exc:
        info(f"⚠ 着手前のテストの所要を配分の履歴へ追記できませんでした（{exc}）。進行は止めません")


def abort_timed_out(strategy: ts.Strategy, budget_minutes: int, baseline: dict[str, Any], timeout: int) -> None:
    """着手前のテストの打ち切りで止める。履歴に残したことと、次の実行の上限の見込みを添える。"""
    lines = [f"着手前のテストが {timeout} 秒で終わりませんでした（{baseline.get('command')}）。打ち切りました"]
    if baseline.get("mode") == "scope" and strategy.whole_on_ci:
        following = ts.limits(strategy, budget_minutes, scope_seconds=float(timeout), scope_source="history")["init_test_timeout"]
        lines.append(f"打ち切りを配分の履歴に残しました。同じ範囲の次の実行の上限は {following} 秒の見込みです")
    else:
        lines.append("打ち切りを配分の履歴に残しました")
    die("\n".join(lines))


def stop_if_window_short(state_file: pathlib.Path, state: dict[str, Any]) -> None:
    """手順の枠が想定最大時間に収まらなければ、止めた印を残して保存し、終了コード 4 で止める。収まれば印を消す。"""
    problem = timeline.window_problem(state["limits"])
    if problem is None:
        if state.pop("window_stopped_at", None) is not None:
            statefile.save(state_file, state)
        return
    state["window_stopped_at"] = statefile.now()
    statefile.save(state_file, state)
    die(problem)
