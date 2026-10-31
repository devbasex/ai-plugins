"""`init` の着手前のテストの周り: 着手前のテストの所要の見込み・配分の履歴への追記・打ち切りと手順の枠での停止か想定最大時間の引き上げ（#1385 #1555 #2047）。

`commands/setup.py` から呼ぶ。`prep` は `setup._InitPreparation`（`strategy`・`work`・`repo` を持つもの）を受ける。
"""

from __future__ import annotations

import math
import pathlib
from typing import Any, Optional

import drive_pause
import run_metrics
import statefile
import test_strategy as ts
import whole_runs

from . import allocation, die, info, timeline
from . import baseline as baseline_lib


def history_file(repo: str) -> pathlib.Path:
    return allocation.history_path(run_metrics.metrics_dir(), repo)


def resolve_scope_seconds(
    prep: Any, scope: list[str], w: Optional[float], m: Optional[float] = None
) -> tuple[Optional[float], Optional[str]]:
    """着手前のテストの所要の見込み s と出所（#1555 の I7・#2047 の I1）。CI に任せる戦略だけ、履歴の同じ戦略・同じ置き場所の
    実測（`history`）を先に採る。無ければ手元の全体テストの直近の実測 m（`recent`）→ 宣言の w（`whole`）→ 無し。
    範囲テストは全体テストの部分なので、全体テストの所要は上限の見込みとして上側に倒れる。"""
    if prep.strategy.whole_on_ci:
        locations = baseline_lib.locations_of(prep.strategy, scope, prep.work)
        if locations:
            measured = allocation.scope_seconds(allocation.read_history(history_file(prep.repo)), prep.strategy.name, locations)
            if measured is not None:
                return measured, "history"
    if m is not None:
        return float(m), "recent"
    if w is not None:
        return float(w), "whole"
    return None, None


def recent_whole_seconds(prep: Any) -> Optional[float]:
    """手元の全体テストの直近の実測 m（`whole_runs`）。無ければ None。"""
    got = whole_runs.recent_whole(prep.work, ts.whole_key(prep.strategy))
    return float(got[0]) if got else None


def extend_for_init_test(budget_minutes: int, timeout: int) -> None:
    """着手前のテストの上限 T が `0.10·B` を超えるなら、テストを始める前にステップの延長を書く（#2047 の決定 4）。
    見込みは `B·60 + (T − 0.10·B·60)`。エンジンの外で打ったときは何もしない。"""
    base = math.ceil(float(budget_minutes) * 60 * ts.INIT_TEST_SHARE)
    if timeout <= base:
        return
    seconds = int(budget_minutes) * 60 + (int(timeout) - base)
    drive_pause.write_extension(seconds, f"着手前のテストの上限 {timeout} 秒が想定最大時間の 10%（{base} 秒）を超える")


def append_init_test(prep: Any, budget_minutes: int, pr: Any, baseline: dict[str, Any]) -> None:
    """着手前のテストの行を履歴へ 1 行足す（E9）。打ち切りでも足す。書けなければ知らせて続ける。"""
    try:
        target = history_file(prep.repo)
        allocation.append_row(target, allocation.init_test_row(baseline, prep.strategy.name, budget_minutes, pr))
    except OSError as exc:
        info(f"⚠ 着手前のテストの所要を配分の履歴へ追記できませんでした（{exc}）。進行は止めません")
    record_whole_run(prep, baseline)


def record_whole_run(prep: Any, baseline: dict[str, Any]) -> None:
    """手元で全 suite を走らせた着手前のテスト（`mode: whole`）が上限の中で終わったら、全体テストの実測として残す（#1631 の決定 4）。
    範囲テスト・ラウンドテスト・打ち切りは残さない（起動の失敗はここへ来る前に止まる）。書けなくても止めない（`whole_runs.record_whole` が知らせる）。"""
    if baseline.get("mode") != "whole" or baseline.get("timed_out"):
        return
    whole_runs.record_whole(prep.work, ts.whole_key(prep.strategy), baseline.get("seconds"), "cross-refactoring")


def abort_timed_out(strategy: ts.Strategy, budget_minutes: int, baseline: dict[str, Any], timeout: int) -> None:
    """着手前のテストの打ち切りで止める。時間の枠の不足（`SKIP=init-timeout`）を出し、履歴に残したことと、次の実行の上限の見込みを添える。"""
    statefile.emit(SKIP="init-timeout", SKIP_DETAIL=f"着手前のテストが上限 {timeout} 秒で終わらなかった")
    lines = [f"着手前のテストが {timeout} 秒で終わりませんでした（{baseline.get('command')}）。打ち切りました"]
    if baseline.get("mode") == "scope" and strategy.whole_on_ci:
        following = ts.limits(strategy, budget_minutes, scope_seconds=float(timeout), scope_source="history")["init_test_timeout"]
        lines.append(f"打ち切りを配分の履歴に残しました。同じ範囲の次の実行の上限は {following} 秒の見込みです")
    else:
        lines.append("打ち切りを配分の履歴に残しました")
    die("\n".join(lines))


def budget_source(state: dict[str, Any]) -> str:
    """想定最大時間の出所。持たない状態ファイル（この変更より前の実行）は `explicit`（止まる側）として読む。"""
    return str(state.get("budget_source") or "explicit")


def settle_window(state_file: pathlib.Path, state: dict[str, Any]) -> None:
    """手順の枠が想定最大時間に収まるかを確かめ、出所に応じて上げるか止める（#2047 の I3・I4）。

    収まれば止めた印を消す。出所が `default` / `floor` なら想定最大時間を下限（打ち直しの見込みを足さない）へ上げ、
    出所を `floor` にして上限の表を組み直す。`explicit` なら `SKIP=window` を出し、止めた印を残して保存し、終了コード 4 で止める。"""
    problem = timeline.window_problem(state["limits"])
    if problem is None:
        if state.pop("window_stopped_at", None) is not None:
            statefile.save(state_file, state)
        return
    offset = timeline.window_offset(state)
    after = float(state["limits"].get("after_plan_minutes") or 0.0)
    floor = timeline.required_budget_minutes(offset, after, grace_seconds=0)
    before = int(state["budget_minutes"])
    x = (state.get("baseline_test") or {}).get("seconds")
    spent = f"着手前のテスト {x} 秒" if x is not None else f"枠の起点 {math.ceil(offset)} 秒"
    if budget_source(state) == "explicit":
        statefile.emit(SKIP="window", SKIP_DETAIL=f"要る下限 {floor} 分 > 想定最大時間 {before} 分（{spent}）")
        state["window_stopped_at"] = statefile.now()
        statefile.save(state_file, state)
        die(problem)
        return
    minutes = max(floor, before)
    state["budget_minutes"] = minutes
    state["limits"] = timeline.of_state(state)
    while timeline.window_problem(state["limits"]) is not None:  # 時刻の丸めで 1 分届かないときだけ回る
        minutes += 1
        state["budget_minutes"] = minutes
        state["limits"] = timeline.of_state(state)
    state["budget_source"] = "floor"
    state["budget_floor"] = {
        "from_minutes": before,
        "minutes": minutes,
        "offset_seconds": round(offset, 1),
        "after_plan_minutes": after,
        "at": statefile.now(),
    }
    state.pop("window_stopped_at", None)
    statefile.save(state_file, state)
    info(
        f"ℹ 想定最大時間を省いたため、下限の {minutes} 分へ上げました（{before} 分では提案とリファクタリング計画の枠と"
        f"計画の後の見込み {after} 分が収まらない。{spent}）"
    )
