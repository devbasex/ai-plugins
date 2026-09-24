"""cross-refactoring の状態の保存と実行の要約（#662 の AC9 / AC14 / AC16 / AC23、#933）。

フェーズの所要は状態の `phases`（進行側の時計）から、CLI の起動回数と合計秒は監視の
記録から組み立てる（#933 の決定 8）。記録は手で書き、`refactor_lib.measure` と、状態の
保存の差し込み口を通した要約を読む。
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

import pytest

from crossref_helpers import git, make_state_v2, read_state


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


def _state(root: pathlib.Path, **over) -> pathlib.Path:
    """版 2 の状態。`start-phase` が HEAD を読むため、書き込み用の作業ディレクトリは git にする。"""
    work = root / "work"
    work.mkdir(parents=True, exist_ok=True)
    git("init", "-q", cwd=work)
    git("-c", "user.email=t@e.st", "-c", "user.name=t", "commit", "-q", "--allow-empty",
        "-m", "init", cwd=work)
    over.setdefault("started_at", "2026-08-15T00:00:00")
    return make_state_v2(root, work, **over)


# ---------- AC9 ----------

def test_start_phase_writes_the_summary(refactor, tmp_path, env_tmp_dir, metrics):
    """状態を保存するサブコマンドが、実行の要約を書く。"""
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)

    refactor.cmd_start_phase(_args(phase="propose"))

    expected = (metrics / "acme--demo"
                / f"cross-refactoring-rf130-{_stamp('2026-08-15T00:00:00')}.json")
    assert sorted(metrics.rglob("cross-refactoring-rf*.json")) == [expected]
    summary = json.loads(expected.read_text())
    assert summary["kind"] == "cross-refactoring"
    assert summary["id"] == 130
    assert summary["budget_minutes"] == 60
    assert summary["implementer"] == "claude"
    assert "phases" in summary
    assert "apply_attempts" not in summary


# ---------- AC14 ----------

def test_phases_counts_launches_and_seconds_per_phase(rf_measure):
    """フェーズの所要は状態から、起動回数と CLI の秒は監視の記録から足す。"""
    state = {"phases": {
        "propose": {"started_at": _t(0), "ended_at": _t(6), "seconds": 360.0},
        "implement": {"started_at": _t(10), "ended_at": _t(25), "seconds": 900.0},
        "verify": {"started_at": _t(25), "ended_at": _t(26), "seconds": 60.0},
        "plan": {"started_at": _t(6)},            # 終わっていないフェーズは所要を置かない
    }}
    launches = [
        _launch("codex-propose-rf130", _t(1), _t(3), 120.0),
        _launch("agy-propose-rf130", _t(1), _t(5), 240.0, reason="timeout"),
        _launch("kiro-propose-rf130", _t(1), _t(2), 60.0),
        _launch("claude-plan-rf130", _t(6), _t(9), 180.0),
        _launch("claude-implement-rf130", _t(10), _t(24), 840.0),
        _launch("claude-judge-test-changes-rf130", _t(24), _t(25), 60.0),
        _launch("claude-fix-rf130", _t(26), _t(28), 120.0),
        _launch("claude-fix-rf130", _t(29), _t(30), 60.0),
        _launch("codex-final-fix", _t(50), _t(55), 300.0),
        _launch("codex-review-pr130", _t(56), _t(57), 60.0),  # 知らない形は数えない
        _launch("claude-apply-r1", _t(58), _t(59), 60.0),     # ラウンド制の形も数えない
    ]

    phases = rf_measure.phases(state, launches)

    assert phases["propose"] == {"seconds": 360.0, "launches": 3, "cli_seconds": 420.0}
    assert phases["plan"] == {"launches": 1, "cli_seconds": 180.0}
    assert phases["implement"] == {"seconds": 900.0, "launches": 1, "cli_seconds": 840.0}
    assert phases["verify"] == {"seconds": 60.0}
    assert phases["fix"] == {"launches": 2, "cli_seconds": 180.0}
    assert phases["judge-test-changes"]["launches"] == 1
    assert phases["final-fix"] == {"launches": 1, "cli_seconds": 300.0}
    assert set(phases) == {"propose", "plan", "implement", "verify", "fix",
                           "judge-test-changes", "final-fix"}


def test_phases_without_records_or_launches_are_absent(rf_measure):
    """測っていないフェーズを 0 で置かない。"""
    assert rf_measure.phases({"phases": {}}, []) == {}
    assert rf_measure.phases({}, [_launch("codex-propose-rf130", _t(1), _t(3), 120.0)]) == {
        "propose": {"launches": 1, "cli_seconds": 120.0}}


def test_the_summary_extra_carries_the_budget_and_the_implementer(rf_measure, tmp_path):
    state = {"budget_minutes": 45, "implementer": "codex",
             "phases": {"propose": {"seconds": 12.0}}}
    extra = rf_measure.summary_extra(tmp_path / "s.json", state, [])
    assert extra == {"phases": {"propose": {"seconds": 12.0}},
                     "budget_minutes": 45, "implementer": "codex"}


# ---------- AC16 ----------

def test_summary_failure_keeps_exit_code_and_stdout(
        refactor, tmp_path, env_tmp_dir, metrics, monkeypatch, capsys):
    run_metrics = sys.modules["run_metrics"]

    def run(sub: pathlib.Path) -> tuple[int, str, str]:
        state_path = _state(sub)
        env_tmp_dir(state_path)
        code = 0
        try:
            refactor.cmd_start_phase(_args(phase="propose"))
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
    state = read_state(tmp_path / "b" / ".cross_refactoring" / "cross-refactoring-rf130-state.json")
    assert state["phases"]["propose"]["started_at"]


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
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)

    refactor.cmd_report(_args(metrics=False))

    last = capsys.readouterr().out.rstrip("\n").splitlines()[-1]
    [path] = sorted(metrics.rglob("*.json"))
    assert last == f"計測の要約: {path.resolve()}"


def test_report_with_metrics_still_ends_with_the_summary_line(
        refactor, tmp_path, env_tmp_dir, metrics, monkeypatch, capsys):
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    monkeypatch.setenv("NDF_METRICS", "0")

    refactor.cmd_report(_args(metrics=True))

    last = capsys.readouterr().out.rstrip("\n").splitlines()[-1]
    assert last == "計測の要約: 書いていません（NDF_METRICS=0）"
