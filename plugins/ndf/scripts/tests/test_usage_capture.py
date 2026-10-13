"""起動の共有処理での使用量の記録（#1953）。偽の CLI を起動し、帳簿（NDF_USAGE_DIR は conftest が一時ディレクトリへ向ける）を見る。"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
LAUNCH = SCRIPTS / "lib" / "launch-cli.sh"
sys.path.insert(0, str(SCRIPTS / "lib"))
import usage_capture  # noqa: E402
import usage_ledger  # noqa: E402

SECRET = "sk-SECRET-VALUE-1953"
PROMPT = "PROMPT-BODY-1953"
SID = "0199aaaa-bbbb-7ccc-8ddd-eeeeffff0000"
CLAUDE_OUT = {
    "result": f"答え {PROMPT}",
    "usage": {
        "input_tokens": 3,
        "output_tokens": 40,
        "cache_read_input_tokens": 500,
        "cache_creation_input_tokens": 300,
        "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 200},
    },
    "modelUsage": {"claude-opus-5-5": {"inputTokens": 3, "outputTokens": 40, "costUSD": 0.05}},
    "total_cost_usd": 0.05,
    "session_id": "sess-c",
}


def ledger(runtime: str) -> list[dict]:
    return usage_ledger.read_usage_ledger(runtime, usage_ledger.ledger_dir())


def write_cli(bin_dir: Path, name: str, body: str) -> None:
    p = bin_dir / name
    p.write_text("#!/usr/bin/env python3\nimport json, os, sys, time, pathlib\n" + body)
    p.chmod(0o755)


@pytest.fixture
def fake_clis(tmp_path, monkeypatch):
    """4 つのランタイムの偽の CLI。codex は rollout を、kiro はセッションの記録を残す。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    home = tmp_path / "home"
    (home / ".kiro" / "sessions" / "cli").mkdir(parents=True)
    write_cli(bin_dir, "claude", f"sys.stdin.read()\nprint(json.dumps({CLAUDE_OUT!r}))\n")
    rollout = {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {"total_token_usage": {"input_tokens": 1200, "cached_input_tokens": 800, "output_tokens": 30, "total_tokens": 1230}},
        },
    }
    turn = {"type": "turn_context", "payload": {"model": "gpt-test"}}
    write_cli(
        bin_dir,
        "codex",
        "sys.stdin.read()\n"
        f"d = pathlib.Path(os.environ['CODEX_HOME']) / 'sessions' / '2026' / '10' / '13'\n"
        "d.mkdir(parents=True, exist_ok=True)\n"
        f"(d / 'rollout-2026-10-13T00-00-00-{SID}.jsonl').write_text(json.dumps({turn!r}) + '\\n' + json.dumps({rollout!r}) + '\\n')\n"
        f"print('session id: {SID}', file=sys.stderr)\nprint('tokens used\\n1,230', file=sys.stderr)\nprint('done')\n",
    )
    write_cli(
        bin_dir,
        "kiro-cli",
        "sys.stdin.read()\n"
        "now = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime()) + '.5Z'\n"
        "meta = [{'model': 'auto', 'metering_usage': [{'value': 0.25, 'unit': 'credit'}, {'value': 0.5, 'unit': 'credit'}]}]\n"
        "s = {'session_id': 'k1', 'cwd': os.getcwd(), 'created_at': now, 'session_state': {'conversation_metadata': "
        "{'user_turn_metadatas': meta}, 'rts_model_state': {'model_info': {'model_id': 'auto'}}}}\n"
        "(pathlib.Path(os.environ['HOME']) / '.kiro' / 'sessions' / 'cli' / 'k1.json').write_text(json.dumps(s))\n"
        "print('kiro done')\n",
    )
    agy_out = {
        "conversation_id": "c1",
        "status": "SUCCESS",
        "response": "2\n",
        "usage": {"input_tokens": 13842, "output_tokens": 41, "total_tokens": 13883},
    }
    write_cli(bin_dir, "agy", f"print(json.dumps({agy_out!r}))\n")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("SECRET_TOKEN", SECRET)
    return tmp_path


def launch(tmp_path: Path, runtime: str, **env) -> Path:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    prompt = tmp_path / "prompt.md"
    prompt.write_text(f"{PROMPT}\n")
    stem = tmp_path / "out" / runtime
    stem.parent.mkdir(exist_ok=True)
    p = subprocess.run(
        ["bash", str(LAUNCH), runtime, str(work), str(prompt), str(stem), "", "", "60"],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )
    assert p.returncode == 0, p.stderr
    return stem


def wait_gone(stem: Path, limit: float = 20) -> None:
    pid = int(Path(f"{stem}.pid").read_text())
    end = time.time() + limit
    while time.time() < end:
        try:
            os.kill(pid, 0)
            state = Path(f"/proc/{pid}/stat").read_text().split()[2] if Path(f"/proc/{pid}/stat").exists() else ""
            if state == "Z":
                return
        except (ProcessLookupError, OSError):
            return
        time.sleep(0.05)
    raise AssertionError("包みのサブシェルが終わらない")


@pytest.mark.parametrize("runtime", ["claude", "codex", "kiro", "agy"])
def test_each_runtime_leaves_one_row_in_its_own_ledger(fake_clis, runtime):
    stem = launch(fake_clis, runtime, NDF_USAGE_SOURCE="cross-review", NDF_USAGE_KIND="review", NDF_USAGE_STEP="s1")
    wait_gone(stem)
    [row] = ledger(runtime)
    assert (row["runtime"], row["source"], row["kind"], row["step"], row["status"]) == (runtime, "cross-review", "review", "s1", "ok")
    assert row["usage"] != "unknown", row
    others = [r for rt in usage_ledger.RUNTIMES if rt != runtime for r in ledger(rt)]
    assert others == []  # ほかの帳簿には入らない（I2）
    text = "".join(p.read_text() for p in usage_ledger.ledger_dir().rglob("*.jsonl"))
    assert SECRET not in text and PROMPT not in text  # プロンプトの本文と環境変数の値を書かない（I6）


def test_runtime_specific_usage_shapes(fake_clis):
    for rt in ("claude", "codex", "kiro", "agy"):
        wait_gone(launch(fake_clis, rt))
    claude, codex, kiro, agy = (ledger(rt)[0] for rt in ("claude", "codex", "kiro", "agy"))
    assert claude["usage"]["cache_creation"] == {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 200}
    assert claude["session_id"] == "sess-c" and claude["model"] == "claude-opus-5-5"
    assert codex["usage"]["input_tokens"] == 1200 and codex["model"] == "gpt-test" and codex["session_id"] == SID
    assert kiro["usage"] == {"credits": 0.75, "turns": 1} and kiro["model"] == "auto"
    assert agy["usage"]["input_tokens"] == 13842 and agy["session_id"] == "c1"
    assert all(r["source"] == "unknown" and r["kind"] == "unknown" for r in (claude, codex, kiro, agy))  # 名乗りが無い


def test_cli_without_usage_is_unknown_not_zero(fake_clis):
    write_cli(fake_clis / "bin", "codex", "sys.stdin.read()\nprint('no usage')\n")
    wait_gone(launch(fake_clis, "codex"))
    [row] = ledger("codex")
    assert row["usage"] == "unknown"
    assert '"usage": "unknown"' in next(usage_ledger.ledger_dir().rglob("*.jsonl")).read_text()


def test_term_to_the_group_leaves_a_killed_row(fake_clis):
    write_cli(fake_clis / "bin", "codex", "sys.stdin.read()\ntime.sleep(60)\n")
    stem = launch(fake_clis, "codex")
    pid = int(Path(f"{stem}.pid").read_text())
    time.sleep(0.5)
    os.killpg(pid, signal.SIGTERM)  # 監視の止め方（monitor_proc._kill_pid）と同じくグループへ送る
    wait_gone(stem)
    [row] = ledger("codex")
    assert row["status"] == "killed" and row["usage"] == "unknown"


def test_term_after_the_cli_ended_still_leaves_the_row(fake_clis):
    # 監視は codex の終わりの印と result.json がそろうと、CLI が終わった後でも包みのグループへ TERM を送る。
    # 記録の python3 を遅らせ、その間に TERM を送っても行が残ることを確かめる。
    bin_dir = fake_clis / "bin"
    real = subprocess.run(["bash", "-c", "command -v python3"], capture_output=True, text=True, check=True).stdout.strip()
    shim = bin_dir / "python3"
    shim.write_text(
        "#!/bin/bash\n"
        'case " $* " in *" record-cli "*) touch "$NDF_TEST_RECORDING"; sleep 1 ;; esac\n'
        f'exec {real} "$@"\n'
    )
    shim.chmod(0o755)
    marker = fake_clis / "recording"
    stem = launch(fake_clis, "codex", NDF_TEST_RECORDING=str(marker))
    pid = int(Path(f"{stem}.pid").read_text())
    end = time.time() + 20
    while not marker.exists():
        assert time.time() < end, "記録が始まらない"
        time.sleep(0.02)
    os.killpg(pid, signal.SIGTERM)
    wait_gone(stem)
    [row] = ledger("codex")
    assert row["status"] == "ok" and row["usage"] != "unknown"


def test_unwritable_ledger_keeps_launch_results(fake_clis, monkeypatch):
    blocker = fake_clis / "blocker"
    blocker.write_text("")
    monkeypatch.setenv("NDF_USAGE_DIR", str(blocker / "usage"))
    stem = launch(fake_clis, "agy")
    wait_gone(stem)
    assert json.loads(Path(f"{stem}-stdout.log").read_text())["response"] == "2\n"
    assert "使用量の帳簿" in Path(f"{stem}-err.log").read_text()


# --- Python の起動の共有処理 ---


def test_probe_rows_per_runtime(fake_clis):
    import auth

    auth.run_check("model", "claude", None, cwd=str(fake_clis))
    auth.run_check("auth", "claude", None, cwd=str(fake_clis))  # 認証の確認は LLM を呼ばない
    [row] = ledger("claude")
    assert row["kind"] == "probe" and row["usage"]["input_tokens"] == 3


def test_jev_post_writes_a_jev_row(monkeypatch):
    import jev

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"answers": {"q": {"type": "boolean", "probability": 0.9}}}).encode()

    monkeypatch.setenv(jev.KEY_ENV, SECRET)
    monkeypatch.setattr(jev.urllib.request, "urlopen", lambda req, timeout: Resp())
    assert jev.ask_boolean("state", "q?") == (True, 0.9)
    [row] = ledger("jev")
    assert (row["runtime"], row["kind"], row["status"], row["usage"]) == ("jev", "evaluate", "ok", {"requests": 1, "questions": 1})
    assert ledger("claude") == [] and SECRET not in json.dumps(row)


def test_jev_without_key_sends_nothing_and_writes_nothing(monkeypatch):
    import jev

    monkeypatch.delenv(jev.KEY_ENV, raising=False)
    assert jev.ask_boolean("state", "q?") is None
    assert ledger("jev") == []


def transcript(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def assistant(mid: str, model: str = "claude-sonnet-5", **u) -> dict:
    usage = {"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 4, **u}
    return {"type": "assistant", "message": {"id": mid, "model": model, "usage": usage}}


def test_subagent_hook_writes_one_row(tmp_path):
    t = transcript(tmp_path / "agent-a1.jsonl", [assistant("m1"), assistant("m1"), assistant("m2"), {"type": "user"}])
    hook = {"session_id": "parent", "agent_id": "a1", "agent_type": "ndf:worker", "agent_transcript_path": str(t), "cwd": str(tmp_path)}
    p = subprocess.run([sys.executable, str(SCRIPTS / "usage-subagent.py")], input=json.dumps(hook), capture_output=True, text=True)
    assert p.returncode == 0 and p.stdout == ""
    [row] = ledger("claude")
    assert (row["source"], row["kind"], row["agent_type"], row["agent_id"], row["parent_session_id"]) == (
        "subagent",
        "subagent",
        "ndf:worker",
        "a1",
        "parent",
    )
    assert row["usage"]["input_tokens"] == 2 and row["usage"]["cache_creation"]["ephemeral_5m_input_tokens"] == 8  # m1 は 1 回


def test_subagent_hook_never_fails(tmp_path):
    for stdin in ("not json", json.dumps({"agent_id": "x", "agent_transcript_path": str(tmp_path / "missing.jsonl")})):
        p = subprocess.run([sys.executable, str(SCRIPTS / "usage-subagent.py")], input=stdin, capture_output=True, text=True)
        assert p.returncode == 0
    [row] = ledger("claude")
    assert row["usage"] == "unknown" and row["agent_id"] == "x"


def test_hook_is_registered_for_subagent_stop():
    hooks = json.loads((SCRIPTS.parent / "hooks" / "claude.json").read_text())["hooks"]
    assert any("usage-subagent.py" in h["command"] for e in hooks["SubagentStop"] for h in e["hooks"])


# --- 帳簿の形 ---


def test_v1_rows_are_read_as_claude(tmp_path):
    (tmp_path / "acme__w.jsonl").write_text(
        json.dumps({"source": "supervise", "kind": "work", "usage": {}, "at": "2026-10-01T00:00:00Z"}) + "\n"
    )
    [row] = usage_ledger.read_usage_ledger("claude", tmp_path)
    assert (row["schema"], row["runtime"], row["status"], row["usage"]) == (1, "claude", "ok", "unknown")
    assert usage_ledger.read_usage_ledger("codex", tmp_path) == []


def test_append_keeps_existing_bytes(tmp_path):
    led = usage_ledger.UsageLedger(tmp_path, tmp_path / "u", "codex")
    led.append(usage_ledger.UsageRecord(source="a", kind="b", runtime="codex"))
    before = led.path.read_bytes()
    led.append(usage_ledger.UsageRecord(source="a", kind="c", runtime="codex"))
    assert led.path.read_bytes().startswith(before)
    with pytest.raises(ValueError):
        led.append(usage_ledger.UsageRecord(source="a", kind="b", runtime="claude"))  # 別の帳簿の行は入らない（I2）


def test_rows_written_in_tests_carry_the_test_name(tmp_path):
    r = json.loads(usage_ledger.UsageRecord(source="a", kind="b").to_json())
    assert r["pytest"].endswith("test_rows_written_in_tests_carry_the_test_name")
    before = usage_ledger.ledger_snapshot(tmp_path)
    usage_ledger.append_safely(tmp_path, usage_ledger.UsageRecord(source="a", kind="b"), tmp_path)
    usage_ledger.UsageLedger(tmp_path, tmp_path).path.open("a").write(json.dumps({"source": "hook"}) + "\n")  # 印の無い行
    found = usage_ledger.marked_rows(tmp_path, before)
    assert [f["source"] for f in found] == ["a"] and found[0]["file"] == "claude/unknown.jsonl"


def test_utc_seconds_reads_fractions():
    t = usage_capture.utc_seconds("2026-10-13T01:13:04.660583342Z")
    assert abs(t - datetime(2026, 10, 13, 1, 13, 4, 660583, tzinfo=timezone.utc).timestamp()) < 1e-3


def test_codex_review_shows_in_the_codex_report(fake_clis):
    """cross-review が名乗って codex を起動すると、codex の帳簿の集計に入力トークンが出る（会話への結び付けを使わない）。"""
    wait_gone(launch(fake_clis, "codex", NDF_USAGE_SOURCE="cross-review", NDF_USAGE_KIND="review"))
    p = subprocess.run(
        [sys.executable, str(SCRIPTS / "usage-report.py"), "--ledger", "codex", "--all-repos", "--root", str(fake_clis)],
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    [g] = out["groups"]
    assert g["key"] == "cross-review:review" and g["usage"]["input_tokens"] > 0
    assert "claude" not in out and out["closed_issues"] == "unknown"


# --- codex_usage の現状固定 ---

CODEX_SID = "0199aaaa-bbbb-cccc-dddd-eeeeffff0000"


def _rollout(home: Path, sid: str, lines: list, sub: str = "2026/10/13") -> Path:
    d = home / "sessions" / sub
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"rollout-2026-10-13T00-00-00-{sid}.jsonl"
    path.write_text("\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return path


def _token_count(**total) -> dict:
    return {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": total}}}


def test_codex_usage_without_session_id_falls_back_to_tokens_used(tmp_path):
    assert usage_capture.codex_usage("tokens used\n 12,345\n", tmp_path) == ({"total_tokens": 12345}, "", None)
    assert usage_capture.codex_usage("", tmp_path) == (usage_capture.UNKNOWN, "", None)
    assert usage_capture.codex_usage(None, tmp_path) == (usage_capture.UNKNOWN, "", None)


def test_codex_usage_takes_the_last_token_count_and_model(tmp_path):
    _rollout(
        tmp_path,
        CODEX_SID,
        [
            "not json",
            [1, 2],
            {"type": "turn_context", "payload": "x"},
            {"type": "turn_context", "payload": {"model": "gpt-a"}},
            _token_count(input_tokens=10),
            {"type": "event_msg", "payload": {"type": "token_count", "info": None}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {}}}},
            {"type": "event_msg", "payload": {"type": "other", "info": {"total_token_usage": {"input_tokens": 99}}}},
            {"type": "turn_context", "payload": {"model": 5}},
            {"type": "turn_context", "payload": {"model": "gpt-b"}},
            _token_count(input_tokens=20, output_tokens=3),
        ],
    )
    err = f"session id: {CODEX_SID}\ntokens used\n 999\n"
    assert usage_capture.codex_usage(err, tmp_path) == ({"input_tokens": 20, "output_tokens": 3}, "gpt-b", CODEX_SID)


def test_codex_usage_reads_rollouts_in_sorted_order(tmp_path):
    _rollout(tmp_path, CODEX_SID, [_token_count(input_tokens=2)], sub="2026/10/14")
    _rollout(tmp_path, CODEX_SID, [{"type": "turn_context", "payload": {"model": "m"}}, _token_count(input_tokens=1)])
    assert usage_capture.codex_usage(f"session id: {CODEX_SID}", tmp_path) == ({"input_tokens": 2}, "m", CODEX_SID)


def test_codex_usage_without_token_count_falls_back(tmp_path):
    _rollout(tmp_path, CODEX_SID, [{"type": "turn_context", "payload": {"model": "gpt-a"}}])
    err = f"session id: {CODEX_SID}\ntokens used\n 7\n"
    assert usage_capture.codex_usage(err, tmp_path) == ({"total_tokens": 7}, "gpt-a", CODEX_SID)
    assert usage_capture.codex_usage(f"session id: {CODEX_SID}", tmp_path) == (usage_capture.UNKNOWN, "gpt-a", CODEX_SID)


def test_codex_usage_without_sessions_dir(tmp_path):
    err = f"session id: {CODEX_SID}"
    assert usage_capture.codex_usage(err, tmp_path / "missing") == (usage_capture.UNKNOWN, "", CODEX_SID)


def test_codex_usage_home_falls_back_to_codex_home_env(tmp_path, monkeypatch):
    _rollout(tmp_path / "env", CODEX_SID, [_token_count(input_tokens=4)])
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "env"))
    assert usage_capture.codex_usage(f"session id: {CODEX_SID}") == ({"input_tokens": 4}, "", CODEX_SID)
