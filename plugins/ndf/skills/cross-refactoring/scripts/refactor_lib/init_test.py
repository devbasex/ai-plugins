"""`init` の着手前のテストの周り: 着手前のテストの所要の見込み・配分の履歴への追記・打ち切り（#1385 #1555 #2047・#2457）。

上限は予算から導かない（#2457 決定 11）。共通ライブラリの予算なしの式（`3·max(w, m)`、所要が無ければ既定の秒）を使う。

`commands/setup.py` から呼ぶ。`prep` は `setup._InitPreparation`（`strategy`・`work`・`repo` を持つもの）を受ける。
"""

from __future__ import annotations

import pathlib
from typing import Any, Optional

import run_metrics
import statefile
import test_strategy as ts
import whole_runs

from . import allocation, die, info
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


def extend_for_init_test(timeout: int) -> None:
    """着手前のテストを始める前に、その上限の分だけ親のステップの締め切りを延ばす（#2457 決定 8）。エンジンの外で打ったときは何もしない。"""
    from . import step_extend

    step_extend.ahead(timeout, f"着手前のテストの上限 {timeout} 秒")


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


def abort_timed_out(baseline: dict[str, Any], timeout: int) -> None:
    """着手前のテストの打ち切りで止める（ハングの検知。`SKIP=init-timeout`）。履歴に残したことを添える。"""
    statefile.emit(SKIP="init-timeout", SKIP_DETAIL=f"着手前のテストが上限 {timeout} 秒で終わらなかった")
    die(f"着手前のテストが {timeout} 秒で終わりませんでした（{baseline.get('command')}）。打ち切りました\n打ち切りを配分の履歴に残しました")


def budget_source(state: dict[str, Any]) -> str:
    """実装の予算の出所（`explicit` / `default`）。持たない状態ファイルは `explicit` として読む。"""
    return str(state.get("budget_source") or "explicit")
