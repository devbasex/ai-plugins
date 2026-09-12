"""効果の測定（`scripts/measure.py`）のテスト（#156 の 4 本目）。

設計は `issues/issue-156-pr4-design.md`、計画は `issues/issue-156-pr4-plan.md` にある。
**測定は状態ファイルを読むだけで、GitHub へ問い合わせない。**
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest


_MEASURE = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "measure.py"


def _state(**overrides) -> dict:
    """最小の状態ファイル。必要な項目だけを上書きして使う。"""
    state = {
        "started_at": "2026-05-23T00:00:00+00:00",
        "ended_at": "2026-05-23T01:10:00+00:00",
        "current_pr": 123,
        "pr_history": [{"pr": 123, "opened_at": "...", "closed_at": None, "rounds": 1}],
        "rounds": [],
        "review_findings": [],
        "evidence_rounds": [],
        "final": "approved",
    }
    state.update(overrides)
    return state


def _round(round_no: int, pr: int = 123, **overrides) -> dict:
    rec = {"round": round_no, "pr": pr, "reviewers": ["codex", "agy"]}
    rec.update(overrides)
    return rec


# ---------- 受け入れ条件 13: 費用（ラウンド数・起動回数・実時間）が並ぶ ----------


def test_cost_counts_rounds_launches_and_wall_clock(measure_mod):
    """費用はローテーション全体の合計である。"""
    st = _state(rounds=[_round(1), _round(2), _round(3)])

    cost = measure_mod.measure(st)["cost"]

    assert cost["rounds"] == 3
    assert cost["reviewer_launches"] == 6
    # 00:00:00 → 01:10:00 は 4200 秒。
    assert cost["wall_clock_seconds"] == 4200


def test_reviewer_launches_falls_back_to_recorded_agents(measure_mod):
    """`reviewers` を持たない古い記録では、結果を残した担当の数を数える。"""
    st = _state(rounds=[
        {"round": 1, "pr": 123,
         "codex": {"intent": "REQUEST_CHANGES"}, "agy": {"intent": "APPROVE"}},
        {"round": 2, "pr": 123, "codex": {"intent": "APPROVE"}},
    ])

    assert measure_mod.measure(st)["cost"]["reviewer_launches"] == 3


def test_identity_keys_report_state_file_key_and_all_prs(measure_mod):
    """`pr` は状態ファイルの鍵で、`prs` は `pr_history[]` の順に全件を持つ。"""
    st = _state(
        current_pr=124,
        pr_history=[{"pr": 123, "rounds": 2}, {"pr": 124, "rounds": 1}],
        rounds=[_round(1, pr=123), _round(2, pr=123), _round(3, pr=124)],
    )

    result = measure_mod.measure(st)

    assert result["pr"] == 123
    assert result["prs"] == [123, 124]
    assert result["rounds"] == 3


# ---------- 受け入れ条件 14: 収束の様子（終わり方・振動・上限の到達） ----------


@pytest.mark.parametrize(
    "final,oscillation,max_rounds",
    [
        ("approved", 0, 0),
        ("max_rounds", 0, 1),
        ("oscillation", 1, 0),
        ("error", 0, 0),
    ],
)
def test_convergence_reports_final_and_flags(measure_mod, final, oscillation, max_rounds):
    """**0 か 1 で出す。** 複数を比べるときに測る側が足せるようにするため。"""
    st = _state(final=final, rounds=[_round(1)])

    convergence = measure_mod.measure(st)["convergence"]

    assert convergence["final"] == final
    assert convergence["oscillation"] == oscillation
    assert convergence["max_rounds"] == max_rounds


# ---------- 受け入れ条件 15: 記録が無いときに落ちない ----------


def test_empty_state_does_not_crash(measure_mod):
    """空の状態ファイルでもキーが欠けない。"""
    result = measure_mod.measure({})

    assert result["pr"] is None
    assert result["prs"] == []
    assert result["rounds"] == 0
    assert result["cost"] == {
        "rounds": 0, "reviewer_launches": 0, "wall_clock_seconds": None}
    assert result["convergence"] == {"final": None, "oscillation": 0, "max_rounds": 0}
    assert "methods" in result


def test_wall_clock_is_null_while_the_run_has_not_ended(measure_mod):
    """終わっていない実行では実時間を出さない。**0 で埋めない。**"""
    st = _state(rounds=[_round(1)])
    del st["ended_at"]

    assert measure_mod.measure(st)["cost"]["wall_clock_seconds"] is None


# ---------- 呼び出し方 ----------


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_MEASURE), *args],
        capture_output=True, text=True, check=False,
    )


def test_cli_writes_json_to_stdout(tmp_path):
    state_file = tmp_path / "cross-review-pr123-state.json"
    state_file.write_text(json.dumps(_state(rounds=[_round(1)])), encoding="utf-8")

    proc = _run([str(state_file)])

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["pr"] == 123


def test_cli_writes_json_to_output_file(tmp_path):
    state_file = tmp_path / "cross-review-pr123-state.json"
    state_file.write_text(json.dumps(_state(rounds=[_round(1)])), encoding="utf-8")
    out = tmp_path / "measure.json"

    proc = _run([str(state_file), "--output", str(out)])

    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["cost"]["rounds"] == 1


def test_cli_fails_when_the_state_file_is_missing(tmp_path):
    proc = _run([str(tmp_path / "absent.json")])

    assert proc.returncode != 0
    assert "absent.json" in proc.stderr
