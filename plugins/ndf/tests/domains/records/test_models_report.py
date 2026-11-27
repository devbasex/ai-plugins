"""#2944: `ndf models report`（使用量ログと実験の記録の集計）。

使用量ログと実験の記録の行は、usage.schema.json・experiment.schema.json の形の見本で作り、書く前に形を確かめる。
集計（`report_rows`）は直接呼び、CLI は `main([...])` の結果 JSON を確かめる。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from ndf import cli
from ndf.adapters import model_choice as mc
from ndf.contracts import internal
from ndf.domains.records import jsonl, usage
from ndf.domains.records import models_cli as mo

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
SINCE = datetime(2026, 11, 1, tzinfo=UTC)
UNTIL = datetime(2026, 11, 30, 23, 59, tzinfo=UTC)
ALLOCATION = {"floor": 0.05, "min_units": 10, "margin": 0.1, "window_days": 60}
ARMS = [{}, {"effort": "high"}]
KEY = mc.compatibility_key(ARMS, mc.metric_of("impl"))


def _at(when: datetime) -> str:
    return when.astimezone(UTC).isoformat(timespec="microseconds")  # 記録の時刻は UTC（common.schema.json の time）


def _row(lid: str, when: datetime = NOW, cost: float | None = 1.0, **fields) -> dict:
    """worker の impl の起動 1 回（リクエスト 1 件の 1 回目で、成功）。"""
    row = {"at": _at(when), "caller": "claude-p", "usage": {"input_tokens": 10, "output_tokens": 5,
           "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}, "launch_id": lid, "session_id": f"s-{lid}",
           "session_seq": 0, "layer": "worker", "name": "impl", "runtime": "claude", "request_id": lid, "request_try": 1,
           "seconds": 10.0, "outcome": "ok"}  # fmt: skip
    if cost is not None:
        row["session_cost_usd"] = cost
    return internal.write_record("usage", {**row, **fields})


def _assign(aid: str, chosen: int, when: datetime = NOW, mode: str | None = None) -> dict:
    row = {"kind": "assign", "at": _at(when), "assign_id": aid, "unit": f"run:{aid}", "stage": "impl", "runtime": "claude",
           "repo": "/r", "arms": ARMS, "probs": [0.5, 0.5], "chosen": chosen, "key": KEY, "phase": "burn_in", "window_days": 60}  # fmt: skip
    return internal.write_record("experiment", {**row, **({"mode": mode} if mode else {})})


def _outcome(aid: str, quality: float, when: datetime = NOW) -> dict:
    row = {"kind": "outcome", "at": _at(when), "assign_id": aid, "metric": "pr_review_major_findings", "metric_version": 1}
    return internal.write_record("experiment", {**row, "quality": quality, "detail": {"major": 0}})


def _units(arm: int, count: int, quality: float, cost: float | None, mode: str | None = None, at: datetime = NOW) -> tuple[list, list]:
    """組 `arm` の単位を `count` 個（割り付け・効果指標・起動 1 回ずつ）。`at` から 1 分ずつ前に割り付ける。"""
    rows, launches = [], []
    for i in range(count):
        aid, when = f"a{arm}-{i}", at - timedelta(minutes=i)
        rows += [_assign(aid, arm, when, mode), _outcome(aid, quality, when)]
        launches.append(_row(f"l{arm}-{i}", when, cost, assign_id=aid, **({"effort": "high"} if arm else {})))
    return rows, launches


def _report(usage_rows=(), rows=(), stage=None, since=SINCE, end=UNTIL, repo="/r") -> list[dict]:
    return mo.report_rows(list(usage_rows), list(rows), mo.Scope(since, end, stage, ALLOCATION, repo))


def _refusal(aid: str, lid: str, arm: dict, when: datetime = NOW) -> dict:
    row = {"kind": "launch_error", "at": _at(when), "assign_id": aid, "launch_id": lid, "stage": "impl", "runtime": "claude"}
    return internal.write_record("experiment", {**row, "arm": arm, "reason": "404", "switched": True})


def _launches(items: list[dict], **key) -> dict:
    (found,) = [i for i in items if i["kind"] == "launch_group" and all(i.get(k) == v for k, v in key.items())]
    return found


def _arms(items: list[dict]) -> list[dict]:
    return sorted((i for i in items if i["kind"] == "arm"), key=lambda i: i["arm"])


# --- 起動の値・リクエスト・ステップ ---


def test_a_launch_logged_twice_is_counted_once():
    one = _launches(_report([_row("l1"), _row("l1")]), stage="impl")
    assert (one["launch_count"], one["cost_usd"], one["requests"], one["success_rate"]) == (1, 1.0, 1, 1.0)
    assert one["tokens"]["input_tokens"] == 10 and one["seconds"] == 10.0


def test_a_launch_of_unknown_cost_is_counted_but_not_added():
    one = _launches(_report([_row("l1"), _row("l2", cost=None)]), stage="impl")
    assert (one["launch_count"], one["cost_usd"], one["unpriced_launches"]) == (2, 1.0, 1)


def test_a_launch_whose_actual_model_differs_from_the_requested_one_is_counted():
    rows = [_row("l1", requested_model="sonnet", model="claude-opus-4"), _row("l2", requested_model="sonnet", model="sonnet")]
    assert _launches(_report(rows), requested_model="sonnet")["mismatched"] == 1


def test_a_request_switched_to_the_base_counts_as_a_success_of_the_base():
    refused = _row("l1", outcome="stopped", effort="high", assign_id="a1", request_id="q")
    base = _row("l2", request_id="q", request_try=2)
    items = _report([refused, base], [_assign("a1", 1), _refusal("a1", "l1", {"effort": "high"})])
    assert (_launches(items, effort=None)["requests"], _launches(items, effort=None)["success_rate"]) == (1, 1.0)
    assert (_launches(items, effort="high")["launch_count"], _launches(items, effort="high")["requests"]) == (1, 0)


def test_a_request_whose_launches_were_all_refused_is_a_failure_of_the_last_launch():
    refused = _row("l1", outcome="stopped", effort="high", assign_id="a1")
    one = _launches(_report([refused], [_assign("a1", 1), _refusal("a1", "l1", {"effort": "high"})]), effort="high")
    assert (one["requests"], one["success_rate"]) == (1, 0.0)


def test_only_the_refused_launch_is_left_out_of_a_judge_request_sharing_the_assignment():
    def judge(lid: str, step: int, outcome: str, **fields) -> dict:
        return _row(lid, layer="judge", name="ask", request_id="q", request_try=step, outcome=outcome, **fields)

    rows = [judge("l1", 1, "invalid", assign_id="a1"), judge("l2", 2, "stopped", assign_id="a1"), judge("l3", 3, "valid")]
    one = _launches(_report(rows, [_assign("a1", 1), _refusal("a1", "l2", {"effort": "high"})]), stage="judge")
    assert (one["judge_requests"], one["judge_retry_rate"], one["success_rate"]) == (1, 1.0, 1.0)


def test_a_launch_in_the_last_second_of_the_last_day_is_in_the_period():
    since, end = mo.jst_span(date(2026, 11, 30), date(2026, 11, 30))
    assert end - since == timedelta(days=1)
    rows = [_row("l1", end - timedelta(microseconds=500_000)), _row("l2", end)]
    assert _launches(_report(rows, since=since, end=end), stage="impl")["launch_count"] == 1


def test_a_gate_is_left_out_of_the_success_rate():
    one = _launches(_report([_row("l1"), _row("l2", outcome="gate"), _row("l3", outcome="stopped")]), stage="impl")
    assert (one["requests"], one["success_rate"]) == (2, 0.5)


def test_judge_retries_and_judgements_returned_to_a_person():
    def judge(lid: str, request: str, step: int, outcome: str) -> dict:
        return _row(lid, layer="judge", name="ask", request_id=request, request_try=step, outcome=outcome)

    rows = [judge("1", "a", 1, "invalid"), judge("2", "a", 2, "valid"), judge("3", "b", 1, "invalid"), judge("4", "b", 2, "invalid")]
    one = _launches(_report([*rows, judge("5", "c", 1, "valid")]), stage="judge")
    assert (one["judge_requests"], one["judge_retry_rate"], one["judge_returned"]) == (3, 2 / 3, 1)
    assert (one["requests"], one["success_rate"]) == (3, 2 / 3)


def test_a_step_with_a_second_attempt_is_a_retry_of_the_first_attempt_values():
    rows = [_row("l1", run_id="r", step="impl", attempt=1, effort="high"), _row("l2", run_id="r", step="impl", attempt=2)]
    rows.append(_row("l3", run_id="r", step="test", attempt=1))
    items = _report(rows)
    assert (_launches(items, effort="high")["steps"], _launches(items, effort="high")["retry_rate"]) == (1, 1.0)
    assert (_launches(items, effort=None)["steps"], _launches(items, effort=None)["retry_rate"]) == (1, 0.0)


def test_a_request_over_the_start_of_the_period_falls_in_the_period_of_its_last_launch():
    before = _row("l1", SINCE - timedelta(hours=1), outcome="invalid", request_id="q")
    one = _launches(_report([before, _row("l2", request_id="q", request_try=2)]), stage="impl")
    assert (one["launch_count"], one["requests"], one["success_rate"]) == (1, 1, 1.0)


def test_launches_are_broken_down_by_mode_and_filtered_by_stage():
    items = _report([_row("l1", mode="light"), _row("l2", mode="full"), _row("l3", layer="judge", name="ask")], stage="impl")
    assert sorted(i["mode"] for i in items if i["kind"] == "launch_group") == ["full", "light"]


# --- 実験の指標 ---


def test_while_units_are_fewer_than_min_units_the_rate_is_not_shown():
    rows, launches = _units(0, 3, 0.8, 1.0)
    more, more_launches = _units(1, 3, 0.8, 1.0, mode="light")
    base, other = _arms(_report(launches + more_launches, rows + more))
    assert (base["phase"], base["verdict"], base["units"]) == ("burn_in", "short", 3)
    assert "probability" not in base and "件数を集めている" in base["note"] and "件数が足りない" in base["note"]
    assert (other["effort"], other["modes"], base["modes"]) == ("high", {"light": 3}, {})


def test_thirty_units_of_equal_effect_and_cost_are_undecided():
    rows, launches = _units(0, 30, 0.8, 1.0)
    more, more_launches = _units(1, 30, 0.8, 1.0)
    base, other = _arms(_report(launches + more_launches, rows + more))
    assert (base["verdict"], other["verdict"]) == ("undecided", "undecided") and "優劣を判定できない" in base["note"]
    assert base["phase"] == "adaptive" and base["probability"] >= ALLOCATION["floor"]


def test_a_cheaper_arm_of_the_same_effect_rises_and_is_decided():
    rows, launches = _units(0, 30, 0.9, 1.0)
    more, more_launches = _units(1, 30, 0.9, 0.5)
    base, other = _arms(_report(launches + more_launches, rows + more))
    assert other["verdict"] == "decided" and other["leader"] and "leader" not in base
    assert other["probability"] > base["probability"] >= ALLOCATION["floor"]
    assert base["probability"] + other["probability"] == pytest.approx(1)
    assert (other["roi"], other["roi_ratio"]) == (pytest.approx(1.8), pytest.approx(2.0))
    assert base["quality_interval"][0] < 0.9 < base["quality_interval"][1]


def test_a_unit_with_an_unknown_cost_is_left_out_of_the_cost_mean():
    rows, launches = _units(0, 2, 0.8, 2.0)
    more, more_launches = _units(1, 1, 0.8, None)
    base, other = _arms(_report(launches + more_launches, rows + more))
    assert (base["cost_usd"], base["unpriced_units"]) == (2.0, 0)
    assert (other["cost_usd"], other["unpriced_units"], other["units"]) == (None, 1, 1)


def test_an_arm_of_zero_effect_has_a_roi_ratio_of_zero():
    rows, launches = _units(0, 2, 0.8, 1.0)
    more, more_launches = _units(1, 2, 0.0, 1.0)
    base, other = _arms(_report(launches + more_launches, rows + more))
    assert (other["roi"], other["roi_ratio"], base["roi_ratio"]) == (0.0, 0.0, 1.0)


def test_assignments_of_another_repository_are_not_reported():
    rows, launches = _units(0, 2, 0.8, 1.0)
    assert _arms(_report(launches, rows, repo="/other")) == []


def test_an_arm_refused_twice_says_when_it_returns_and_later_refusals_do_not_count():
    rows, launches = _units(0, 2, 0.8, 1.0)
    more, more_launches = _units(1, 2, 0.8, 1.0)
    refusals = [_refusal("a1-0", "x1", ARMS[1], UNTIL - timedelta(days=2)), _refusal("a1-1", "x2", ARMS[1], UNTIL - timedelta(days=1))]
    _, other = _arms(_report(launches + more_launches, rows + more + refusals))
    assert (other["dropped"], other["dropped_until"]) == ("refused", internal.stamp((UNTIL + timedelta(days=5)).timestamp()))
    later = [_refusal("a1-0", "x1", ARMS[1], UNTIL + timedelta(hours=1)), _refusal("a1-1", "x2", ARMS[1], UNTIL + timedelta(hours=2))]
    assert "dropped" not in _arms(_report(launches + more_launches, rows + more + later))[1]


def test_a_cutoff_is_reported_only_while_its_units_are_in_the_window():
    rows, launches = _units(0, 30, 0.9, 1.0)
    more, more_launches = _units(1, 30, 0.1, 1.0)
    assert _arms(_report(launches + more_launches, rows + more))[1]["dropped"] == "cutoff"
    old, old_launches = _units(0, 30, 0.9, 1.0, at=NOW - timedelta(days=100))
    older, older_launches = _units(1, 30, 0.1, 1.0, at=NOW - timedelta(days=100))
    _, other = _arms(_report(old_launches + older_launches, old + older, since=NOW - timedelta(days=120)))
    assert other["units"] == 30 and "dropped" not in other


def test_an_unpriced_model_outside_the_window_does_not_make_it_uniform():
    rows, launches = _units(0, 2, 0.8, 1.0, at=NOW - timedelta(days=100))
    unpriced = internal.write_record(
        "experiment", {"kind": "unpriced", "at": _at(NOW - timedelta(days=100)), "assign_id": "a0-0", "model": "m"}
    )
    base, _ = _arms(_report(launches, [*rows, unpriced], since=NOW - timedelta(days=120)))
    assert base["phase"] != "uniform" and "unpriced_models" not in base
    recent, recent_launches = _units(0, 2, 0.8, 1.0)
    fresh = internal.write_record("experiment", {"kind": "unpriced", "at": _at(NOW), "assign_id": "a0-0", "model": "m"})
    assert _arms(_report(recent_launches, [*recent, fresh]))[0]["unpriced_models"] == ["m"]


def test_a_switch_to_the_base_after_the_period_keeps_the_unit_in_the_period():
    rows, launches = _units(0, 2, 0.8, 1.0)
    more, more_launches = _units(1, 2, 0.8, 1.0)
    later = _refusal("a1-0", "x1", ARMS[1], UNTIL + timedelta(hours=1))
    assert _arms(_report(launches + more_launches, rows + more + [later]))[1]["units"] == 2


def test_the_best_probability_comes_only_from_the_units_of_the_period():
    old, old_launches = _units(0, 30, 0.9, 1.0, at=NOW - timedelta(days=10))
    older, older_launches = _units(1, 30, 0.9, 0.5, at=NOW - timedelta(days=10))
    rows = [*old, *older, _assign("p0", 0), _outcome("p0", 0.9), _assign("p1", 1), _outcome("p1", 0.9)]
    launches = [*old_launches, *older_launches, _row("lp0", assign_id="p0"), _row("lp1", assign_id="p1", effort="high")]
    base, other = _arms(_report(launches, rows, since=NOW - timedelta(hours=1)))
    assert (base["units"], other["units"]) == (1, 1) and "best" not in base and "best" not in other


def test_a_unit_of_another_model_assigned_after_the_period_does_not_remove_a_unit_in_the_period():
    later = UNTIL + timedelta(days=1)
    rows = [_assign("a-old", 0), _outcome("a-old", 0.8), _assign("a-new", 0, later), _outcome("a-new", 0.8, later)]
    launches = [_row("l-old", assign_id="a-old", model="m-old"), _row("l-new", later, assign_id="a-new", model="m-new")]
    base, _ = _arms(_report(launches, rows))
    assert base["units"] == 1


def test_an_arm_without_units_says_no_records():
    rows, launches = _units(0, 2, 0.8, 1.0)
    base, other = _arms(_report(launches, rows))
    assert other["units"] == 0 and other["note"] == "記録なし" and base["units"] == 2


# --- CLI ---


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(mc.launch_approval, "repo_key", lambda root: os.path.realpath(root))
    monkeypatch.delenv("NDF_USAGE_DIR", raising=False)
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".ndf").mkdir()
    (root / ".ndf" / "launch.json").write_text(json.dumps({"version": 1, "experiments": {}}), encoding="utf-8")
    monkeypatch.chdir(root)
    return root


ARGV = ["models", "report", "--since", "2026-11-01", "--until", "2026-11-30"]


def test_report_without_records_says_so(repo, capsys, last_result):
    assert cli.main(ARGV) == 0
    got = last_result(capsys.readouterr().out)
    assert (got["status"], got["items"]) == ("ok", []) and "記録なし" in got["summary"]


def _month_file(store: Path, month: str) -> Path:
    return store / mc.facts.value("ndf-files", mc.LEDGER, "path").replace(mc.MONTH, month)


def test_report_reads_the_records_and_leaves_the_rates_file_alone(repo, capsys, last_result, tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_USAGE_DIR", str(tmp_path / "usage"))  # エントリポイントが形の古い状態の保存先を退避しても残る場所
    rows, launches = _units(0, 2, 0.8, 1.0)
    store = mc.records_dir()
    with mc.ledger(NOW - timedelta(days=60), store) as led:
        for row in rows:
            led.append({**row, "repo": os.path.realpath(repo)} if row["kind"] == "assign" else row)
    with _month_file(store, "2026-11").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"version": 1, "kind": "assign", "at": _at(NOW)}) + "\n")  # 必須の欄の無い行
    path = usage.usage_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    for row in launches:
        jsonl.append(path, row, kind="usage")
    assert cli.main([*ARGV, "--stage", "impl"]) == 0
    got = last_result(capsys.readouterr().out)
    assert [i["kind"] for i in got["items"]] == ["launch_group", "arm", "arm"] and got["metrics"]["usage_rows"] == 2
    assert got["metrics"]["broken_experiment_rows"] == 1
    assert not (store / mc.facts.value("ndf-files", mc.WEIGHTS, "path")).exists()


def test_a_since_before_the_oldest_kept_month_reports_what_is_left_and_says_so(repo, capsys, last_result, monkeypatch, tmp_path):
    monkeypatch.setenv("NDF_USAGE_DIR", str(tmp_path / "usage"))
    monkeypatch.setattr(mc, "utc_now", lambda: datetime(2026, 11, 30, tzinfo=UTC))
    mc.append_row(_assign("a1", 0, datetime(2026, 9, 15, tzinfo=UTC)), mc.records_dir())
    assert cli.main(["models", "report", "--since", "2025-01-01", "--until", "2026-11-30"]) == 0
    got = last_result(capsys.readouterr().out)
    assert got["status"] == "ok" and "2026-09-01 からの月だけが残っている" in got["summary"]
    assert got["metrics"]["kept_since"] == "2026-09-01"


def test_a_since_within_the_kept_period_adds_no_notice(repo, capsys, last_result, monkeypatch):
    monkeypatch.setattr(mc, "utc_now", lambda: datetime(2026, 11, 30, tzinfo=UTC))
    assert cli.main(ARGV) == 0
    got = last_result(capsys.readouterr().out)
    assert "からの月だけが残っている" not in got["summary"] and "kept_since" not in got["metrics"]
