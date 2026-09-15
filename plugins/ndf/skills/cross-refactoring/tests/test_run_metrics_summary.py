"""cross-refactoring の状態の保存と実行の要約（#662 の AC9 / AC14 / AC16 / AC23）。

工程の所要は監視の記録から組み立てる（設計の決定 9）。記録は手で書き、
`refactor_lib.measure` と、状態の保存の差し込み口を通した要約を読む。
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

import pytest

from crossref_helpers import make_state, read_state


@pytest.fixture()
def metrics(tmp_path, monkeypatch):
    base = tmp_path / "metrics"
    monkeypatch.setenv("NDF_METRICS_DIR", str(base))
    monkeypatch.delenv("NDF_METRICS", raising=False)
    return base


@pytest.fixture(scope="session")
def rf_measure(refactor):
    return sys.modules["refactor_lib.measure"]


def _args(state_id=130, **over):
    return type("A", (), {"id": state_id, **over})()


def _stamp(naive: str) -> str:
    return (dt.datetime.fromisoformat(naive).astimezone()
            .astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))


def _launch(stem: str, started: str, ended: str, elapsed: float, reason: str = "ok") -> dict:
    return {"agent": stem.split("-", 1)[0], "stem": stem, "status": "OK", "exit_code": 0,
            "reason": reason, "detail": "err.log の抜粋", "launched_at": started,
            "started_at": started, "ended_at": ended, "elapsed": elapsed,
            "idle_seconds": 0.0, "progress_tail": "", "result_exists": True, "pid": 1}


def _t(minute: int) -> str:
    return f"2026-08-15T01:{minute:02d}:00+00:00"


# ---------- AC9 ----------

def test_start_round_writes_the_summary(refactor, tmp_path, env_tmp_dir, metrics):
    state_path = make_state(tmp_path)
    env_tmp_dir(state_path)

    refactor.cmd_start_round(_args())

    expected = (metrics / "devbasex--ai-plugins"
                / f"cross-refactoring-rf130-{_stamp('2026-08-15T00:00:00')}.json")
    assert sorted(metrics.rglob("*.json")) == [expected]
    summary = json.loads(expected.read_text())
    assert summary["kind"] == "cross-refactoring"
    assert summary["id"] == 130
    assert summary["rounds"][0]["round"] == 1
    assert "kind" in summary["rounds"][0]
    assert "phases" in summary
    # AC14: 群ごとの試行回数は D-C の P6 が足す
    assert "apply_attempts" not in summary


# ---------- AC14 ----------

def test_phases_counts_launches_and_seconds_per_kind(rf_measure):
    state = {
        "final": "no_more_proposals",
        "started_at": "2026-08-15T01:00:00+00:00",
        "ended_at": "2026-08-15T01:59:00+00:00",
        "rounds": [
            {"round": 1, "kind": "test", "started_at": _t(0)},
            {"round": 2, "kind": "structure", "started_at": _t(30)},
        ],
    }
    launches = [
        _launch("codex-propose-rf130-r1", _t(1), _t(3), 120.0),
        _launch("agy-propose-rf130-r1", _t(1), _t(5), 240.0, reason="timeout"),
        _launch("kiro-propose-rf130-r1", _t(1), _t(2), 60.0),
        _launch("claude-apply-r1", _t(6), _t(16), 600.0),
        _launch("codex-judge-test-changes-r1-g1", _t(17), _t(18), 60.0),
        _launch("claude-fix-r1", _t(19), _t(24), 300.0),
        _launch("codex-propose-rf130-r2", _t(31), _t(33), 120.0),
        _launch("kiro-apply-r2", _t(34), _t(44), 600.0),
        _launch("codex-final-fix", _t(50), _t(55), 300.0),
        _launch("codex-review-pr130", _t(56), _t(57), 60.0),  # 知らない形は数えない
    ]

    phases = rf_measure.phases(state, launches)

    test = phases["test"]
    assert test["propose"] == {"launches": 3, "cli_seconds": 420.0,
                               "first_started_at": _t(1), "last_ended_at": _t(5)}
    assert test["apply"]["launches"] == 1
    assert test["judge-test-changes"]["cli_seconds"] == 60.0
    assert test["fix"]["last_ended_at"] == _t(24)
    # ラウンド 1 は 30 分（1800 秒）で、CLI の時間は 1380 秒
    assert test["other_seconds"] == 420
    structure = phases["structure"]
    assert set(structure) == {"propose", "apply", "other_seconds"}
    # ラウンド 2 は全体の終了まで 29 分（1740 秒）で、CLI の時間は 720 秒
    assert structure["other_seconds"] == 1020
    assert phases["final-fix"] == {"launches": 1, "cli_seconds": 300.0,
                                   "first_started_at": _t(50), "last_ended_at": _t(55)}


def test_phases_of_an_unfinished_round_have_no_other_seconds(rf_measure):
    state = {"final": None, "rounds": [{"round": 1, "kind": "test", "started_at": _t(0)}]}
    phases = rf_measure.phases(state, [_launch("codex-propose-rf130-r1", _t(1), _t(3), 120.0)])
    assert phases["test"]["other_seconds"] is None
    assert phases["test"]["propose"]["launches"] == 1


# ---------- AC16 ----------

def test_summary_failure_keeps_exit_code_and_stdout(
        refactor, tmp_path, env_tmp_dir, metrics, monkeypatch, capsys):
    run_metrics = sys.modules["run_metrics"]

    def run(sub: pathlib.Path) -> tuple[int, str, str]:
        state_path = make_state(sub)
        env_tmp_dir(state_path)
        code = 0
        try:
            refactor.cmd_start_round(_args())
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    monkeypatch.setenv("NDF_METRICS", "0")
    expected = run(tmp_path / "a")
    monkeypatch.delenv("NDF_METRICS")

    def boom(*a, **k):
        raise RuntimeError("要約が壊れた")

    monkeypatch.setattr(run_metrics, "build_summary", boom)
    actual = run(tmp_path / "b")

    assert actual[:2] == expected[:2]
    assert "要約が壊れた" in actual[2]
    assert read_state(tmp_path / "b" / ".cross_refactoring" / "cross-refactoring-rf130-state.json")["rounds"]


def test_a_raising_hook_does_not_break_save(tmp_path, capsys, refactor):
    statefile = sys.modules["statefile"]

    def hook(path, state):
        raise RuntimeError("差し込み口が壊れた")

    statefile.register_after_save(hook)
    try:
        statefile.save(tmp_path / "s.json", {"a": 1})
    finally:
        statefile.unregister_after_save(hook)
    assert json.loads((tmp_path / "s.json").read_text()) == {"a": 1}
    assert "差し込み口が壊れた" in capsys.readouterr().err


# ---------- AC23 ----------

def test_report_ends_with_the_summary_path(refactor, tmp_path, env_tmp_dir, metrics, capsys):
    state_path = make_state(tmp_path)
    env_tmp_dir(state_path)

    refactor.cmd_report(_args(metrics=False))

    last = capsys.readouterr().out.rstrip("\n").splitlines()[-1]
    [path] = sorted(metrics.rglob("*.json"))
    assert last == f"計測の要約: {path.resolve()}"


def test_report_with_metrics_still_ends_with_the_summary_line(
        refactor, tmp_path, env_tmp_dir, metrics, monkeypatch, capsys):
    state_path = make_state(tmp_path)
    env_tmp_dir(state_path)
    monkeypatch.setenv("NDF_METRICS", "0")

    refactor.cmd_report(_args(metrics=True))

    last = capsys.readouterr().out.rstrip("\n").splitlines()[-1]
    assert last == "計測の要約: 書いていません（NDF_METRICS=0）"
