"""drive の後半の判定（予算と安全網・成立の判定・フレーキーの直し・計画のコメントと完了報告）。"""

from __future__ import annotations

import pytest

from ndf.domains.crossrefactor import budget_decide, flaky_decide, judge_decide, report_decide


def _item(n: int, tier: str, test: float, impl: float, by: int = 1) -> dict:
    return {
        "id": f"I-{n:03d}",
        "path": "src/a.py",
        "symbol": f"f{n}",
        "title": "t",
        "tier": tier,
        "estimate": {"test": test, "implement": impl},
        "by": ["x"] * by,
    }


def test_budget_skips_a_large_item_and_still_takes_the_smaller_ones_after_it() -> None:
    got = budget_decide.select([_item(1, "high", 10, 30), _item(2, "medium", 5, 5), _item(3, "low", 1, 1)], 30, factor=1.0)
    assert [i["id"] for i in got.adopted] == ["I-002", "I-003"]
    assert [i["id"] for i in got.skipped] == ["I-001"] and got.skipped[0]["reason"].startswith(budget_decide.OVER_BUDGET)


def test_budget_extends_up_to_the_factor_when_high_items_exceed_it() -> None:
    got = budget_decide.select([_item(1, "high", 10, 25), _item(2, "high", 0, 10)], 30, factor=2.0)
    # 順位が同じなら見積りの合計の昇順（旧い refactor_lib/budget.py の rank_key と同じ）
    assert got.budget.extended_to == 45 and [i["id"] for i in got.adopted] == ["I-002", "I-001"]
    beyond = budget_decide.select([_item(1, "high", 50, 50)], 30, factor=2.0)
    assert beyond.budget.extended_to is None and beyond.adopted == []


@pytest.mark.parametrize("estimate", [None, {"test": 1}, {"test": True, "implement": 1}, {"test": -1, "implement": 1}])
def test_item_without_a_valid_estimate_is_skipped_with_a_reason(estimate) -> None:
    item = {**_item(1, "high", 1, 1), "estimate": estimate}
    got = budget_decide.select([item], 30)
    assert got.adopted == [] and got.skipped[0]["reason"] == budget_decide.NO_ESTIMATE


def test_safety_net_counts_only_coding_phases_and_halts_at_the_limit() -> None:
    net = budget_decide.SafetyNet.of(10, explicit=1.0)
    net = net.add("propose", 600).add("judge", 600)
    assert net.used_seconds == 0 and not net.halted
    net = net.add("implement", 59).add("flaky-fix", 1)
    assert net.halted and "1 分" in net.reason()


def test_safety_net_default_limit_is_the_effective_budget_times_the_factor() -> None:
    assert budget_decide.SafetyNet.of(30).limit_minutes == 60


def test_stall_asks_for_a_judgment_once_then_reverts() -> None:
    item: dict = {"id": "I-001"}
    assert judge_decide.after_stall(item, ["codex"]).kind == judge_decide.JUDGE
    assert judge_decide.after_stall(item, []).reason == judge_decide.NO_JUDGE
    item["feasibility"] = [judge_decide.record("codex", {"feasible": True}, "t")]
    assert judge_decide.after_stall(item, ["codex"]).reason == judge_decide.STALLED_AFTER_JUDGE


def test_judge_answer_decides_continue_infeasible_or_undecided() -> None:
    payload = {"items": [{"item": "I-001", "feasible": False, "reason": "型が合わない"}, {"item": "I-002", "feasible": "yes"}]}
    row = judge_decide.answer_of(payload, "I-001")
    assert judge_decide.on_answer(row).reason == judge_decide.INFEASIBLE
    assert judge_decide.answer_of(payload, "I-002") is None
    assert judge_decide.on_answer(None).reason == judge_decide.UNDECIDED
    assert judge_decide.on_answer({"feasible": True}).kind == judge_decide.CONTINUE
    assert judge_decide.record("codex", None, "t")["feasible"] is None


def test_flaky_keys_pass_on_rerun_and_are_asked_once() -> None:
    stable, flaky = flaky_decide.split_flaky({"pytest:a", "pytest:b"}, {"pytest:a"})
    assert stable == {"pytest:a"} and flaky == {"pytest:b"}
    assert flaky_decide.fresh(["pytest:b"], flaky) == []
    assert flaky_decide.fresh([], flaky) == ["pytest:b"]


def test_flaky_fix_violations_send_back_once_then_revert() -> None:
    ok = flaky_decide.Commit("a1", "pytest:b", ("tests/test_b.py",))
    bad = flaky_decide.Commit("b2", "", ("src/b.py",))
    found = flaky_decide.violations([ok, bad], ["pytest:b"], lambda p: p.startswith("tests/"))
    assert [f["kind"] for f in found] == ["trailer", "non_test"]
    assert flaky_decide.outcome([ok, bad], found, 1) == flaky_decide.SENDBACK
    assert flaky_decide.outcome([ok, bad], found, 2) == flaky_decide.REVERTED
    assert flaky_decide.outcome([ok], [], 1) == flaky_decide.KEPT
    assert flaky_decide.outcome([], [], 1) == flaky_decide.NONE
    assert flaky_decide.fixed([ok]) == {"pytest:b": "a1"}


def test_plan_comment_and_report_show_items_budget_and_steps() -> None:
    state = {
        "items": [
            {
                **_item(1, "high", 1, 1),
                "status": "reverted",
                "reason": "成立しない",
                "feasibility": [{"by": "codex", "feasible": False, "reason": "r"}],
            }
        ],
        "skipped": [{"reason": budget_decide.NO_ESTIMATE}],
        "budget": {"minutes": 30, "extended_to": None, "reason": ""},
        "safety_net": {"limit_minutes": 60, "used_seconds": 90},
        "phase_seconds": {"implement": 12.0},
        "flaky_fixes": [{"key": "pytest:b", "result": "kept", "commit": "a1"}],
    }
    body = report_decide.plan_comment(7, state, "完了")
    assert body.startswith(report_decide.MARKER) and "| I-001 |" in body and "予算: 30 分" in body and "1.5 分" in body
    text = report_decide.report(7, state, "完了")
    for part in ("成立しない（codex", "implement: 12 秒", "pytest:b: kept", budget_decide.NO_ESTIMATE, "未実施"):
        assert part in text
    assert report_decide.digest(body) == report_decide.digest(body) != report_decide.digest(text)


def test_flaky_fix_log_is_split_into_commits_with_trailer_and_paths() -> None:
    text = "\x1ea1\x1fpytest:tests/test_b.py\n\x1f\n\ntests/test_b.py\n\x1eb2\x1f\x1f\n\nsrc/b.py\ntests/test_c.py\n"
    got = flaky_decide.parse_log(text)
    assert got == [
        flaky_decide.Commit("a1", "pytest:tests/test_b.py", ("tests/test_b.py",)),
        flaky_decide.Commit("b2", "", ("src/b.py", "tests/test_c.py")),
    ]
