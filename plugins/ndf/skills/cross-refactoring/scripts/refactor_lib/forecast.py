"""配分テーブルの読み出し（#933 決定 7）。

`merge-plan` が採る件数を決めるのに使う。履歴の根は `run_metrics.metrics_dir()` で決める（`allocation` は根を引数で受けるため、ここで決める）。
"""

from __future__ import annotations

from typing import Any

import run_metrics

from . import allocation, info


def allocation_table(state: dict[str, Any], notify: bool = True) -> dict[str, Any]:
    """配分テーブルを履歴から集計する（決定 7）。読めなければ初期値で、`notify` なら 1 行知らせる（AC20）。"""
    base = run_metrics.metrics_dir()
    rows = allocation.read_history(allocation.history_path(base, str(state["repo"])))
    table = allocation.build_table(rows, allocation.load_defaults())
    if notify and table.get("source") != "history":
        info("ℹ 配分の履歴が無いか読めないため、初期値（#917 の実測）で改修計画します")
    return table
