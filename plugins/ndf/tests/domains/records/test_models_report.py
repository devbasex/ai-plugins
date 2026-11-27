"""#2944: `ndf models report`（使用量ログと実験の記録の集計）。

使用量ログと実験の記録の行は、usage.schema.json・experiment.schema.json の形の見本で作り、書く前に形を確かめる。
集計（`report_rows`）は直接呼び、CLI は `main([...])` の結果 JSON を確かめる。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
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
    return when.isoformat(timespec="microseconds")


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


def _units(arm: int, count: int, quality: float, cost: float | None, mode: str | None = None) -> tuple[list, list]:
    """組 `arm` の単位を `count` 個（割り付け・効果指標・起動 1 回ずつ）。"""
    rows, launches = [], []
    for i in range(count):
        aid, when = f"a{arm}-{i}", NOW - timedelta(minutes=i)
        rows += [_assign(aid, arm, when, mode), _outcome(aid, quality, when)]
        launches.append(_row(f"l{arm}-{i}", when, cost, assign_id=aid, **({"effort": "high"} if arm else {})))
    return rows, launches


def _report(usage_rows=(), rows=(), stage=None) -> list[dict]:
    return mo.report_rows(list(usage_rows), list(rows), SINCE, UNTIL, stage, ALLOCATION)


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
    error = internal.write_record("experiment", {"kind": "launch_error", "at": _at(NOW), "assign_id": "a1", "stage": "impl",
                                                 "runtime": "claude", "arm": {"effort": "high"}, "reason": "404", "switched": True})  # fmt: skip
    items = _report([refused, base], [_assign("a1", 1), error])
    assert (_launches(items, effort=None)["requests"], _launches(items, effort=None)["success_rate"]) == (1, 1.0)
    assert (_launches(items, effort="high")["launch_count"], _launches(items, effort="high")["requests"]) == (1, 0)


def test_a_request_whose_launches_were_all_refused_is_a_failure_of_the_last_launch():
    refused = _row("l1", outcome="stopped", effort="high", assign_id="a1")
    error = internal.write_record("experiment", {"kind": "launch_error", "at": _at(NOW), "assign_id": "a1", "stage": "impl",
                                                 "runtime": "claude", "arm": {"effort": "high"}, "reason": "404", "switched": True})  # fmt: skip
    one = _launches(_report([refused], [_assign("a1", 1), error]), effort="high")
    assert (one["requests"], one["success_rate"]) == (1, 0.0)


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


def test_report_reads_the_records_and_leaves_the_rates_file_alone(repo, capsys, last_result, tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_USAGE_DIR", str(tmp_path / "usage"))  # エントリポイントが形の古い状態の保存先を退避しても残る場所
    rows, launches = _units(0, 2, 0.8, 1.0)
    store = mc.records_dir()
    with mc.ledger(NOW - timedelta(days=60), store) as led:
        for row in rows:
            led.append(row)
    path = usage.usage_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    for row in launches:
        jsonl.append(path, row, kind="usage")
    assert cli.main([*ARGV, "--stage", "impl"]) == 0
    got = last_result(capsys.readouterr().out)
    assert [i["kind"] for i in got["items"]] == ["launch_group", "arm", "arm"] and got["metrics"]["usage_rows"] == 2
    assert not (store / mc.facts.value("ndf-files", mc.WEIGHTS, "path")).exists()
