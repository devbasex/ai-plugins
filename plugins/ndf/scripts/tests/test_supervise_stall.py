"""実装のステップの無進捗の打ち切り（#2505 の受け入れ条件「実装のステップ」、不変条件 I5・I6）。

`ProgressWatch` は手で進める時計で確かめ、結合は NDF_SUPERVISE_CLAUDE の偽物と短い許容時間で流す。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
PY = sys.executable
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import monitor_loop  # noqa: E402
from supervise_lib import engine, paths, progress_watch, worker_steps  # noqa: E402
from supervise_lib.progress_watch import ProgressWatch  # noqa: E402


class Signals:
    """手で進める時計と 3 つの信号。"""

    def __init__(self, tmp: Path):
        self.t, self.lines, self.commits = 1000.0, 0, 5
        self.session = {"id": "s1", "config": str(tmp)}
        self.record = tmp / "projects" / "-r" / "s1.jsonl"
        self.record.parent.mkdir(parents=True)

    def grow(self):
        with open(self.record, "a") as f:
            f.write("{}\n")

    def watch(self, stall=1800, hard=None):
        return ProgressWatch(stall, self.session, lambda: self.lines, lambda: self.commits, hard, lambda: self.t)


@pytest.fixture
def sig(tmp_path):
    return Signals(tmp_path)


def advance(sig, w, seconds, step=60, touch=None):
    """`seconds` 秒を `step` 秒ずつ進め、最後の締め切りを返す。"""
    got = w.deadline(1000.0)
    for _ in range(int(seconds / step)):
        sig.t += step
        if touch:
            touch()
        got = w.deadline(1000.0)
    return got


def test_a_growing_transcript_keeps_moving_the_deadline_past_an_hour(sig):
    sig.grow()
    w = sig.watch()
    deadline = advance(sig, w, 7200, touch=sig.grow)
    assert deadline == sig.t + 1800 and w.last_signal == "transcript"


def test_a_silent_step_keeps_its_deadline(sig):
    sig.grow()
    w = sig.watch()
    assert advance(sig, w, 600) == 1000.0 + 1800 and w.last_signal == "start"


@pytest.mark.parametrize("attr, signal", [("lines", "progress_log"), ("commits", "commit")])
def test_a_worker_line_and_a_commit_count_as_progress(sig, attr, signal):
    sig.grow()
    w = sig.watch()
    advance(sig, w, 300)
    setattr(sig, attr, getattr(sig, attr) + 1)
    sig.t += 60
    assert w.deadline(1000.0) == sig.t + 1800 and w.last_signal == signal


def test_without_a_transcript_the_longer_allowance_is_used(sig):
    w = sig.watch()
    assert advance(sig, w, 120) == 1000.0 + progress_watch.BLIND_STALL
    assert not w.observable and w.allowance == progress_watch.BLIND_STALL
    sig.lines += 1
    sig.t += 60
    assert w.deadline(1000.0) == sig.t + progress_watch.BLIND_STALL


def test_an_elapsed_limit_wins_over_progress_and_is_not_a_stall(sig):
    sig.grow()
    w = sig.watch(hard=3000)
    assert w.first_timeout() == 1800
    assert advance(sig, w, 3000, touch=sig.grow) == 1000.0 + 3000
    assert not w.stalled()


def test_a_restarted_call_counts_from_its_own_start(sig):
    sig.grow()
    w = sig.watch()
    advance(sig, w, 600)
    sig.t += 60
    assert w.deadline(sig.t) == sig.t + 1800 and w.stalled()


FAKE_CLAUDE = r"""
import json, os, pathlib, sys, time
argv = sys.argv[1:]
prompt = sys.stdin.read()
open(os.environ["FAKE_CLAUDE_LOG"], "a").write(json.dumps({"judge": "## 経過と想定" in prompt, "argv": argv}) + "\n")
sid = argv[argv.index("--session-id") + 1] if "--session-id" in argv else ""
path = pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "-r" / f"{sid}.jsonl"
path.parent.mkdir(parents=True, exist_ok=True)
for i in range(int(os.environ.get("FAKE_TICKS", "20"))):
    if i == 0 or os.environ.get("FAKE_GROW") == "1":
        with open(path, "a") as f:
            f.write("{}\n")
    time.sleep(0.1)
print(json.dumps({"result": "## 作業の報告\n- 結果: 完了", "usage": {}, "total_cost_usd": 0.01, "num_turns": 1, "session_id": sid}))
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE_CLAUDE)
    monkeypatch.setenv("NDF_SUPERVISE_CLAUDE", f"{PY} {fake}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude.log"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    for k in ("FAKE_GROW", "FAKE_TICKS", "NDF_SUPERVISE_CLAUDE_FALLBACK"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def run_plan(tmp: Path, **step):
    plan = {
        "フェーズ": "試験",
        "課題": [2505],
        "作業場所": str(tmp),
        "report_interval": 0.2,
        "steps": [{"id": "impl", "type": "work", "kind": "実装", "prompt": "直す", "next": "end", **step}],
        "slow": {"history": str(tmp / "history.jsonl")},
    }
    s = engine.Engine(plan, tmp / "state")
    return s, s.run()


def rows(tmp: Path, kind: str) -> list[dict]:
    return [d for d in map(json.loads, (tmp / "state" / "progress.jsonl").read_text().splitlines()) if d["kind"] == kind]


def calls(tmp: Path) -> list[dict]:
    return [json.loads(x) for x in (tmp / "claude.log").read_text().splitlines()]


def test_a_worker_that_keeps_progressing_outlives_the_allowance(env, monkeypatch):
    monkeypatch.setenv("FAKE_GROW", "1")
    monkeypatch.setenv("FAKE_TICKS", "50")
    s, text = run_plan(env, stall=3, expected=0.3)
    assert "結果: 完了" in text and not rows(env, "stalled")
    assert s.state.log[0]["seconds"] > 3
    argv = calls(env)[0]["argv"]
    assert "--no-session-persistence" not in argv and "--session-id" in argv
    assert not rows(env, "slow") and not any(c["judge"] for c in calls(env))  # 遅れの見張りは止めない


def test_a_silent_worker_is_cut_and_the_reason_is_kept(env, monkeypatch):
    monkeypatch.setenv("FAKE_TICKS", "100")
    s, _ = run_plan(env, stall=1)
    row = rows(env, "stalled")
    # 偽の claude は起動の直後に 1 行だけ書く。読む前か後かは負荷で変わるので、最後の進みはどちらの形も受ける
    assert len(row) == 1 and row[0]["step"] == "impl" and row[0]["stall"] == 1 and row[0]["last_signal"] in ("start", "transcript")
    assert s.state.log[0]["stalled"]["last_signal"] == row[0]["last_signal"] and s.state.log[0]["exit"] != 0


def test_an_elapsed_limit_stops_a_progressing_worker_without_a_stalled_row(env, monkeypatch):
    monkeypatch.setenv("FAKE_GROW", "1")
    monkeypatch.setenv("FAKE_TICKS", "100")
    s, _ = run_plan(env, stall=5, timeout=1)
    assert not rows(env, "stalled") and "stalled" not in s.state.log[0] and s.state.log[0]["exit"] != 0


def test_a_step_without_stall_keeps_the_elapsed_timeout(env, monkeypatch):
    monkeypatch.setenv("FAKE_TICKS", "100")
    s, _ = run_plan(env, timeout=1)
    assert "--no-session-persistence" in calls(env)[0]["argv"] and not rows(env, "stalled")


class ExternalStep(worker_steps.WorkStep):
    def mvv_system(self, ctx) -> str:
        return ""


def external_ctx(tmp: Path):
    written: list[dict] = []
    state = SimpleNamespace(cur={}, dir=tmp, every=0.05, progress_write=written.append)
    return SimpleNamespace(state=state, tick=lambda: None, plan_path=str(tmp / "p.json")), written


def fake_external(tmp: Path, monkeypatch, result: str) -> Path:
    log = tmp / "argv.json"
    script = tmp / "external.py"
    body = {"status": "ok" if result == "ok" else "stopped", "summary": result, "items": [{"result": result}]}
    script.write_text(f"import json, sys\nopen({str(log)!r}, 'w').write(json.dumps(sys.argv[1:]))\nprint(json.dumps({body!r}))\n")
    monkeypatch.setattr(paths, "EXTERNAL_AI", script)
    return log


def test_an_external_runtime_gets_the_allowance_and_no_elapsed_limit(tmp_path, monkeypatch):
    log = fake_external(tmp_path, monkeypatch, "ok")
    ctx, written = external_ctx(tmp_path)
    res = ExternalStep().call_worker(ctx, {"id": "impl", "runtime": "codex", "stall": 900}, "直す", str(tmp_path), "impl")
    argv = json.loads(log.read_text())
    assert res["ok"] and argv[argv.index("--timeout") + 1] == "0" and argv[argv.index("--stall-timeout") + 1] == "900"
    assert not written


def test_an_external_runtime_with_a_limit_passes_it(tmp_path, monkeypatch):
    log = fake_external(tmp_path, monkeypatch, "ok")
    ctx, _ = external_ctx(tmp_path)
    ExternalStep().call_worker(ctx, {"id": "impl", "runtime": "codex", "stall": 900, "timeout": 5000}, "直す", str(tmp_path), "impl")
    argv = json.loads(log.read_text())
    assert argv[argv.index("--timeout") + 1] == "5000"


@pytest.mark.parametrize("result, stalled", [("stalled", True), ("timeout", False)])
def test_an_external_stall_is_told_apart_from_a_timeout(tmp_path, monkeypatch, result, stalled):
    fake_external(tmp_path, monkeypatch, result)
    ctx, written = external_ctx(tmp_path)
    before = time.time()
    res = ExternalStep().call_worker(ctx, {"id": "impl", "runtime": "codex", "stall": 900}, "直す", str(tmp_path), "impl")
    assert not res["ok"] and ("stalled" in ctx.state.cur) is stalled and bool(written) is stalled
    if stalled:
        assert written[0]["kind"] == "stalled" and written[0]["last_signal"] == "monitor_log"
        assert ctx.state.cur["stalled"]["seconds"] >= 900 - 1 and time.time() - before < 60


def test_monitor_timeout_zero_means_no_elapsed_limit():
    assert monitor_loop._timeout_outcome(10**9, 0, True, 1) is None
