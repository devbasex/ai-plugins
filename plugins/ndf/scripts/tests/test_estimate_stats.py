"""lib/estimate_stats.py: 集計の関数と、既存の 3 系統が集計を自分で持たないこと（#1856 の受け入れ条件 5・12）。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
PLUGIN = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(PLUGIN / "skills" / "cross-refactoring" / "scripts"))
import estimate_stats as st  # noqa: E402
import slow_step as ss  # noqa: E402


def test_recent_keeps_last_window_in_order():
    assert st.recent(list(range(15))) == list(range(5, 15))
    assert st.recent([1, 2, 3], 2) == [2, 3]
    assert st.recent([], 3) == []


def test_median_quantile_maximum():
    assert st.median_value([3, 1, 2]) == 2
    assert st.median_value([1, 2, 3, 4]) == 2.5
    assert st.median_value([]) is None
    # nearest-rank: ceil(q * n) 番目
    assert st.quantile([1, 2, 3, 4, 5], 0.8) == 4
    assert st.quantile([10], 0.8) == 10
    assert st.quantile([], 0.8) is None
    assert st.maximum([1, 7, 3]) == 7
    assert st.maximum([]) is None


def test_ratio_of_sums():
    assert st.ratio_of_sums([(2, 60), (1, 30)]) == 30
    assert st.ratio_of_sums([]) is None
    assert st.ratio_of_sums([(0, 10)]) is None


# --- 置き換えの前と同じ値（受け入れ条件 12）。期待値は置き換えの前の実装で出した値 ---


def test_slow_step_expected_for_keeps_values():
    cfg = ss.resolve_config(None, None, {"window": 3, "min_samples": 2, "factor": 2, "floor": 10})
    assert ss.expected_for([100, 1, 2, 9, 7], cfg) == (14.0, {"source": "history", "samples": 3, "factor": 2.0, "floor": 10.0, "median": 7.0})
    assert ss.expected_for([1, 2, 3], cfg)[1]["source"] == "floor"
    assert ss.expected_for([5], cfg)[1]["source"] == "default"


def test_slow_step_read_history_window(tmp_path):
    h = tmp_path / "h.jsonl"
    h.write_text("".join(json.dumps({"phase": "p", "step": "s", "seconds": i}) + "\n" for i in range(5)))
    assert ss.read_history(h, "p", "s", 2) == [3.0, 4.0]
    assert ss.read_history(h, "p", "s") == [0.0, 1.0, 2.0, 3.0, 4.0]


def test_measure_ci_ndf_record_keeps_values(tmp_path, monkeypatch):
    from project_lib import measure_ci

    monkeypatch.setattr(measure_ci.run_metrics, "metrics_dir", lambda: tmp_path)
    monkeypatch.setattr(measure_ci.repo_id, "slug", lambda repo: "o-r")
    f = tmp_path / "o-r" / measure_ci.RECORD_NAME
    f.parent.mkdir(parents=True)
    rows = [{"whole_test": {"init": v}} for v in [500, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]]
    rows.append({"kind": "init_test", "whole_test": {"init": 9999}})
    f.write_text("".join(json.dumps(r) + "\n" for r in rows))
    got = measure_ci.ndf_record("o/r")
    assert got["seconds"] == 5.5


def test_allocation_values_unchanged():
    from refactor_lib import allocation

    rows = [
        {"schema": 2, "kind": "init_test", "strategy": "ci", "mode": "scope", "locations": ["t"], "seconds": s}
        for s in [900, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    ]
    assert allocation.scope_seconds(rows, "ci", ["t"]) == 10


def test_allocation_per_unit_exact():
    from refactor_lib import allocation

    usable = [(c, s) for c, s in [(1, 600), (0, 50), (2, 600)]]
    assert allocation._per_unit(usable) == 1200 / 3 / 60
    many = [(1, 6000)] + [(1, 60)] * 10
    assert allocation._per_unit(many) == 1.0


# --- 3 系統が集計を自分で持たない（非機能の運用・保守性） ---

STATS_PATTERN = re.compile(r"statistics\.(median|quantiles)|\[-(WINDOW|RECORD_ROWS|cfg\.window|window) *:\]")


@pytest.mark.parametrize(
    "rel",
    [
        "scripts/lib/slow_step.py",
        "scripts/project_lib/measure_ci.py",
        "skills/cross-refactoring/scripts/refactor_lib/allocation.py",
    ],
)
def test_three_sources_do_not_aggregate_themselves(rel):
    assert not STATS_PATTERN.search((PLUGIN / rel).read_text(encoding="utf-8"))
