"""supervise.py の遅れの見張り: 一次の調査・決まった手・LLM の判定・打ち切り・所要の履歴・history / expected。

claude は NDF_SUPERVISE_CLAUDE の偽物で置き換える。待ちの秒はステップの expected と report_interval で縮める。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SUPERVISE = SCRIPTS / "supervise.py"
PY = sys.executable

spec = importlib.util.spec_from_file_location("supervise_slow", SUPERVISE)
sv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sv)

FAKE_CLAUDE = r'''
import json, os, sys, time
prompt = sys.stdin.read()
log = os.environ["FAKE_CLAUDE_LOG"]
open(log, "a").write(json.dumps({"judge": "## 経過と想定" in prompt}) + "\n")
if os.environ.get("FAKE_CLAUDE_FORBID"):
    open(os.environ["FAKE_CLAUDE_FORBID"], "w").write("called")
if "## 経過と想定" in prompt:
    answer = os.environ.get("FAKE_DECISION", "wait")
    if answer == "garbage":
        text = "よく分からない"
    else:
        text = json.dumps({"decision": answer, "reason": "理由の目印 " + answer, "wait_seconds": 1})
    print(json.dumps({"result": text, "usage": {}, "total_cost_usd": 0.002, "num_turns": 1}))
    sys.exit(0)
counter = os.environ.get("FAKE_LIMIT_ONCE")
if counter and not os.path.exists(counter):
    open(counter, "w").write("1")
    print(json.dumps({"result": "Claude AI usage limit reached|%d" % int(time.time()), "is_error": True}))
    sys.exit(1)
time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "0")))
print(json.dumps({"result": "## 作業の報告\n- 結果: 完了", "usage": {}, "total_cost_usd": 0.01, "num_turns": 1}))
'''

PROBE = r'''
import json, sys
print(json.dumps({"status": "ok", "summary": "調べた目印 " + sys.argv[1],
                  "metrics": {"class": sys.argv[2], "action": sys.argv[1]}}))
'''


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE_CLAUDE)
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE)
    monkeypatch.setenv("NDF_SUPERVISE_CLAUDE", f"{PY} {fake}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude.log"))
    for k in ("FAKE_DECISION", "FAKE_CLAUDE_FORBID", "FAKE_LIMIT_ONCE", "FAKE_CLAUDE_SLEEP",
              "NDF_SUPERVISE_CLAUDE_FALLBACK"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def probe_of(tmp_path, action, cls="c"):
    return {"cmd": f"{PY} {tmp_path / 'probe.py'} {action} {cls}"}


def run_plan(tmp_path, steps, slow=None, **extra):
    plan = {"フェーズ": "試験", "課題": [1102], "作業場所": str(tmp_path), "report_interval": 0.4, "steps": steps,
            "slow": {"history": str(tmp_path / "history.jsonl"), **(slow or {})}, **extra}
    s = sv.Supervisor(plan, tmp_path / "state")
    return s, s.run()


def lines(tmp_path, kind):
    p = tmp_path / "state" / "progress.jsonl"
    return [d for d in map(json.loads, p.read_text().splitlines()) if d["kind"] == kind]


def claude_calls(tmp_path):
    p = tmp_path / "claude.log"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.is_file() else []


def test_probe_runs_when_elapsed_passes_expected(env):
    s, text = run_plan(env, [{"id": "wait", "type": "run", "cmd": "sleep 1", "expected": 0.4,
                              "probe": probe_of(env, "wait", "running"), "next": "end"}], slow={"max_waits": 10})
    assert "結果: 完了" in text
    slow = lines(env, "slow")
    assert slow and slow[0]["probe"]["class"] == "running" and slow[0]["act"] == "wait"
    assert slow[0]["basis"]["source"] == "step" and slow[0]["by"] == "rule"
    assert slow[0]["next_check"] > slow[0]["elapsed"]
    assert not claude_calls(env)
    assert "遅れの調査" in text


def test_builtin_probes_output_and_worker(env, monkeypatch):
    run_plan(env, [{"id": "r", "type": "run", "cmd": "sh -c 'echo a >&2; sleep 1'", "expected": 0.3, "next": "end"}],
             slow={"max_waits": 10})
    assert lines(env, "slow")[0]["probe"]["name"] == "output"
    (env / "state" / "progress.jsonl").unlink()
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "1")
    monkeypatch.setenv("FAKE_DECISION", "wait")
    run_plan(env, [{"id": "w", "type": "work", "prompt": "何か", "expected": 0.3, "next": "end"}])
    assert lines(env, "slow")[0]["probe"]["name"] == "worker"


def test_remedied_is_solved_without_llm_and_tells_conductor(env, monkeypatch):
    forbid = env / "forbidden"
    monkeypatch.setenv("FAKE_CLAUDE_FORBID", str(forbid))
    _, text = run_plan(env, [{"id": "release", "type": "run", "cmd": "sleep 1", "expected": 0.4,
                              "probe": probe_of(env, "remedied", "stale"), "next": "end"}])
    assert "結果: 完了" in text and not forbid.exists()
    att = [a for a in lines(env, "attention") if a["reason"] == "遅れ"]
    assert att and "待ち直す" in att[0]["text"] and "調べた目印 remedied" in att[0]["text"]


def judge_plan(env, decision, monkeypatch, cmd="sleep 3", **step):
    monkeypatch.setenv("FAKE_DECISION", decision)
    return run_plan(env, [{"id": "slow", "type": "run", "cmd": cmd, "expected": 0.3,
                           "probe": probe_of(env, "judge", "unknown"), "next": "end", **step}])


def test_judge_stop_stops_plan_with_reason(env, monkeypatch):
    started = time.time()
    s, text = judge_plan(env, "stop", monkeypatch)
    assert time.time() - started < 2.5
    assert "結果: 止まった" in text and "遅れ: 理由の目印 stop" in text
    slow = lines(env, "slow")[-1]
    assert (slow["by"], slow["act"], slow["llm"]["reason"]) == ("llm", "stop", "理由の目印 stop")
    assert s.results["slow"]["exit"] == 125
    assert any("打ち切った（stop）" in a["text"] for a in lines(env, "attention"))


def test_judge_retry_reruns_step_then_stops_over_max_retry(env, monkeypatch):
    count = env / "count"
    _, text = judge_plan(env, "retry", monkeypatch, cmd=f"sh -c 'echo x >> {count}; sleep 3'")
    assert len(count.read_text().splitlines()) == 2
    assert "結果: 止まった" in text and "retry の上限 1 回を超えた" in text
    acts = [d["act"] for d in lines(env, "slow")]
    assert acts == ["retry", "stop"]


def test_judge_fix_goes_to_on_fail(env, monkeypatch):
    mark = env / "fixed"
    monkeypatch.setenv("FAKE_DECISION", "fix")
    s, text = run_plan(env, [
        {"id": "slow", "type": "run", "cmd": "sleep 3", "expected": 0.3, "probe": probe_of(env, "judge"),
         "on_fail": "after", "next": "end"},
        {"id": "after", "type": "run", "cmd": f"touch {mark}", "next": "end"}])
    assert mark.exists() and "結果: 完了" in text
    assert s.results["slow"]["exit"] == 125 and "理由の目印 fix" in s.results["slow"]["text"]


def test_judge_wait_extends_next_check(env, monkeypatch):
    _, text = judge_plan(env, "wait", monkeypatch, cmd="sleep 1")
    assert "結果: 完了" in text
    first = lines(env, "slow")[0]
    assert (first["act"], first["by"]) == ("wait", "llm") and first["next_check"] > first["elapsed"]
    assert any("判定 wait" in a["text"] for a in lines(env, "attention"))


def test_judge_limit_turns_watch_off(env, monkeypatch):
    _, text = judge_plan(env, "wait", monkeypatch, cmd="sleep 2")
    assert "結果: 完了" in text
    acts = [d["act"] for d in lines(env, "slow")]
    assert acts[-1] == "off" and sum(1 for c in claude_calls(env) if c["judge"]) == 2
    assert any("上限 2 回に達した" in a["text"] for a in lines(env, "attention"))


def test_unreadable_judge_answer_waits(env, monkeypatch):
    _, text = judge_plan(env, "garbage", monkeypatch, cmd="sleep 1")
    assert "結果: 完了" in text
    assert lines(env, "slow")[0]["act"] == "wait"
    assert any("遅れの判定を読めない" in a["text"] for a in lines(env, "attention"))


def test_stop_kills_grandchild(env, monkeypatch):
    pidf = env / "pid"
    judge_plan(env, "stop", monkeypatch, cmd=f"sh -c 'sleep 30 & echo $! > {pidf}; wait'")
    pid = int(pidf.read_text())
    time.sleep(0.2)
    status = Path(f"/proc/{pid}/status")
    alive = status.is_file() and "\nState:\tZ" not in status.read_text()
    assert not alive


def test_history_keeps_only_success_and_gate(env):
    run_plan(env, [{"id": "ok", "type": "run", "cmd": "true", "next": "bad"},
                   {"id": "bad", "type": "run", "cmd": "false", "next": "end"}])
    run_plan(env, [{"id": "gate", "type": "run", "cmd": "sh -c 'exit 10'", "next": "end"}])
    rows = [json.loads(l) for l in (env / "history.jsonl").read_text().splitlines()]
    assert [(r["step"], r["exit"], r["phase"]) for r in rows] == [("ok", 0, "試験"), ("gate", 10, "試験")]


def test_limit_wait_is_not_counted_as_slow(env, monkeypatch):
    monkeypatch.setenv("FAKE_LIMIT_ONCE", str(env / "limited"))
    monkeypatch.setenv("NDF_SUPERVISE_LIMIT_SLEEP", "1")
    _, text = run_plan(env, [{"id": "w", "type": "work", "prompt": "何か", "expected": 0.6, "next": "end"}])
    assert "結果: 完了" in text and (env / "limited").exists()
    assert not lines(env, "slow")


def test_bad_slow_config_stops_before_steps(env):
    mark = env / "ran"
    _, text = run_plan(env, [{"id": "a", "type": "run", "cmd": f"touch {mark}", "next": "end"}],
                       slow={"nope": 1})
    assert "結果: 止まった" in text and "slow の設定が読めない（nope）" in text and not mark.exists()


def cli(*args, cwd=None):
    p = subprocess.run([PY, str(SUPERVISE), *args], capture_output=True, text=True, cwd=cwd)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def test_expected_follows_declaration_and_argument(tmp_path):
    wt = tmp_path / "wt"
    (wt / ".ndf").mkdir(parents=True)
    (wt / ".ndf" / "supervise.json").write_text(json.dumps({"version": 1, "slow": {"factor": 5}}))
    h = tmp_path / "h.jsonl"
    h.write_text("".join(json.dumps({"at": f"t{i}", "phase": "実装", "step": "impl", "type": "work",
                                     "seconds": 200, "exit": 0, "plan": ""}) + "\n" for i in range(3)))
    plan = tmp_path / "p.json"
    plan.write_text(json.dumps({"フェーズ": "実装", "作業場所": str(wt), "steps": [
        {"id": "impl", "type": "work", "prompt": "x"}, {"id": "j", "type": "judge", "question": "q"}]}))
    code, out = cli("expected", str(plan), "--history", str(h))
    assert code == 0 and [i["expected"] for i in out["items"]] == [1000.0]
    _, out = cli("expected", str(plan), "--history", str(h), "--slow", "factor=2")
    assert out["items"][0]["expected"] == 400.0 and out["items"][0]["basis"]["source"] == "history"


def test_history_import_twice_adds_nothing_second_time(tmp_path):
    st = tmp_path / "a-state"
    st.mkdir()
    (tmp_path / "a.json").write_text(json.dumps({"フェーズ": "実装", "steps": []}))
    (st / "progress.jsonl").write_text(json.dumps({"kind": "step", "at": "t", "step": "impl", "type": "work",
                                                   "exit": 0, "seconds": 5}) + "\n")
    h = tmp_path / "h.jsonl"
    code, out = cli("history", "import", str(st / "progress.jsonl"), "--history", str(h))
    assert code == 0 and out["metrics"]["added"] == 1
    _, out = cli("history", "import", str(st / "progress.jsonl"), "--history", str(h))
    assert out["metrics"]["added"] == 0
    code, out = cli("history", "import", str(tmp_path / "none.jsonl"), "--history", str(h))
    assert code == 1 and out["status"] == "stopped"
