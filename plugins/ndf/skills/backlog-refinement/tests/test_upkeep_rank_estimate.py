"""棚卸しの順位へ見積もりの記録を重ねる（upkeep_rank_estimate.py。#1856 の受け入れ条件 6・I7）。GitHub を呼ばない。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS.parents[2] / "scripts" / "lib"))
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE))
import estimate_records as er  # noqa: E402
import upkeep_rank as R  # noqa: E402
import upkeep_rank_estimate as RE  # noqa: E402
from test_upkeep_rank import board, raw  # noqa: E402

ACT = {"mark": "実績", "kind": "通常/追加の調査"}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "r"
    r.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    monkeypatch.setenv("NDF_ESTIMATE_DIR", str(tmp_path / "rec"))
    (r / ".ndf").mkdir()
    (r / ".ndf" / "estimate.json").write_text('{"size_unit_seconds": 100}')
    return r


def _estimate(n, a, at="2026-10-01T00:00:00+00:00", basis=(7,)):
    vals = {
        "A": {**ACT, "typical": a, "high": a, "basis": [{"issue": b, "value": a} for b in basis]},
        "H": {"mark": "材料なし"},
        "L": {**ACT, "typical": 600, "high": 600, "basis": [{"issue": 7, "value": 600}]},
        "C": {"mark": "未校正"},
    }
    er.append_estimate(
        Path(os.environ["NDF_ESTIMATE_DIR"]),
        {"at": at, "id": f"e{n}{at}", "issue": n, "done": "実装", "items": {}, "config": {}, "values": vals},
    )


def test_overlay_rounds_a_and_keeps_other_values(repo):
    _estimate(1, 150, at="2026-09-01T00:00:00+00:00")
    _estimate(1, 400)  # 最新を使う
    _estimate(2, 5000)
    got = RE.estimate_overlay(repo, [1, 2, 3], (1, 2, 3, 5, 8, 13, 20))
    assert got[1]["size"] == 3 and got[1]["row"]["size_source"] == "見積もり"
    assert got[1]["row"]["size_basis"] == [7]
    assert got[1]["row"]["l"] == {"typical": 600, "mark": "実績"} and got[1]["row"]["h"]["mark"] == "材料なし"
    assert (got[2]["size"], got[2]["ratio"]) == ("尺度を超える", 50.0)
    assert 3 not in got


def test_rank_uses_overlay_and_keeps_over_scale_out_of_boundary(repo):
    _estimate(1, 400)
    _estimate(2, 5000)
    b = board([raw(1, size=13), raw(2, ubv=20, size=1), raw(3, size=2)], {1: "01 近い", 2: "01 近い", 3: "01 近い"}, capacity=30)
    b.sizes = RE.estimate_overlay(repo, list(b.issues), b.cfg.scale)
    b.estimates = RE.apply_overlay(b.estimates, b.sizes)
    out = R.compute_ranking(b)
    ms = out["milestones"][0]
    rows = {r["number"]: r for r in ms["rows"]}
    assert rows[1]["size"] == 3 and rows[1]["size_source"] == "見積もり"
    assert rows[2]["size"] == "尺度を超える"
    assert rows[3]["size"] == 2 and rows[3]["size_source"] == "付けた段階" and rows[3]["a"] is None
    assert ms["over_scale"] == [2]
    assert ms["boundary"]["number"] != 2 and ms["boundary"]["size_sum"] == 5
    table = R.render_rank_table(ms)
    assert "| A | H | L | C |" in table and "尺度を超える" in table and "10 分" in table


def test_value_of_over_scale_uses_unrounded_ratio(repo):
    _estimate(2, 5000)
    b = board([raw(2, ubv=20, size=1)], {2: "01 近い"})
    b.sizes = RE.estimate_overlay(repo, [2], b.cfg.scale)
    b.estimates = RE.apply_overlay(b.estimates, b.sizes)
    row = R.compute_ranking(b)["milestones"][0]["rows"][0]
    assert row["value"] == round(row["cod"] / (50.0**b.cfg.size_exponent), 9)


def test_no_records_keep_given_sizes(repo):
    b = board([raw(1, size=5)], {1: "01 近い"})
    assert RE.estimate_overlay(repo, [1], b.cfg.scale) == {}
    row = R.compute_ranking(b)["milestones"][0]["rows"][0]
    assert row["size"] == 5 and row["size_source"] == "付けた段階"


def test_boundary_skips_issues_that_depend_on_an_over_scale_issue(repo):
    _estimate(1, 5000)  # 尺度を超える前提の課題
    b = board([raw(1, size=1), raw(2, size=3), raw(3, size=2)], {1: "01 近い", 2: "01 近い", 3: "01 近い"}, deps=[(2, 1)], capacity=30)
    b.sizes = RE.estimate_overlay(repo, list(b.issues), b.cfg.scale)
    b.estimates = RE.apply_overlay(b.estimates, b.sizes)
    ms = R.compute_ranking(b)["milestones"][0]
    assert (ms["boundary"]["number"], ms["boundary"]["size_sum"]) == (3, 2)


def test_over_scale_row_survives_the_table_round_trip(repo):
    _estimate(2, 5000)
    b = board([raw(1, size=3), raw(2, size=1)], {1: "01 近い", 2: "01 近い"})
    b.sizes = RE.estimate_overlay(repo, list(b.issues), b.cfg.scale)
    b.estimates = RE.apply_overlay(b.estimates, b.sizes)
    ms = R.compute_ranking(b)["milestones"][0]
    parsed = R.parse_rank_table("### 順位\n\n" + R.render_rank_table(ms))
    rows = {r["number"]: r for r in parsed["rows"]}
    assert rows[2]["size"] == "尺度を超える" and rows[1]["size"] == 3
    restored = R.restore_from_row(rows[2], b.cfg)
    assert restored["size"]["step"] == b.cfg.scale[-1]
