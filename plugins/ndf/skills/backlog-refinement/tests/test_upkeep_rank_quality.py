"""品質の枠（upkeep_rank.py。#2089 の決定 14 の A・受け入れ条件 22）。GitHub を呼ばない。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS.parents[2] / "scripts" / "lib"))
sys.path.insert(0, str(SCRIPTS))
import upkeep_rank as R  # noqa: E402
from test_upkeep_rank import raw  # noqa: E402

NEAR = "01 近い"


def board(specs, labels=None, created=None, decl=None, **cfg_args):
    cfg = R.build_config(cfg_args, decl)
    ests = {}
    for s in specs:
        e, why = R.validate_estimate(s, cfg)
        assert e is not None, why
        ests[e.number] = e
    issues = {
        s["number"]: {"milestone": NEAR, "labels": (labels or {}).get(s["number"], []), "created_at": (created or {}).get(s["number"], "")}
        for s in specs
    }
    return R.Board(issues, ests, cfg, [NEAR])


def near(out):
    return next(m for m in out["milestones"] if m["title"] == NEAR)


# 高い順に #1〜#4（品質ではない）、低い #5・#6 が品質向上の Issue
SPECS = [
    raw(1, ubv=8, size=1),
    raw(2, ubv=5, size=1),
    raw(3, ubv=5, size=2),
    raw(4, ubv=3, size=1),
    raw(5, ubv=1, size=1),
    raw(6, ubv=1, size=1),
]
LABELS = {5: ["quality"], 6: ["quality"]}


def test_without_the_declaration_the_output_is_unchanged():
    plain = R.compute_ranking(board(SPECS, LABELS, capacity=3))
    m = near(plain)
    assert "quality_slot" not in m
    assert all("quality_slot" not in r for r in m["rows"])
    assert m["boundary"]["number"] == 2  # #1・#2 で Size 2、#3 は Size 2 で収まらない
    # 宣言の無い decl（ほかの項目だけ）でも同じ
    assert R.compute_ranking(board(SPECS, LABELS, decl={"candidate_limit": 5}, capacity=3)) == plain


def test_declared_slot_takes_quality_issues_first_and_narrows_the_boundary():
    out = R.compute_ranking(board(SPECS, LABELS, decl={"quality": {"ratio": 0.4}}, capacity=5))
    m = near(out)
    q = m["quality_slot"]
    assert q["issues"] == [5, 6] and q["budget"] == 2 and q["size_sum"] == 2
    assert [r["number"] for r in m["rows"] if r["quality_slot"]] == [5, 6]
    # 残りの容量 3 を今の順位で埋める: #1・#2（Size 2）、#3 は Size 2 で超える
    assert m["boundary"] == {"number": 2, "size_sum": 2, "capacity": 5, "over": False}


def test_minimum_takes_one_issue_even_when_the_ratio_rounds_to_zero():
    out = R.compute_ranking(board(SPECS, LABELS, decl={"quality": {}}, capacity=3))  # 3 × 0.2 = 0 件分
    q = near(out)["quality_slot"]
    assert q["budget"] == 0 and q["issues"] == [5]


def test_order_inside_the_slot_is_escaped_then_age_then_rank():
    specs = [raw(1, ubv=8), raw(5, ubv=3), raw(6, ubv=1), raw(7, ubv=1, observed_harm=True)]
    labels = {5: ["quality"], 6: ["quality"], 7: ["quality"]}
    created = {5: "2026-10-20T00:00:00Z", 6: "2026-10-01T00:00:00Z", 7: "2026-10-23T00:00:00Z"}
    out = R.compute_ranking(board(specs, labels, created, decl={"quality": {"ratio": 0.9, "min": 0}}, capacity=10))
    assert near(out)["quality_slot"]["issues"] == [7, 6, 5]


def test_same_input_gives_the_same_slot_and_boundary():
    decl = {"quality": {"ratio": 0.4}}
    a = R.compute_ranking(board(SPECS, LABELS, decl=decl, capacity=5))
    b = R.compute_ranking(board(list(reversed(SPECS)), LABELS, decl=decl, capacity=5))
    assert a == b


def test_without_capacity_the_slot_takes_the_minimum_count():
    out = R.compute_ranking(board(SPECS, LABELS, decl={"quality": {"min": 1}}))
    m = near(out)
    assert m["quality_slot"]["issues"] == [5] and m["boundary"] is None


def test_table_shows_the_slot_mark():
    out = R.compute_ranking(board(SPECS, LABELS, decl={"quality": {"ratio": 0.4}}, capacity=5))
    text = R.render_rank_table(near(out))
    assert "#5 #6" in text.splitlines()[-2]
    assert R.parse_rank_table("### 順位\n\n" + text + "\n")["rows"][0]["number"] == 1


@pytest.mark.parametrize("bad", [[], {"ratio": 1}, {"label": ""}, {"min": -1}])
def test_malformed_declaration_is_an_error(bad):
    with pytest.raises(R.RankError):
        R.build_config({}, {"quality": bad})


def test_slot_that_uses_up_the_capacity_leaves_no_normal_candidate():
    specs = [raw(1, ubv=8, size=1), raw(5, ubv=1, size=3)]
    m = near(R.compute_ranking(board(specs, {5: ["quality"]}, decl={"quality": {"min": 1}}, capacity=3)))
    assert m["quality_slot"]["issues"] == [5]
    assert m["boundary"]["empty"] is True and m["boundary"]["size_sum"] == 0  # #1 を容量を超えて足さない
    assert "通常の候補は 0 件" in R.render_rank_table(m)


def test_slot_skips_a_quality_issue_whose_open_dependency_is_not_in_the_slot():
    b = board(SPECS, LABELS, decl={"quality": {"ratio": 0.4}}, capacity=5)
    b.edges = {(5, 4)}  # #5 は品質ではない #4 に依存する
    assert near(R.compute_ranking(b))["quality_slot"]["issues"] == [6]
