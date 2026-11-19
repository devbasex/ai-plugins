"""候補を上限で切る並びと見積の採り方（#2064・#1640・#2540・#3854）。"""

from __future__ import annotations

import pytest

from ndf.domains.backlog import estimates_decide, model, table_decide
from ndf.domains.backlog import selection_decide as sel
from ndf.domains.backlog.model import Config


def test_manual_issues_are_never_cut_by_the_limit():
    # #2064 の表: --add の 33 件と diff-path の 38 件で --limit 45 のとき、--add の 33 件がすべて残る
    found = {n: {sel.DIFF_PATH} for n in range(1, 39)}
    found |= {n: {sel.MANUAL} for n in range(1600, 1633)}
    got = sel.select(found, 45, model.DIFF, {})
    assert set(range(1600, 1633)) <= set(got.keep)
    assert len(got.keep) == 45 and len(got.deferred) == 26


def test_manual_beyond_the_limit_is_kept_and_counted():
    found = {n: {sel.MANUAL} for n in range(1, 6)}
    got = sel.select(found, 3, model.DIFF, {})
    assert got.keep == [1, 2, 3, 4, 5] and got.manual_over == 2


def test_carried_issues_come_before_other_routes():
    # #1640: 前回の持ち越しは経路の多い課題より先に残る
    found = {1: {sel.DIFF_PATH, sel.NO_MILESTONE}, 2: {sel.DIFF_PATH, sel.NO_MILESTONE}, 50: {sel.CARRIED}}
    got = sel.select(found, 1, model.DIFF, {})
    assert got.keep == [50] and got.deferred == [1, 2]


def test_review_keeps_manual_first_then_oldest():
    found = {1: {sel.ALL}, 2: {sel.ALL}, 3: {sel.ALL, sel.MANUAL}}
    got = sel.select(found, 2, model.REVIEW, {1: "2026-11-02", 2: "2026-10-01", 3: "2026-11-09"})
    assert got.keep == [3, 2]


def test_carry_does_not_count_issues_deferred_only_for_missing_estimates():
    # #2540: 見積の欠けだけで入った課題は、見直しの起動の材料（carry）に数えない
    found = {1: {sel.MANUAL}, **{n: {sel.UNSCORED} for n in range(10, 20)}, 30: {sel.DIFF_PATH, sel.UNSCORED}}
    got = sel.select(found, 1, model.DIFF, {})
    assert len(got.deferred) == 11 and got.carry == 1


def _col(step, why: str = "根拠") -> dict:
    return {"step": step, "why": why}


def _raw(n: int, digest: str | None = None, ubv: int = 3, **extra) -> dict:
    return {"number": n, "ubv": _col(ubv), "tc": _col(2), "rr_oe": _col(1), "size": _col(2), "digest": digest, **extra}


def _pick(numbers, given=None, stored=None, previous=None, tables=None, cfg=None, digests=None, since=None, created=None):
    src = estimates_decide.Sources(given or {}, stored or {}, previous or {}, tables or {})
    return estimates_decide.estimates(numbers, src, cfg or Config(), digests, since, created)


def test_review_takes_restored_estimates_and_excludes_stale_ones():
    # #3854 の決定 9: 見直しでも前回の表から戻した見積を採り、restored に返す。要約値の違う記録は今のとおり外す
    stored = {"1": _raw(1, "now1"), "2": _raw(2, "old2")}
    previous = {3: {"number": 3, "ubv": 3, "tc": 2, "rr_oe": 1, "size": 2}}
    digests = {1: "now1", 2: "now2", 3: "now3", 4: "now4"}
    got = _pick([1, 2, 3, 4], stored=stored, previous=previous, digests=digests)
    assert sorted(got.estimates) == [1, 3] and got.restored == [3]
    assert {e["number"]: e["reason"] for e in got.excluded} == {2: estimates_decide.STALE, 4: estimates_decide.MISSING}


def test_diff_depth_takes_stored_and_previous_estimates():
    stored = {"2": _raw(2, "old2")}
    previous = {3: {"number": 3, "ubv": 3, "tc": 2, "rr_oe": 1, "size": 2}}
    got = _pick([2, 3], stored=stored, previous=previous)
    assert sorted(got.estimates) == [2, 3] and got.excluded == [] and got.restored == [3]


def test_given_estimates_need_a_digest_and_a_scale_value():
    given = {1: {k: v for k, v in _raw(1).items() if k != "digest"}, 2: _raw(2, "x", ubv=4)}
    got = _pick([1, 2], given=given)
    assert got.estimates == {} and got.excluded[0]["reason"] == estimates_decide.NO_DIGEST and "UBV" in got.excluded[1]["reason"]


def test_sources_are_taken_in_order_given_stored_previous_table():
    given = {1: _raw(1, "g", ubv=8)}
    stored = {"1": _raw(1, "s", ubv=5), "2": _raw(2, "s", ubv=5)}
    previous = {1: {"number": 1, "ubv": 1, "tc": 1, "rr_oe": 1, "size": 1}, 3: {"number": 3, "ubv": 2, "tc": 1, "rr_oe": 1, "size": 1}}
    tables = {3: {"number": 3, "ubv": 13, "tc": 1, "rr_oe": 1, "size": 1}, 4: {"number": 4, "ubv": 20, "tc": 1, "rr_oe": 1, "size": 1}}
    got = _pick([1, 2, 3, 4], given=given, stored=stored, previous=previous, tables=tables)
    assert {n: e.ubv for n, e in got.estimates.items()} == {1: 8, 2: 5, 3: 2, 4: 20}
    assert got.restored == [3, 4] and got.excluded == []


def test_table_steps_marked_by_label_or_dependents_go_back_to_the_lowest_step():
    tables = {5: {"number": 5, "ubv": None, "tc": 3, "rr_oe": None, "size": 5, "waiting_decision": True}}
    est = _pick([5], tables=tables).estimates[5]
    assert (est.ubv, est.tc, est.rr_oe, est.size, est.waiting_decision, est.restored) == (1, 3, 1, 5, True, True)


def test_previous_rank_steps_adjusted_by_label_or_dependents_go_back_to_the_lowest_step():
    # 前回の順位の UBV 13 が bug ラベルの下限なら、ラベルを外した再計算で 13 に残らない
    src = {"ubv": model.SOURCE_LABEL, "tc": model.SOURCE_LLM, "rr_oe": model.SOURCE_DEPENDENTS, "size": model.SOURCE_RECORDS}
    previous = {5: {"number": 5, "ubv": 13, "tc": 3, "rr_oe": 8, "size": 5, "sources": src},
                6: {"number": 6, "ubv": 13, "tc": 3, "rr_oe": 8, "size": 5, "sources": {"ubv": "llm", "rr_oe": "llm"}}}  # fmt: skip
    got = _pick([5, 6], previous=previous).estimates
    assert (got[5].ubv, got[5].tc, got[5].rr_oe, got[5].size) == (1, 3, 1, 5)
    assert (got[6].ubv, got[6].rr_oe) == (13, 8)


def test_missing_estimates_split_into_excluded_and_left_for_review_by_created_time():
    # #3854 の I9: 起点ちょうどの時刻に作られた課題は起点以前に数える。起点が無ければすべて excluded
    since = "2026-11-01T00:00:00Z"
    created = {1: "2026-10-01T00:00:00Z", 2: since, 3: "2026-11-02T00:00:00Z"}
    got = _pick([1, 2, 3], since=since, created=created)
    assert got.left_for_review == [1, 2] and [e["number"] for e in got.excluded] == [3] and got.estimates == {}
    assert [e["number"] for e in _pick([1, 2, 3]).excluded] == [1, 2, 3]


def test_old_integer_records_are_taken_as_restored_estimates_without_a_band_check():
    # #3854 の決定 8: 11.0.x が書いた整数の記録は根拠の無い見積として採る。見直しでも要約値が同じなら採る
    stored = {"1": {"number": 1, "ubv": 20, "tc": 1, "rr_oe": 1, "size": 1, "digest": "d1"}}
    got = _pick([1], stored=stored, digests={1: "d1"})
    assert got.estimates[1].ubv == 20 and got.restored == [1]


def test_records_with_an_impact_are_checked_against_the_band():
    # #3854 の条件 5: 記録から採った見積も区分を持てば帯で確かめる
    stored = {"1": {**_raw(1, "d1"), "ubv": {"step": 13, "why": "根拠", "impact": "表示"}}}
    got = _pick([1], stored=stored)
    assert got.estimates == {} and "帯 2〜3 の外" in got.excluded[0]["reason"]


def test_unscored_matches_the_review_rule():
    stored = {"1": _raw(1, "a"), "2": _raw(2, "old")}
    assert estimates_decide.unscored([1, 2, 3], stored, {1: "a", 2: "new", 3: "c"}) == [2, 3]
    assert estimates_decide.unscored([1], {}, {1: "a"}) == []


# --- 現状固定: 記録の鮮度（_pick の記録の枝と unscored）。辞書でない記録と digest の欠けた記録の扱いを記録する ---


def test_review_treats_non_dict_and_digestless_records_as_stale():
    stored = {"1": "壊れた記録", "2": {"number": 2, "ubv": 3, "tc": 2, "rr_oe": 1, "size": 2}, "3": _raw(3, "now3")}
    digests = {1: "now1", 2: "now2", 3: "now3"}
    got = _pick([1, 2, 3], stored=stored, digests=digests)
    assert list(got.estimates) == [3]
    assert [(e["number"], e["reason"]) for e in got.excluded] == [(1, estimates_decide.STALE), (2, estimates_decide.STALE)]
    assert estimates_decide.unscored([3, 2, 1, 4], stored, {**digests, 4: "now4"}) == [2, 1, 4]


def test_review_checks_the_given_scores_against_the_current_issue():
    # 候補の収集の後に本文が書き換わった課題の古い scores で見直しを終えない
    given = {1: _raw(1, "now1"), 2: _raw(2, "old2")}
    got = _pick([1, 2], given=given, digests={1: "now1", 2: "now2"})
    assert list(got.estimates) == [1] and got.excluded == [{"number": 2, "reason": estimates_decide.STALE}]


def test_issues_deferred_only_for_missing_estimates_stay_out_of_carry_when_carried():
    # 見積の欠けだけで持ち越した課題は、次の回に carried が付いても carry に数えない
    first = sel.select({1: {sel.MANUAL}, 10: {sel.UNSCORED}, 30: {sel.DIFF_PATH}}, 1, model.DIFF, {})
    assert first.unscored_only == [10] and first.carry == 1
    found = {1: {sel.MANUAL}, 10: {sel.CARRIED, sel.UNSCORED}, 30: {sel.CARRIED, sel.DIFF_PATH}}
    again = sel.select(found, 1, model.DIFF, {}, frozenset(first.unscored_only))
    assert sorted(again.deferred) == [10, 30] and again.carry == 1 and again.unscored_only == [10]


# --- #3854: 見積の入力の形・実害の目安・説明の表の読み戻し ---


def _ranking_md_example() -> dict:
    """ranking.md の「見積の入力」の例の 1 件（手順書が定める形）。"""
    return {"number": 1399, "digest": "<candidates の値>",
            "ubv": {"step": 13, "why": "取り消した変更が origin へ出た（安全機構の欠落）", "impact": "全体"},
            "tc": {"step": 8, "why": "..."}, "rr_oe": {"step": 8, "why": "..."},
            "size": {"step": 5, "why": "<閉じた課題の番号>（差分 <行数>・<所要>・<費用>）と同じ程度"},
            "mitigation": "無い", "observed_harm": True, "depends_on": [1237], "waiting_decision": False}  # fmt: skip


def test_parse_estimate_reads_the_ranking_md_example():
    est = model.parse_estimate(_ranking_md_example(), Config(), given=True)
    assert (est.ubv, est.tc, est.rr_oe, est.size, est.impact, est.mitigation, est.observed_harm) == (13, 8, 8, 5, "全体", "無い", True)
    assert est.why["ubv"].startswith("取り消した変更") and not est.restored


def test_parse_estimate_takes_over_scale_size_and_a_missing_impact():
    raw = {**_raw(1, "d"), "size": _col(model.OVER_SCALE)}
    est = model.parse_estimate(raw, Config(), given=True)
    assert est.over and est.impact is None


@pytest.mark.parametrize(
    "change, message",
    [
        ({"ubv": 5}, 'UBV は {"step", "why"} で書く'),
        ({"tc": {"step": 2}}, "TC の根拠（why）が空"),
        ({"rr_oe": {"step": 1, "why": "  "}}, "RR/OE の根拠（why）が空"),
        ({"size": {"why": "x"}}, 'Size は {"step", "why"} で書く'),
        ({"ubv": {"step": 3, "why": "x", "impact": "知らない区分"}}, "UBV の区分 '知らない区分' が実害の目安に無い"),
        ({"mitigation": "たぶん"}, "退避策の状態"),
    ],
)
def test_parse_estimate_rejects_wrong_shapes_and_empty_reasons(change, message):
    with pytest.raises(model.EstimateError, match=message):
        model.parse_estimate({**_raw(1, "d"), **change}, Config(), given=True)


def test_check_band_accepts_the_band_and_one_band_lower_only_when_mitigated():
    cfg = Config()
    assert model.check_band(5, "機能", "無い", cfg) is None and model.check_band(8, "機能", "無い", cfg) is None
    assert "帯 5〜8 の外" in model.check_band(3, "機能", "無い", cfg)
    assert model.check_band(3, "機能", "防いでいる", cfg) is None and model.check_band(2, "機能", "防いでいる", cfg) is None
    assert model.check_band(1, "機能", "防いでいる", cfg) is not None and model.check_band(13, "機能", "防いでいる", cfg) is not None
    assert model.check_band(3, "なし", "防いでいる", cfg) is not None
    assert model.check_band(1, "なし", "防いでいる", cfg) is None
    # 帯の間に隙間があれば、退避策があっても 1 つ下の帯の上限を超える値は受けない
    gap = Config(harm_guide={"全体": (13, 20), "表示": (2, 3)})
    assert model.check_band(8, "全体", "防いでいる", gap) is not None
    assert model.check_band(3, "全体", "防いでいる", gap) is None


def test_config_harm_guide_defaults_and_custom_scale():
    assert model.config_from({}, None).harm_guide == model.HARM_GUIDE
    # 既定の帯の端が尺度に無く harm_guide を書いていなければ、帯を確かめない（空）
    custom = {"scale": [1, 2, 4, 8, 16], "dependents_steps": [[0, 1]], "auto_threshold": 8, "large_min_size": 8}
    assert model.config_from(custom, None).harm_guide == {}
    written = model.config_from({**custom, "harm_guide": {"重い": [8, 16], "軽い": [1, 2]}}, None)
    assert written.harm_guide == {"重い": (8, 16), "軽い": (1, 2)}
    with pytest.raises(model.ConfigError, match="harm_guide"):
        model.config_from({**custom, "harm_guide": {"全体": [13, 20]}}, None)
    with pytest.raises(model.ConfigError, match="下限を上限以下"):
        model.config_from({"harm_guide": {"全体": [20, 13]}}, None)


def test_without_a_harm_guide_the_impact_is_kept_without_a_band_check():
    cfg = model.config_from({"scale": [1, 2, 4, 8, 16], "dependents_steps": [[0, 1]], "auto_threshold": 8, "large_min_size": 8}, None)
    raw = {**_raw(1, "d"), "ubv": {"step": 16, "why": "x", "impact": "表示"}, "tc": _col(1), "rr_oe": _col(1), "size": _col(1)}
    assert model.parse_estimate(raw, cfg, given=True).impact == "表示"


def test_estimate_to_json_keeps_reasons_and_the_impact():
    est = model.parse_estimate({**_raw(1, "d"), "ubv": {"step": 5, "why": "w", "impact": "機能"}}, Config(), given=True)
    got = est.to_json()
    assert got["ubv"] == {"step": 5, "why": "w", "impact": "機能"} and got["tc"] == {"step": 2, "why": "根拠"}
    assert got["mitigation"] == "無い" and got["digest"] == "d"


def _table(rows: list[dict]) -> str:
    ms = {"rows": rows, "large_slot": None, "boundary": None}
    return (
        "説明の文\n\n" + table_decide.replace_section("", table_decide.render(ms)) + "\n## 後ろ\n\n| 1 | #99 | 1 | 1 | 1 | 1 | 3 | 3.0 |\n"
    )


def _row(n: int, ubv: int = 3, rr: int = 1, size=2, waiting: bool = False, ubv_src="llm", rr_src="llm", size_src="llm") -> dict:
    return {"rank": 1, "number": n, "ubv": ubv, "tc": 2, "rr_oe": rr, "size": size, "cod": 7, "value": 1.5,
            "waiting_decision": waiting, "sources": {"ubv": ubv_src, "rr_oe": rr_src, "size": size_src}}  # fmt: skip


def test_parse_rows_reads_the_rendered_table_and_drops_the_marks():
    marked = [_row(2, ubv=13, waiting=True, ubv_src=model.SOURCE_LABEL),
              _row(3, rr=8, rr_src=model.SOURCE_DEPENDENTS, size_src=model.SOURCE_RECORDS)]  # fmt: skip
    desc = _table([_row(1), *marked])
    got = table_decide.parse_rows(desc)
    assert sorted(got) == [1, 2, 3]
    assert got[1] == {"number": 1, "ubv": 3, "tc": 2, "rr_oe": 1, "size": 2, "waiting_decision": False}
    assert got[2]["ubv"] is None and got[2]["waiting_decision"] is True
    assert got[3]["rr_oe"] is None and got[3]["size"] == 2


def test_parse_rows_reads_over_scale_and_skips_other_lines():
    ms = {"rows": [_row(1, size=model.OVER_SCALE), _row(2)], "large_slot": None, "boundary": None}
    desc = table_decide.replace_section("", table_decide.render(ms, limit=1)) + "| 壊れた | 行 |\n"
    got = table_decide.parse_rows(desc)
    assert got == {1: {"number": 1, "ubv": 3, "tc": 2, "rr_oe": 1, "size": model.OVER_SCALE, "waiting_decision": False}}
    assert "件は表に載せていない" in desc
    assert table_decide.parse_rows("表の無い説明") == {}
