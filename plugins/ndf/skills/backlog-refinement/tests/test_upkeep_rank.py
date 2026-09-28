"""順位付けの算出（upkeep_rank.py。#1429 の AC1〜AC8・AC13・AC17〜AC21、I1〜I4・I8〜I10）。GitHub を呼ばない。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"

import sys  # noqa: E402

sys.path.insert(0, str(SCRIPTS.parents[2] / "scripts" / "lib"))
sys.path.insert(0, str(SCRIPTS))
import upkeep_rank as R  # noqa: E402

BANDS = {1: "なし", 2: "表示", 3: "表示", 5: "機能", 8: "機能", 13: "全体", 20: "全体"}


def raw(n, ubv=3, tc=1, rr=1, size=1, impact=None, **kw):
    return {
        "number": n,
        "digest": f"d{n}",
        "ubv": {"step": ubv, "why": "根拠", "impact": impact or BANDS[ubv]},
        "tc": {"step": tc, "why": "根拠"},
        "rr_oe": {"step": rr, "why": "根拠"},
        "size": {"step": size, "why": "閉じた課題の実績と比べた"},
        **kw,
    }


def board(specs, placement, order=("01 近い", "02 次", "03 後"), deps=(), subs=None, labels=None, **cfg_args):
    cfg = R.build_config(cfg_args, None)
    ests = {}
    for s in specs:
        e, why = R.validate_estimate(s, cfg)
        assert e is not None, why
        ests[e.number] = e
    issues = {n: {"milestone": m, "labels": (labels or {}).get(n, [])} for n, m in placement.items()}
    edges = R.merge_edges([R.sub_issue_edges(subs or {}), list(deps)], set(issues))
    return R.Board(issues, ests, cfg, list(order), subs or {}, edges)


def ranks(out, title="01 近い"):
    return [r["number"] for m in out["milestones"] if m["title"] == title for r in m["rows"]]


# ---------------- AC1・I4 ----------------


def test_same_input_gives_same_order_and_ties_break_by_cod_then_number():
    specs = [raw(1, ubv=5, tc=5, size=2), raw(2, ubv=3, tc=2, size=1), raw(3, ubv=3, tc=2, size=1), raw(4, ubv=2, tc=2, size=1)]
    place = {n: "01 近い" for n in (1, 2, 3, 4)}
    a = R.compute_ranking(board(specs, place, size_exponent=1))
    b = R.compute_ranking(board(list(reversed(specs)), place, size_exponent=1))
    assert a == b
    # #1 は CoD 11・Size 2 で 5.5、#2 と #3 は CoD 6・Size 1 で 6.0 の同点 → 番号の昇順
    assert ranks(a) == [2, 3, 1, 4]


def test_equal_value_puts_higher_cod_first():
    specs = [raw(1, ubv=3, tc=1, rr=1, size=1), raw(2, ubv=5, tc=3, rr=2, size=2)]  # 5.0 と 5.0
    out = R.compute_ranking(board(specs, {1: "01 近い", 2: "01 近い"}, size_exponent=1))
    assert ranks(out) == [2, 1]


# ---------------- AC2・I1・AC21・I10 ----------------


@pytest.mark.parametrize(
    "spec, why",
    [
        (raw(1, tc=4), "尺度に無い"),
        ({**raw(1), "tc": {"step": 2, "why": " "}}, "根拠の 1 行が空"),
        ({k: v for k, v in raw(1).items() if k != "size"}, "Size の段階が無い"),
        (raw(1, ubv=8, impact="全体"), "帯 13〜20 の外"),
        (raw(1, ubv=5, impact="表示"), "帯 2〜3 の外"),
        (raw(1, impact="重大"), "実害の目安に無い"),
        (raw(1, mitigation="ある"), "退避策の状態"),
        (raw(1, ubv=8, impact="全体", mitigation="部分的"), "帯 13〜20 の外"),
        (raw(1, ubv=8, impact="全体", mitigation="無い"), "帯 13〜20 の外"),
        (raw(1, ubv=3, impact="全体", mitigation="防いでいる"), "帯 13〜20 の外"),
        (raw(1, ubv=20, impact="機能", mitigation="防いでいる"), "帯 5〜8 の外"),
    ],
)
def test_invalid_estimates_are_rejected_with_a_reason(spec, why):
    est, reason = R.validate_estimate(spec, R.build_config({}, None))
    assert est is None and why in reason


@pytest.mark.parametrize("spec", [raw(1, ubv=13, impact="全体"), raw(1, ubv=20, impact="全体"), raw(1, ubv=3, impact="表示")])
def test_estimates_inside_the_band_are_accepted(spec):
    assert R.validate_estimate(spec, R.build_config({}, None))[0] is not None


def test_prevented_mitigation_lowers_the_band_by_one_only():
    cfg = R.build_config({}, None)
    assert R.validate_estimate(raw(1, ubv=8, impact="全体", mitigation="防いでいる"), cfg)[0] is not None
    assert R.validate_estimate(raw(1, ubv=1, impact="なし", mitigation="防いでいる"), cfg)[0] is not None
    assert R.validate_estimate(raw(1, ubv=1, impact="表示", mitigation="防いでいる"), cfg)[0] is not None
    assert R.validate_estimate(raw(1, ubv=1, impact="機能", mitigation="防いでいる"), cfg)[0] is None


def test_declared_harm_guide_wins_and_empty_band_is_an_error():
    cfg = R.build_config({}, {"harm_guide": {"重い": [8, 20], "軽い": [1, 5]}})
    assert R.validate_estimate(raw(1, ubv=8, impact="重い"), cfg)[0] is not None
    assert R.validate_estimate(raw(1, ubv=8, impact="全体"), cfg)[0] is None
    with pytest.raises(R.RankError):
        R.build_config({}, {"harm_guide": {"全体": [14, 19]}})
    with pytest.raises(R.RankError):  # 宣言の尺度に既定の帯の値が無い
        R.build_config({"scale": "1,4,6,7", "auto_threshold": 6, "large_min_size": 6}, None)


# ---------------- AC3・I3 ----------------


def test_dependency_comes_first_even_with_a_lower_value():
    specs = [raw(1, ubv=13, tc=8, rr=8), raw(2, ubv=1, tc=1, rr=1)]
    out = R.compute_ranking(board(specs, {1: "01 近い", 2: "01 近い"}, deps=[(1, 2)]))
    assert ranks(out) == [2, 1]


def test_cycle_stops_with_the_numbers_on_the_cycle():
    specs = [raw(1), raw(2), raw(3), raw(4)]
    with pytest.raises(R.CycleError) as e:
        R.compute_ranking(board(specs, {n: "01 近い" for n in (1, 2, 3, 4)}, deps=[(1, 2), (2, 3), (3, 1), (4, 1)]))
    assert e.value.numbers == [1, 2, 3]


def test_lower_source_edge_against_a_higher_source_is_dropped():
    edges = R.merge_edges([[(1, 2)], [(2, 1), (3, 1)]], {1, 2, 3})
    assert edges == {(1, 2), (3, 1)}


def test_parallel_group_dependency_column_gives_edges():
    desc = "主題\n\n### 並列の組（見込み）\n\n| 組 | 課題 | 触る場所の見込み | 依存 |\n| --- | --- | --- | --- |\n"
    desc += "| 1 | #10 #11 | a | なし |\n| 2 | #12 | b | 1 |\n| 3 | #13 | c | 1（#10 の後） |\n"
    assert sorted(R.parallel_group_edges(desc)) == [(12, 10), (12, 11), (13, 10)]


# ---------------- AC4・AC19・I2 ----------------


@pytest.mark.parametrize("count, step", [(0, 1), (1, 5), (2, 8), (3, 13), (4, 13), (5, 20), (9, 20)])
def test_released_count_maps_to_a_step(count, step):
    assert R.build_config({}, None).dependents_step(count) == step


def test_dependents_step_raises_rr_and_the_source_is_shown():
    specs = [raw(1, rr=2), raw(2), raw(3), raw(4, rr=13)]
    place = {n: "01 近い" for n in (1, 2, 3, 4)}
    out = R.compute_ranking(board(specs, place, deps=[(2, 1), (3, 1), (2, 4)], subs={}))
    rows = {r["number"]: r for r in out["milestones"][0]["rows"]}
    assert rows[1]["rr_oe"] == 8 and rows[1]["sources"]["rr_oe"] == "dependents" and rows[1]["released"] == 2
    assert rows[4]["rr_oe"] == 13 and rows[4]["sources"]["rr_oe"] == "llm"  # 対応表は下げない


def test_sub_issue_children_count_as_released():
    specs = [raw(1), raw(2), raw(3)]
    out = R.compute_ranking(board(specs, {1: "01 近い", 2: "01 近い", 3: "01 近い"}, subs={1: [2, 3]}))
    row = next(r for r in out["milestones"][0]["rows"] if r["number"] == 1)
    assert row["released"] == 2 and row["rr_oe"] == 8
    assert ranks(out)[0] == 1  # 子は親に依存する


def test_label_floor_raises_ubv_only_when_declared():
    specs = [raw(1, ubv=3)]
    out = R.compute_ranking(board(specs, {1: "01 近い"}, labels={1: ["priority: high"]}))
    assert out["milestones"][0]["rows"][0]["ubv"] == 3
    out = R.compute_ranking(board(specs, {1: "01 近い"}, labels={1: ["priority: high"]}, label_floor={"priority: high": 8}))
    row = out["milestones"][0]["rows"][0]
    assert row["ubv"] == 8 and row["sources"]["ubv"] == "label"


# ---------------- AC5・AC18・I9 ----------------


def test_boundary_needs_capacity():
    specs = [raw(1, size=2), raw(2, size=2), raw(3, size=2)]
    place = {n: "01 近い" for n in (1, 2, 3)}
    out = R.compute_ranking(board(specs, place))
    assert out["milestones"][0]["boundary"] is None and out["milestones"][0]["large_slot"] is None
    assert out["forward"] == [] and out["backward"] == [] and out["rejected_stale"] == []
    out = R.compute_ranking(board(specs, place, capacity=5, large_slot_ratio=0))
    assert out["milestones"][0]["boundary"] == {"number": 2, "size_sum": 4, "capacity": 5, "over": False}


def test_large_slot_takes_the_first_fitting_child_and_boundary_excludes_it():
    # #1 は大きい課題（Size 20）。子 #2（Size 8）は枠 6 を超え、#3（Size 5）が枠に入る。#3 は #2 に依存しない
    specs = [raw(1, ubv=13, size=20), raw(2, size=8), raw(3, size=5), raw(4, ubv=8, tc=8, rr=5, size=3), raw(5, ubv=8, tc=8, rr=5, size=3)]
    place = {n: "01 近い" for n in (1, 2, 3, 4, 5)}
    out = R.compute_ranking(board(specs, place, capacity=13, subs={1: [2, 3]}))
    ms = out["milestones"][0]
    assert ms["large_slot"] == {"issue": 1, "slice": 3, "size": 5, "budget": 6, "status": "当てた"}
    # 容量の残りは 13 - 5 = 8。#3 を除いて先頭から取る
    assert ms["boundary"] == {"number": 5, "size_sum": 6, "capacity": 13, "over": False}


def test_large_issue_without_a_fitting_piece_needs_splitting_and_returns_the_slot():
    specs = [raw(1, ubv=13, size=20), raw(2, ubv=8, tc=8, rr=5, size=5), raw(3, ubv=8, tc=8, rr=5, size=5)]
    place = {n: "01 近い" for n in (1, 2, 3)}
    out = R.compute_ranking(board(specs, place, capacity=13))
    ms = out["milestones"][0]
    assert ms["large_slot"]["status"] == "分割が要る" and ms["large_slot"]["slice"] is None
    assert ms["boundary"] == {"number": 3, "size_sum": 10, "capacity": 13, "over": False}


def test_no_large_issue_returns_the_slot():
    out = R.compute_ranking(board([raw(1, size=3)], {1: "01 近い"}, capacity=13))
    assert out["milestones"][0]["large_slot"]["status"] == "なし"


# ---------------- AC6〜AC8 ----------------


def _example(capacity=3):
    """設計の例: 容量 3 の直近 M1 に #1422・#1382、M2 に #1289、未設定に #1399。"""
    specs = [
        raw(1422, ubv=3, tc=5, rr=2, size=1, observed_harm=True),
        raw(1382, ubv=3, tc=1, rr=2, size=2),
        raw(1289, ubv=5, tc=3, rr=5, size=3, observed_harm=True),
        raw(1399, ubv=13, tc=8, rr=8, size=5, observed_harm=True),
    ]
    place = {1422: "01 近い", 1382: "01 近い", 1289: "02 次", 1399: None, 772: "02 次", 785: "02 次"}
    return board(specs, place, subs={1289: [772, 785]}, capacity=capacity)


def test_design_example_moves_and_pairs_a_backward_candidate():
    out = R.compute_ranking(_example())
    fwd = {f["number"]: f for f in out["forward"]}
    assert set(fwd) == {1399, 1289}
    assert fwd[1399]["threshold"] == 8 and fwd[1399]["boundary_cod"] == 6 and fwd[1399]["diff"] == 23
    assert fwd[1399]["higher_columns"] == ["UBV", "TC", "RR/OE"] and fwd[1399]["decision"] == "自動"
    assert [b["number"] for b in out["backward"]] == [1382] and out["backward"][0]["decision"] == "承認"
    assert out["backward"][0]["to"] == "02 次"
    assert ranks(out) == [1399, 1289, 1422, 1382]  # 自動の前倒しを入れた後の並び
    assert out["milestones"][0]["totals"] == {"cod_sum": 61, "cod_max": 29, "high_count": 1}


def test_forward_without_observed_harm_needs_approval():
    specs = [raw(1, size=1), raw(2, ubv=13, tc=8, rr=8, size=1)]
    out = R.compute_ranking(board(specs, {1: "01 近い", 2: "02 次"}, capacity=1))
    assert out["forward"][0]["decision"] == "承認" and ranks(out) == [1]
    b = board(specs, {1: "01 近い", 2: "02 次"}, capacity=1)
    b.approved = (2,)
    out = R.compute_ranking(b)
    assert out["forward"][0]["decision"] == "承認済み" and ranks(out) == [2, 1]


def test_margin_keeps_near_candidates_out_and_threshold_extends_past_the_scale():
    cfg = R.build_config({}, None)
    assert cfg.threshold(6) == 8 and cfg.threshold(8) == 13 and cfg.threshold(20) == 27 and cfg.threshold(24) == 27
    assert R.build_config({"margin_steps": 2}, None).threshold(20) == 34
    specs = [raw(1, ubv=3, tc=2, rr=1, size=1), raw(2, ubv=3, tc=3, rr=1, size=1)]  # 境界 CoD 6、候補 CoD 7 < 8
    assert R.compute_ranking(board(specs, {1: "01 近い", 2: "02 次"}, capacity=1))["forward"] == []


def test_forward_group_carries_dependencies_behind_the_nearest():
    specs = [raw(1, size=1), raw(2, ubv=13, tc=8, rr=8, size=1, observed_harm=True), raw(3, size=1), raw(4, size=1)]
    place = {1: "01 近い", 2: "03 後", 3: "02 次", 4: "01 近い"}
    out = R.compute_ranking(board(specs, place, capacity=2, deps=[(2, 3), (2, 4)]))
    f = next(x for x in out["forward"] if x["number"] == 2)
    assert f["group"] == [3]
    assert out["placement"][3] == "01 近い"


def test_backward_skips_depended_issues_and_needs_a_next_milestone():
    specs = [raw(1, size=1), raw(2, ubv=5, size=1), raw(3, ubv=13, tc=8, rr=8, size=1)]
    place = {1: "01 近い", 2: "01 近い", 3: "02 次"}
    out = R.compute_ranking(board(specs, place, capacity=2, deps=[(2, 1)]))
    assert [b["number"] for b in out["backward"]] == [2]  # #1 は #2 が依存しているため外す
    out = R.compute_ranking(board(specs, place, capacity=2))
    assert [b["number"] for b in out["backward"]] == [1]
    out = R.compute_ranking(board(specs, {1: "01 近い", 2: "01 近い", 3: None}, order=("01 近い",), capacity=2))
    assert out["forward"] and out["backward"] == []  # 次のマイルストーンが無い


def test_backward_is_not_offered_when_it_outranks_every_forward_candidate():
    specs = [raw(1, ubv=13, tc=8, rr=8, size=1), raw(2, ubv=13, tc=8, rr=8, size=1)]
    out = R.compute_ranking(board(specs, {1: "01 近い", 2: "02 次"}, capacity=1, margin_steps=0))
    assert out["forward"] == [] and out["backward"] == []


# ---------------- E8（却下） ----------------


def test_rejected_move_is_dropped_until_its_digest_changes():
    first = R.compute_ranking(_example())
    b = _example()
    b.rejected = [
        {"number": 1399, "direction": "前倒し", "move_digest": next(f for f in first["forward"] if f["number"] == 1399)["move_digest"]}
    ]
    out = R.compute_ranking(b)
    assert [f["number"] for f in out["forward"]] == [1289]
    assert out["rejected"][0]["number"] == 1399 and out["rejected_stale"] == []
    b = _example(capacity=1)  # 境界が変わると別の移動になる
    b.rejected = [{"number": 1399, "direction": "前倒し", "move_digest": first["forward"][0]["move_digest"]}]
    out = R.compute_ranking(b)
    assert 1399 in [f["number"] for f in out["forward"]] and out["rejected_stale"]


def test_rejected_backward_is_not_replaced_by_the_next_one():
    first = R.compute_ranking(_example())
    b = _example()
    b.rejected = [{"number": 1382, "direction": "後ろ倒し", "move_digest": first["backward"][0]["move_digest"]}]
    out = R.compute_ranking(b)
    assert out["backward"] == [] and out["rejected"][0]["direction"] == "後ろ倒し"


def test_rejected_stale_is_empty_without_capacity():
    b = _example()
    b.cfg = R.build_config({}, None)
    b.rejected = [{"number": 9, "direction": "前倒し", "move_digest": "x"}]
    assert R.compute_ranking(b)["rejected_stale"] == []


# ---------------- AC13・I8 ----------------


def test_arguments_beat_the_declaration_and_defaults_are_generic():
    cfg = R.build_config({"capacity": 5, "size_exponent": None}, {"capacity": 13, "size_exponent": 0.5, "margin_steps": 2})
    assert (cfg.capacity, cfg.size_exponent, cfg.margin_steps) == (5, 0.5, 2)
    d = R.build_config({}, None)
    assert d.capacity is None and d.label_floor == {} and d.scale == (1, 2, 3, 5, 8, 13, 20)
    assert (d.margin_steps, d.auto_threshold, d.size_exponent, d.large_slot_ratio, d.large_min_size) == (1, 8, 0.3, 0.5, 13)


def test_scripts_do_not_hold_values_of_this_repository():
    text = "".join(p.read_text(encoding="utf-8") for p in SCRIPTS.glob("upkeep_rank*.py"))
    assert "priority" not in text and "#1399" not in text and "#1142" not in text


@pytest.mark.parametrize(
    "args",
    [
        {"size_exponent": 1.5},
        {"large_slot_ratio": 1},
        {"auto_threshold": 7},
        {"scale": "3,2"},
        {"capacity": 0},
        {"dependents_steps": "2:5,1:8"},
    ],
)
def test_bad_settings_are_errors(args):
    with pytest.raises(R.RankError):
        R.build_config(args, None)


# ---------------- AC17 ----------------


def test_size_exponent_one_and_zero():
    specs = [raw(1, ubv=3, tc=5, rr=2, size=1), raw(2, ubv=13, tc=8, rr=3, size=5), raw(3, ubv=8, tc=3, rr=3, size=2)]
    place = {n: "01 近い" for n in (1, 2, 3)}
    assert ranks(R.compute_ranking(board(specs, place, size_exponent=1))) == [1, 3, 2]  # 10.0・7.0・4.8
    assert ranks(R.compute_ranking(board(specs, place, size_exponent=0))) == [2, 3, 1]  # CoD 24・14・10
    specs = [raw(1, ubv=3, tc=5, rr=2, size=1), raw(2, ubv=13, tc=8, rr=3, size=5)]  # CoD 10・Size 1 と CoD 24・Size 5
    assert ranks(R.compute_ranking(board(specs, {1: "01 近い", 2: "01 近い"}))) == [2, 1]


# ---------------- AC20 ----------------


def test_waiting_decision_marks_without_changing_the_value():
    plain = R.compute_ranking(board([raw(1, ubv=8, size=2)], {1: "01 近い"}))
    waiting = R.compute_ranking(board([raw(1, ubv=8, size=2, waiting_decision=True)], {1: "01 近い"}))
    a, b = plain["milestones"][0]["rows"][0], waiting["milestones"][0]["rows"][0]
    assert a["value"] == b["value"] and b["waiting_decision"] is True
    assert "#1（判断待ち）" in R.render_rank_table(waiting["milestones"][0])


# ---------------- 表・差分（AC11・AC12） ----------------


def test_table_round_trips_and_changes_are_explained():
    out = R.compute_ranking(_example())
    table = R.render_rank_table(out["milestones"][0])
    desc = R.replace_rank_section("主題の文\n\n### 並列の組（見込み）\n\n| 組 | 課題 | 触る場所の見込み | 依存 |\n", table)
    assert desc.startswith("主題の文\n\n### 並列の組（見込み）\n\n| 組 | 課題 | 触る場所の見込み | 依存 |\n")
    assert R.replace_rank_section(desc, table) == desc
    prev = R.parse_rank_table(desc)
    assert [r["number"] for r in prev["rows"]] == [1399, 1289, 1422, 1382] and prev["rows"][1]["rr_oe_dependents"]
    b = _example()
    b.previous = {"01 近い": prev}
    b.estimates[1422] = R.validate_estimate(raw(1422, ubv=3, tc=13, rr=2, size=1, observed_harm=True), b.cfg)[0]
    got = {(c["number"], c["kind"]): c for c in R.compute_ranking(b)["changes"]}
    assert got[(1422, "up")]["columns"] == ["TC 5→13"] and (1289, "down") in got
    assert R.rank_changes([{"title": "01 近い", "rows": []}], {"01 近い": prev}, {1399: "02 次"}, {})[0]["reason"] == "02 次 へ移った"


def test_restored_rows_use_the_scale_minimum_for_marked_cells():
    cfg = R.build_config({}, None)
    row = {"number": 1, "ubv": 8, "ubv_label": True, "tc": 3, "rr_oe": 8, "rr_oe_dependents": True, "size": 2, "waiting_decision": True}
    est, _ = R.validate_estimate(R.restore_from_row(row, cfg), cfg, check_band=False)
    assert est.steps == {"ubv": 1, "tc": 3, "rr_oe": 1, "size": 2} and est.restored and not est.observed_harm


# ---------------- 非機能（性能） ----------------


def test_two_hundred_issues_and_four_hundred_edges_within_five_seconds():
    scale = (1, 2, 3, 5, 8, 13, 20)
    specs = [raw(n, ubv=scale[n % 7], tc=scale[(n * 3) % 7], rr=scale[(n * 5) % 7], size=scale[(n * 2) % 7]) for n in range(1, 201)]
    place = {n: ("01 近い", "02 次", "03 後", None)[n % 4] for n in range(1, 201)}
    deps = [(n, m) for n in range(3, 201) for m in (n - 1, n - 2)][:400]
    t = time.monotonic()
    out = R.compute_ranking(board(specs, place, deps=deps, capacity=40))
    assert time.monotonic() - t < 5 and sum(len(m["rows"]) for m in out["milestones"]) > 0
