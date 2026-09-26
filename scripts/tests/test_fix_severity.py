"""修正のまとめの重要度別の集計（scripts/measure/fix-severity.py・#1287 の受け入れ条件 8）。解析の関数だけを縛る。"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "fix_severity", Path(__file__).resolve().parents[1] / "measure" / "fix-severity.py")
fs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fs)

OLD = ("## 🔧 /ndf:fix サマリ | round 2 | commit abc\n\n対応件数: critical=0 / major=0 / minor=3（合計 3 件）\n"
       "決着: 3 件 / 見送り: 0 件 / 却下: 0 件\nCI: SUCCESS\n")
MIXED = ("## 🔧 /ndf:fix サマリ | commit def\n\n対応件数: critical=1 / major=1 / minor=0（合計 2 件）\n"
         "決着: 2 件 / 見送り: 2 件 / 却下: 0 件\n基準外の見送り: 2 件\nCI: NONE\n")


def test_old_and_new_summaries_are_read():
    assert fs.parse_summary(OLD) == {"critical": 0, "major": 0, "minor": 3, "waived": 0}
    assert fs.parse_summary(MIXED) == {"critical": 1, "major": 1, "minor": 0, "waived": 2}
    assert fs.parse_summary("ふつうのコメント critical=1 / major=0 / minor=0") is None


def test_aggregate_counts_ratio_minor_only_and_waived():
    res = fs.aggregate([(1, OLD), (2, MIXED), (2, "雑談")], [])
    assert res["summaries"] == 2 and res["prs"] == 2
    assert res["fixed_by_severity"] == {"critical": 1, "major": 1, "minor": 3}
    assert res["minor_ratio"] == 0.6 and res["minor_only_rounds"] == 1 and res["waived"] == 2
    assert res["verdict"] is False


def test_verdict_passes_under_the_thresholds():
    rows = [(n, MIXED) for n in range(20)] + [(99, OLD)]
    res = fs.aggregate(rows, [], max_minor_ratio=0.10, max_minor_only_per_pr=0.05)
    # minor 3 / 43 ≒ 0.07、minor だけの回 1 ≤ 0.05 × 21
    assert res["minor_ratio"] <= 0.10 and res["minor_only_rounds"] == 1 and res["verdict"] is True
    assert fs.aggregate(rows, [], max_minor_ratio=0.05)["verdict"] is False


def test_threads_measure_posted_minor_and_raised():
    threads = [["[minor / 整合性] ずれ", "対応しました。（abc）"],
               ["[nit / style] 末尾", "直しません。この指摘は…"],
               ["[major / 正確性] 空"],
               ["ラベルなし"]]
    res = fs.aggregate([], threads)
    assert res["posted_by_severity"] == {"critical": 0, "major": 1, "minor": 1, "nit": 1}
    assert res["posted_minor_ratio"] == round(2 / 3, 4) and res["raised_from_minor"] == 1
    assert res["minor_ratio"] == 0.0 and res["verdict"] is True


def test_collect_cuts_summaries_by_period():
    since = datetime(2026, 10, 1, tzinfo=timezone.utc)
    nodes = [{"number": 5, "comments": {"nodes": [{"body": OLD, "createdAt": "2026-09-30T23:59:59Z"},
                                                    {"body": MIXED, "createdAt": "2026-10-01T00:00:00Z"}]},
              "reviewThreads": {"nodes": [{"comments": {"nodes": [{"body": "[minor / x] y"}]}}]}}, {}]
    summaries, threads = fs.collect(nodes, since, None)
    assert summaries == [(5, MIXED)] and threads == [["[minor / x] y"]]
    assert fs.collect(nodes, since, datetime(2026, 10, 1, tzinfo=timezone.utc))[0] == []
