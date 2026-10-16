"""supervise の実行が見積もりの実績の記録を書く（#1856 の F5・受け入れ条件 8）。"""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import estimate_records as er  # noqa: E402
from supervise_lib import actuals, engine  # noqa: E402


def _rows():
    return er.read_actuals(Path(os.environ["NDF_ESTIMATE_DIR"]))


def _plan(tmp_path, cmd):
    return {"フェーズ": "実装", "課題": [42], "作業場所": str(tmp_path), "steps": [{"id": "a", "type": "run", "cmd": cmd, "next": "end"}]}


def test_success_writes_start_step_end(tmp_path):
    text = engine.Engine(_plan(tmp_path, "true"), tmp_path / "state", plan_path=str(tmp_path / "p.json")).run()
    assert "結果: 完了" in text
    kinds = [r["kind"] for r in _rows()]
    assert kinds == ["run_start", "step", "run_end"]
    start, step, end = _rows()
    assert (start["issues"], start["phase"], start["attempt"], start["imported"]) == ([42], "実装", 1, False)
    assert step["run"] == start["run"] == end["run"] and step["exit"] == 0
    assert step["cost_usd"] == 0.0  # LLM を呼ばない run のステップは 0（費用の不明とは分ける）
    assert end["outcome"] == "成功" and set(end["config"]) == set(er.CONFIG_KEYS)
    assert not (tmp_path / "state" / actuals.RUN_FILE).exists()


def test_failure_is_recorded_with_outcome(tmp_path):
    text = engine.Engine(_plan(tmp_path, "false"), tmp_path / "state", plan_path=str(tmp_path / "p.json")).run()
    assert "結果: 完了" not in text
    assert _rows()[-1]["outcome"] == "失敗"
    table = er.run_table(_rows(), alive=lambda r: False)
    assert [r.outcome for r in table.values()] == ["失敗"]


def test_cancel_writes_cancelled_and_passes_on(tmp_path, monkeypatch):
    eng = engine.Engine(_plan(tmp_path, "true"), tmp_path / "state", plan_path=str(tmp_path / "p.json"))
    eng.actuals.start(None)
    seen = []
    handler = actuals._cancel_handler(signal.SIGTERM, lambda s, f: seen.append(s))
    handler(signal.SIGTERM, None)
    assert seen == [signal.SIGTERM]
    assert _rows()[-1]["outcome"] == "取り消し"


def test_resume_after_cancel_continues_the_same_run(tmp_path):
    state = tmp_path / "state"
    eng = engine.Engine(_plan(tmp_path, "true"), state, plan_path=str(tmp_path / "p.json"))
    eng.actuals.start(None)
    actuals._cancel_handler(signal.SIGTERM, lambda s, f: None)(signal.SIGTERM, None)
    # 再開: 耐久ワークフローは prepare_step を再生し、start を呼ばずに残りのステップを流す
    again = engine.Engine(_plan(tmp_path, "true"), state, plan_path=str(tmp_path / "p.json"))
    again.actuals.step({"id": "a", "type": "run", "exit": 0, "seconds": 1.0})
    again.actuals.end("完了")
    rows = _rows()
    assert [r["kind"] for r in rows] == ["run_start", "run_end", "step", "run_end"]
    assert len({r["run"] for r in rows}) == 1
    table = er.run_table(rows, alive=lambda r: False)
    assert [(r.outcome, len(r.steps)) for r in table.values()] == [("成功", 1)]
    assert not (state / actuals.RUN_FILE).exists()


def test_unwritable_records_do_not_change_result(tmp_path, monkeypatch):
    blocker = tmp_path / "f"
    blocker.write_text("x")
    monkeypatch.setenv("NDF_ESTIMATE_DIR", str(blocker / "sub"))
    text = engine.Engine(_plan(tmp_path, "true"), tmp_path / "state", plan_path=str(tmp_path / "p.json")).run()
    assert "結果: 完了" in text
    lines = [json.loads(l) for l in (tmp_path / "state" / "progress.jsonl").read_text().splitlines()]
    assert any(l.get("kind") == "estimate_record" and l.get("ok") is False for l in lines)


# --- 実装の上限（受け入れ条件 7） ---


def _impl_history(seconds_list, config=None, exits=None):
    d = Path(os.environ["NDF_ESTIMATE_DIR"])
    for i, sec in enumerate(seconds_list):
        code = (exits or {}).get(i, 0)
        run = f"r{i}"
        at = f"2026-10-0{i + 1}T00:00:00+00:00"
        er.append_actual(
            d,
            {
                "kind": "run_start",
                "at": at,
                "run": run,
                "plan": "/p.json",
                "issues": [i],
                "phase": "実装",
                "from": None,
                "attempt": 1,
                "imported": False,
            },
        )
        er.append_actual(
            d, {"kind": "step", "at": at, "run": run, "step": "impl", "type": "work", "exit": code, "seconds": sec, "retry": 0}
        )
        er.append_actual(d, {"kind": "run_end", "at": at, "run": run, "outcome": "成功", "wall_seconds": sec, "config": config or {}})


def _ns(**kw):
    import argparse

    return argparse.Namespace(**{"impl_timeout": None, "impl_timeout_source": None, "worktree": ".", **kw})


def test_impl_timeout_from_history_uses_max_with_margin():
    from supervise_lib.verify_steps import impl_timeout_estimate, with_margin

    _impl_history([1000, 5000, 3000])
    timeout, est = impl_timeout_estimate(_ns())
    assert timeout == with_margin(5000) and est["source"] == "history" and est["samples"] == 3


def test_impl_timeout_does_not_grow_on_a_timed_out_max():
    from supervise_lib.verify_steps import impl_timeout_estimate

    _impl_history([1000, 3960, 3000], exits={1: 124})  # 打ち切り（124）の秒は前の上限と同じ値
    timeout, est = impl_timeout_estimate(_ns())
    assert (timeout, est["source"]) == (3960, "history") and "timed_out" not in est


def test_import_skips_runs_supervise_already_recorded(tmp_path):
    from estimate_import import import_supervise_progress

    plan = _plan(tmp_path, "true")
    (tmp_path / "p.json").write_text(json.dumps(plan))
    engine.Engine(plan, tmp_path / "p-state", plan_path=str(tmp_path / "p.json")).run()
    got = import_supervise_progress(tmp_path, Path(os.environ["NDF_ESTIMATE_DIR"]))
    assert (got["added"], got["skipped"]) == (0, 1)
    assert [r["kind"] for r in _rows()] == ["run_start", "step", "run_end"]


def test_impl_timeout_uncalibrated_without_history():
    from supervise_lib.verify_steps import impl_timeout_estimate

    _impl_history([1000, 2000])
    timeout, est = impl_timeout_estimate(_ns())
    assert (timeout, est["source"]) == (3600, "uncalibrated")


def test_impl_timeout_argument_and_declaration_win():
    from supervise_lib.verify_steps import impl_timeout_estimate

    _impl_history([1000, 5000, 3000])
    assert impl_timeout_estimate(_ns(impl_timeout=1234, impl_timeout_source="arg")) == (
        1234,
        {"source": "arg", "samples": 0, "high": None, "basis": []},
    )
    assert impl_timeout_estimate(_ns(impl_timeout=99, impl_timeout_source="decl"))[1]["source"] == "decl"


def test_impl_timeout_window_and_min_samples_from_declaration(tmp_path):
    from supervise_lib.verify_steps import impl_timeout_estimate

    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "estimate.json").write_text('{"window": 2, "min_samples": 2}')
    _impl_history([9000, 1000, 2000])
    timeout, est = impl_timeout_estimate(_ns(worktree=str(tmp_path)))
    assert (est["source"], est["samples"], est["high"]) == ("history", 2, 2000)


def test_impl_timeout_does_not_drop_below_default():
    from supervise_lib.verify_steps import IMPL_TIMEOUT_DEFAULT, impl_timeout_estimate

    _impl_history([300, 200, 250])
    timeout, est = impl_timeout_estimate(_ns())
    assert (timeout, est["source"]) == (IMPL_TIMEOUT_DEFAULT, "history")


def test_step_cost_is_unknown_when_a_call_had_no_cost():
    assert actuals._step_cost_usd("work", {"cost": 0.0, "cost_unknown": True}) is None
    assert actuals._step_cost_usd("work", {"cost": 0.12}) == 0.12


def test_add_usage_marks_a_call_without_cost(tmp_path):
    from supervise_lib.state import RunState

    st = RunState.__new__(RunState)
    st.llm = {"claude": 0, "input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "cost": 0.0}
    st.cur = {}
    st.add_usage("claude", {"usage": {}, "cost": 0.5})
    assert st.cur["llm"]["cost_unknown"] is False
    st.add_usage("claude", {"usage": {}})
    assert st.cur["llm"]["cost_unknown"] is True and st.cur["llm"]["cost"] == 0.5


def test_launch_installs_cancel_handler_on_main_thread(monkeypatch):
    from supervise_lib import flow

    called = []
    monkeypatch.setattr(actuals, "install_cancel_handler", lambda: called.append(1))
    monkeypatch.setattr(flow.durable, "launch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop")))
    try:
        flow.launch_queue(Path("/tmp/done"), 1, {}, False)
    except RuntimeError:
        pass
    assert called == [1]
