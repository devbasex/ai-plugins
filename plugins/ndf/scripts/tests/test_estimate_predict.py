"""lib/estimate_predict.py: 類似の実績の選択・構成で分けること・値の形と印・Size への丸め・上限の見込み・誤差（#1856）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import estimate_predict as ep  # noqa: E402

DEAD = lambda run: False  # noqa: E731
CFG = {"models": ["opus"], "harness": "claude-p", "review": None, "test": None, "parallel": None}


def _done(rows, issue, a, cost=1.0, config=None, day=1, phase="実装", issues=None):
    run = f"r{issue}-{day}-{len(rows)}"
    at = f"2026-10-{day:02d}T00:00:00+00:00"
    rows.append(
        {
            "schema": 1,
            "kind": "run_start",
            "at": at,
            "run": run,
            "plan": "/p.json",
            "issues": issues or [issue],
            "phase": phase,
            "from": None,
            "attempt": 1,
            "imported": False,
        }
    )
    rows.append(
        {
            "schema": 1,
            "kind": "step",
            "at": at,
            "run": run,
            "step": "impl",
            "type": "work",
            "exit": 0,
            "seconds": a,
            "cost_usd": cost,
            "retry": 0,
        }
    )
    rows.append(
        {
            "schema": 1,
            "kind": "run_end",
            "at": f"2026-10-{day:02d}T01:00:00+00:00",
            "run": run,
            "outcome": "成功",
            "wall_seconds": 3600,
            "config": config or CFG,
        }
    )


def _req(**kw):
    base = {"issue": None, "task": "x", "done": "実装", "items": {}, "like": None, "provisional": {}}
    return ep.Request(**{**base, **kw})


def test_no_records_gives_uncalibrated_or_no_material():
    got = ep.predict(_req(), [], [], {}, {})
    assert set(got["values"]) == {"A", "H", "L", "C"}
    assert {v["mark"] for v in got["values"].values()} <= {"未校正", "材料なし"}
    assert all(v["typical"] is None for v in got["values"].values())
    assert got["size"] is None


def test_provisional_is_returned_as_uncalibrated():
    got = ep.predict(_req(provisional={"A": (1800, 5400)}), [], [], {}, {})
    a = got["values"]["A"]
    assert (a["mark"], a["typical"], a["high"]) == ("未校正", 1800, 5400)


def test_like_with_few_samples_uses_median_and_max():
    rows: list[dict] = []
    for i, a in enumerate([100, 300, 200]):
        _done(rows, 10 + i, a, day=i + 1)
    got = ep.predict(_req(like=[10, 11, 12]), rows, [], {}, {}, alive=DEAD)
    a = got["values"]["A"]
    assert (a["mark"], a["kind"], a["typical"], a["high"]) == ("実績", "通常/追加の調査", 200, 300)
    assert sorted(b["issue"] for b in a["basis"]) == [10, 11, 12]
    assert all(b["value"] is not None for b in a["basis"])
    assert got["values"]["H"]["mark"] == "材料なし"
    assert got["values"]["L"]["typical"] == 3600


def test_many_samples_use_p50_p80():
    rows: list[dict] = []
    for i in range(5):
        _done(rows, 20 + i, 100 * (i + 1), day=i + 1)
    a = ep.predict(_req(like=list(range(20, 25))), rows, [], {}, {}, alive=DEAD)["values"]["A"]
    assert (a["kind"], a["typical"], a["high"]) == ("P50/P80", 300, 400)


def test_other_config_is_excluded():
    rows: list[dict] = []
    _done(rows, 1, 100, day=1)
    _done(rows, 2, 9999, day=2, config={**CFG, "models": ["sonnet"]})
    _done(rows, 3, 300, day=3)  # 最新の構成は CFG
    got = ep.predict(_req(like=[1, 2]), rows, [], {}, {}, alive=DEAD)
    assert got["values"]["A"]["typical"] == 100
    assert got["other_config"] == [{"issue": 2, "keys": ["models"]}]


def test_unknown_config_keys_are_not_compared():
    rows: list[dict] = []
    _done(rows, 1, 100, day=1, config={})
    _done(rows, 2, 300, day=2)
    got = ep.predict(_req(like=[1]), rows, [], {}, {}, alive=DEAD)
    assert got["values"]["A"]["typical"] == 100
    assert "構成の一部が不明" in got["values"]["A"]["note"]


def test_items_select_by_at_most_one_difference():
    rows: list[dict] = []
    ests = []
    for i, items in enumerate(
        [{"spread": "軽", "rework": "軽"}, {"spread": "重", "rework": "軽"}, {"spread": "重", "rework": "重", "information": "重"}]
    ):
        _done(rows, 30 + i, 100 * (i + 1), day=i + 1)
        ests.append({"schema": 1, "id": f"e{i}", "at": f"2026-09-0{i + 1}T00:00:00+00:00", "issue": 30 + i, "done": "実装", "items": items})
    got = ep.predict(_req(items={"spread": "軽", "rework": "軽"}), rows, ests, {}, {}, alive=DEAD)
    assert sorted(b["issue"] for b in got["values"]["A"]["basis"]) == [30, 31]


def test_shared_runs_are_marked():
    rows: list[dict] = []
    _done(rows, 1, 600, day=1, issues=[1, 2])
    a = ep.predict(_req(like=[1]), rows, [], {}, {}, alive=DEAD)["values"]["A"]
    assert a["typical"] == 300 and a["basis"][0]["shared"] is True


def test_cost_unknown_note():
    rows: list[dict] = []
    _done(rows, 1, 600, cost=None, day=1)
    c = ep.predict(_req(like=[1]), rows, [], {}, {}, alive=DEAD)["values"]["C"]
    assert "費用の取れない実行を含む" in c["note"]


def test_human_rows_feed_h():
    rows: list[dict] = []
    _done(rows, 1, 600, day=1)
    rows.append({"schema": 1, "kind": "human", "at": "2026-10-01T00:00:00+00:00", "issues": [1], "seconds": 300})
    h = ep.predict(_req(like=[1]), rows, [], {}, {}, alive=DEAD)["values"]["H"]
    assert (h["mark"], h["typical"]) == ("実績", 300)


# --- Size（I7） ---


@pytest.mark.parametrize(
    "ratio,size",
    [(0.2, 1), (1.5, 1), (1.6, 2), (2.5, 2), (2.6, 3), (4, 3), (4.1, 5), (6.5, 5), (10.5, 8), (16.5, 13), (20, 20), (20.1, "尺度を超える")],
)
def test_to_size_midpoints(ratio, size):
    assert ep.to_size(ratio * 100, 100) == size


def test_size_unit_from_decl_or_smallest_quarter():
    assert ep.size_unit([], {"size_unit_seconds": 50}) == 50
    assert ep.size_unit([100, 200, 300], {}) is None
    assert ep.size_unit([800, 100, 200, 300, 400, 500, 600, 700], {}) == 150


def test_predict_size_uses_unit():
    rows: list[dict] = []
    for i in range(4):
        _done(rows, 40 + i, 100 * (i + 1), day=i + 1)
    got = ep.predict(_req(like=[43]), rows, [], {}, {}, alive=DEAD)
    assert got["size"] == 3  # 400 ÷ 基準 100 = 4


def test_size_over_scale():
    got = ep.predict(_req(provisional={"A": (9000, 9000)}), [], [], {"size_unit_seconds": 100}, {})
    assert got["size"] is None  # 未校正の A は丸めない
    rows: list[dict] = []
    _done(rows, 1, 5000, day=1)
    assert ep.predict(_req(like=[1]), rows, [], {"size_unit_seconds": 100}, {}, alive=DEAD)["size"] == "尺度を超える"


# --- 実装の上限 ---


def test_impl_limit_needs_three_samples_and_uses_max():
    rows: list[dict] = []
    assert ep.impl_limit(rows)["source"] == "uncalibrated"
    for d, a in enumerate([1000, 4000, 2000]):
        _done(rows, 1, a, day=d + 1)
    got = ep.impl_limit(rows)
    assert (got["source"], got["samples"], got["high"]) == ("history", 3, 4000)
    assert len(got["basis"]) == 3


def test_impl_limit_includes_failures_and_skips_other_config():
    rows: list[dict] = []
    for d, a in enumerate([1000, 2000]):
        _done(rows, 1, a, day=d + 1)
    _done(rows, 2, 99999, day=3, config={**CFG, "harness": "codex"})
    rows.append(
        {
            "schema": 1,
            "kind": "run_start",
            "at": "2026-10-04T00:00:00+00:00",
            "run": "f",
            "plan": "/p.json",
            "issues": [3],
            "phase": "実装",
            "from": None,
            "attempt": 1,
            "imported": False,
        }
    )
    rows.append(
        {
            "schema": 1,
            "kind": "step",
            "at": "2026-10-04T00:00:00+00:00",
            "run": "f",
            "step": "impl",
            "type": "work",
            "exit": 1,
            "seconds": 3000,
            "retry": 0,
        }
    )
    _done(rows, 4, 500, day=5)
    got = ep.impl_limit(rows)
    assert (got["samples"], got["high"]) == (4, 3000)


# --- 誤差 ---


def test_errors_pairs_after_estimate_only():
    rows: list[dict] = []
    _done(rows, 1, 9999, day=1)  # 見積もりの前
    _done(rows, 1, 200, day=5)
    _done(rows, 2, 400, day=6)
    ests = [
        {
            "schema": 1,
            "id": "a",
            "at": "2026-10-03T00:00:00+00:00",
            "issue": 1,
            "done": "実装",
            "values": {"A": {"typical": 100}, "L": {"typical": 3600}, "C": {"typical": 1}},
        },
        {
            "schema": 1,
            "id": "b",
            "at": "2026-10-03T00:00:00+00:00",
            "issue": 2,
            "done": "実装",
            "values": {"A": {"typical": 300}, "L": {}, "C": {}},
        },
        {"schema": 1, "id": "c", "at": "2026-10-03T00:00:00+00:00", "task": "x", "done": "実装", "values": {"A": {"typical": 1}}},
    ]
    items, metrics = ep.errors(ests, rows, alive=DEAD)
    assert metrics["pairs"] == 2
    one = next(i for i in items if i["issue"] == 1)
    assert one["actual"]["A"] == 200 and one["ratio"]["A"] == 2
    assert metrics["rank_correlation_A"] == 1.0


def test_errors_zero_pairs():
    items, metrics = ep.errors([], [], alive=DEAD)
    assert items == [] and metrics["pairs"] == 0


def test_no_multiplier_constants():
    src = (SCRIPTS / "lib" / "estimate_predict.py").read_text(encoding="utf-8")
    for word in ("multiplier", "speedup", "person_day", "人日"):
        assert word not in src
