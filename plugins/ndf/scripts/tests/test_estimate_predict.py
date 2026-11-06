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


# 以下は errors の現状固定テスト。期待値の根拠は今の実装の出力で、仕様の主張ではない。


def _est(issue, eid="e", day=3, done="実装", **typical):
    return {
        "schema": 1,
        "id": eid,
        "at": f"2026-10-{day:02d}T00:00:00+00:00",
        "issue": issue,
        "done": done,
        "values": {k: {"typical": v} for k, v in typical.items()},
    }


def _ratio(items, key="A"):
    return [i["ratio"][key] for i in items]


def test_errors_pair_and_metrics_shape():
    rows: list[dict] = []
    _done(rows, 1, 200, cost=2.0, day=5)
    items, metrics = ep.errors([_est(1, "a", A=100, L=7200, C=0.5)], rows, alive=DEAD)
    assert items == [
        {
            "kind": "pair",
            "issue": 1,
            "estimate": "a",
            "predicted": {"A": 100, "L": 7200, "C": 0.5},
            "actual": {"A": 200.0, "L": 3600.0, "C": 2.0},
            "ratio": {"A": 2.0, "L": 0.5, "C": 4.0},
        }
    ]
    assert metrics == {"pairs": 1, "ratio_median_A": 2.0, "ratio_median_L": 0.5, "ratio_median_C": 4.0, "rank_correlation_A": None}


def test_errors_zero_pairs_metrics_are_none():
    assert ep.errors([], [], alive=DEAD)[1] == {
        "pairs": 0,
        "ratio_median_A": None,
        "ratio_median_L": None,
        "ratio_median_C": None,
        "rank_correlation_A": None,
    }


def test_errors_ratio_is_rounded_to_four_places():
    rows: list[dict] = []
    _done(rows, 1, 100, day=5)
    assert _ratio(ep.errors([_est(1, A=3)], rows, alive=DEAD)[0]) == [33.3333]


def test_errors_skips_estimates_without_integer_issue():
    rows: list[dict] = []
    _done(rows, 1, 100, day=5)
    ests = [{**_est(1, A=1), "issue": v} for v in (None, "1", 1.0)] + [{k: v for k, v in _est(1, A=1).items() if k != "issue"}]
    assert ep.errors(ests, rows, alive=DEAD)[0] == []


def test_errors_since_keeps_estimates_at_the_boundary():
    rows: list[dict] = []
    _done(rows, 1, 100, day=5)
    ests = [_est(1, "a", day=3, A=100)]
    assert [i["estimate"] for i in ep.errors(ests, rows, since=ests[0]["at"], alive=DEAD)[0]] == ["a"]
    assert ep.errors(ests, rows, since="2026-10-03T00:00:01+00:00", alive=DEAD)[0] == []


def test_errors_since_filters_before_picking_the_latest():
    rows: list[dict] = []
    _done(rows, 1, 100, day=5)
    ests = [_est(1, "old", day=2, A=1), _est(1, "new", day=4, A=1)]
    assert [i["estimate"] for i in ep.errors(ests, rows, since="2026-10-03T00:00:00+00:00", alive=DEAD)[0]] == ["new"]


@pytest.mark.parametrize("since", ["", "日時でない"])
def test_errors_since_without_a_time_filters_nothing(since):
    rows: list[dict] = []
    _done(rows, 1, 100, day=5)
    assert len(ep.errors([_est(1, A=1)], rows, since=since, alive=DEAD)[0]) == 1


def test_errors_since_drops_estimate_with_unreadable_at():
    rows: list[dict] = []
    _done(rows, 1, 100, day=5)
    ests = [{**_est(1, A=1), "at": "日時でない"}]
    assert ep.errors(ests, rows, since="2026-10-01T00:00:00+00:00", alive=DEAD)[0] == []
    assert len(ep.errors(ests, rows, alive=DEAD)[0]) == 1


@pytest.mark.parametrize(
    "ests,picked",
    [
        pytest.param([_est(1, "first", day=3, A=1), _est(1, "second", day=3, A=1)], "second", id="same-time-later-in-list"),
        pytest.param([_est(1, "new", day=4, A=1), _est(1, "old", day=2, A=1)], "new", id="older-later-in-list"),
        pytest.param([_est(1, "dated", day=4, A=1), {**_est(1, "bad", A=1), "at": "日時でない"}], "dated", id="unreadable-at-is-oldest"),
    ],
)
def test_errors_picks_latest_estimate_of_an_issue(ests, picked):
    rows: list[dict] = []
    _done(rows, 1, 100, day=5)
    assert [i["estimate"] for i in ep.errors(ests, rows, alive=DEAD)[0]] == [picked]


def test_errors_only_the_latest_estimate_is_paired():
    rows: list[dict] = []
    _done(rows, 1, 100, day=5)
    assert ep.errors([_est(1, "old", day=3, A=1), _est(1, "new", day=7, A=1)], rows, alive=DEAD)[0] == []


def test_errors_counts_only_runs_started_after_the_estimate():
    rows: list[dict] = []
    _done(rows, 1, 9999, day=3)  # 見積もりと同じ時刻に始まった実行は足さない
    _done(rows, 1, 200, day=5)
    assert ep.errors([_est(1, A=100)], rows, alive=DEAD)[0][0]["actual"]["A"] == 200.0


def test_errors_failed_run_is_not_a_pair():
    rows: list[dict] = []
    _done(rows, 1, 100, day=5)
    rows[-1]["outcome"] = "失敗"
    assert ep.errors([_est(1, A=1)], rows, alive=DEAD)[0] == []


def test_errors_passes_alive_to_the_actuals():
    rows: list[dict] = []
    _done(rows, 1, 100, day=5)
    rows.pop()
    seen: list[str] = []
    ep.errors([_est(1, A=1)], rows, alive=lambda run: seen.append(run.run) or False)
    assert len(seen) == 1


@pytest.mark.parametrize("done", [None, ""])
def test_errors_empty_done_means_the_implementation_phase(done):
    rows: list[dict] = []
    _done(rows, 1, 100, day=5, phase="設計")
    assert ep.errors([_est(1, done=done, A=1)], rows, alive=DEAD)[0] == []
    _done(rows, 1, 200, day=6, phase="実装")
    assert ep.errors([_est(1, done=done, A=1)], rows, alive=DEAD)[0][0]["actual"]["A"] == 300.0


def test_errors_done_decides_the_completing_phase():
    rows: list[dict] = []
    _done(rows, 1, 100, day=5, phase="実装")
    _done(rows, 1, 200, day=6, phase="検査")
    assert ep.errors([_est(1, done="検査", A=1)], rows, alive=DEAD)[0][0]["actual"]["A"] == 300.0
    assert ep.errors([_est(1, done="実装", A=1)], rows, alive=DEAD)[0][0]["actual"]["A"] == 100.0


@pytest.mark.parametrize(
    "values",
    [
        pytest.param({"A": {"typical": None}}, id="typical-none"),
        pytest.param({"A": {"typical": 0}}, id="typical-zero"),
        pytest.param({"A": {"typical": "100"}}, id="typical-text"),
        pytest.param({"A": {}}, id="typical-missing"),
        pytest.param({"A": None}, id="value-none"),
        pytest.param({}, id="value-missing"),
        pytest.param(None, id="values-none"),
    ],
)
def test_errors_ratio_is_none_without_a_usable_prediction(values):
    rows: list[dict] = []
    _done(rows, 1, 100, day=5)
    items, metrics = ep.errors([{**_est(1), "values": values}], rows, alive=DEAD)
    assert _ratio(items) == [None]
    assert metrics["ratio_median_A"] is None


def test_errors_bool_prediction_counts_as_a_number():
    # NOTE: 現状固定。bool も数として割る。意図した振る舞いかは確かめていない。
    rows: list[dict] = []
    _done(rows, 1, 100, day=5)
    assert _ratio(ep.errors([_est(1, A=True)], rows, alive=DEAD)[0]) == [100.0]


def test_errors_ratio_is_none_when_the_actual_lead_time_is_unknown():
    rows: list[dict] = []
    _done(rows, 1, 100, day=5)
    rows[0]["at"] = "日時でない"  # 始まりが読めないと L が出ない。見積もりの at も無ければ実行は除かれない
    est = {k: v for k, v in _est(1, A=100, L=100).items() if k != "at"}
    item = ep.errors([est], rows, alive=DEAD)[0][0]
    assert item["actual"]["L"] is None and item["ratio"]["L"] is None
    assert item["ratio"]["A"] == 1.0


def test_errors_items_are_sorted_by_issue():
    rows: list[dict] = []
    _done(rows, 2, 100, day=5)
    _done(rows, 1, 100, day=6)
    assert [i["issue"] for i in ep.errors([_est(2, A=1), _est(1, A=1)], rows, alive=DEAD)[0]] == [1, 2]


def test_errors_median_skips_pairs_without_a_ratio():
    rows: list[dict] = []
    for issue, a in ((1, 100), (2, 400), (3, 900)):
        _done(rows, issue, a, day=5)
    ests = [_est(1, A=100), _est(2, A=100), _est(3)]
    assert ep.errors(ests, rows, alive=DEAD)[1]["ratio_median_A"] == 2.5


def test_errors_median_keeps_a_zero_ratio():
    rows: list[dict] = []
    _done(rows, 1, 0, day=5)
    _done(rows, 2, 100, day=5)
    assert ep.errors([_est(1, A=100), _est(2, A=100)], rows, alive=DEAD)[1]["ratio_median_A"] == 0.5


@pytest.mark.parametrize(
    "pairs,expected",
    [
        pytest.param([(10, 100), (20, 200), (30, 300)], 1.0, id="same-order"),
        pytest.param([(10, 300), (20, 200), (30, 100)], -1.0, id="reverse-order"),
        pytest.param([(10, 100), (10, 200)], None, id="no-variance"),
        pytest.param([(0, 100), (10, 200)], 1.0, id="zero-prediction-still-ranked"),
        pytest.param([("x", 300), (10, 100), (20, 200)], 1.0, id="text-prediction-dropped"),
        pytest.param([("x", 100), (10, 200)], None, id="one-pair-left"),
    ],
)
def test_errors_rank_correlation(pairs, expected):
    rows: list[dict] = []
    ests = []
    for issue, (predicted, actual) in enumerate(pairs, start=1):
        _done(rows, issue, actual, day=5)
        ests.append(_est(issue, A=predicted))
    assert ep.errors(ests, rows, alive=DEAD)[1]["rank_correlation_A"] == expected


def test_no_multiplier_constants():
    src = (SCRIPTS / "lib" / "estimate_predict.py").read_text(encoding="utf-8")
    for word in ("multiplier", "speedup", "person_day", "人日"):
        assert word not in src
