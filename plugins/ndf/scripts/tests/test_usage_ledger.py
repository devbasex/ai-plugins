"""使用量の帳簿（lib/usage_ledger.py）と、帳簿へ書く側・プランの状態の置き場所（#1142 の不足 f と d の入れ子）。

claude は NDF_SUPERVISE_CLAUDE / NDF_MVV_CLAUDE で偽物へ差し替える。帳簿の置き場所は NDF_USAGE_DIR、
プランの状態の実体の置き場所は NDF_SV_STATE_DIR で一時ディレクトリへ向ける。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
PY = sys.executable
sys.path.insert(0, str(SCRIPTS / "lib"))
import usage_ledger  # noqa: E402


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(SCRIPTS))
from supervise_lib import claude, engine, paths, prompts  # noqa: E402
mvv = load("mvv_gate_for_ledger", SCRIPTS / "mvv-gate.py")

USAGE = {"input_tokens": 3, "output_tokens": 40, "cache_read_input_tokens": 500,
         "cache_creation_input_tokens": 300,
         "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 200}}
MODEL_USAGE = {"claude-opus-5-5": {"inputTokens": 3, "outputTokens": 30, "cacheReadInputTokens": 400,
                                   "cacheCreationInputTokens": 300, "costUSD": 0.05},
               "claude-haiku-4-5": {"inputTokens": 1, "outputTokens": 10, "cacheReadInputTokens": 100,
                                    "cacheCreationInputTokens": 0, "costUSD": 0.01}}
CLAUDE_OUT = {"result": "## 作業の報告\n- 結果: 完了", "usage": USAGE, "modelUsage": MODEL_USAGE,
              "total_cost_usd": 0.06, "num_turns": 4, "session_id": "sess-1"}


def fake_claude(tmp_path: Path, out: dict) -> Path:
    f = tmp_path / "fake_claude.py"
    f.write_text(f"import sys\nsys.stdin.read()\nprint({json.dumps(json.dumps(out, ensure_ascii=False))})\n")
    return f


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    d = tmp_path / "usage"
    monkeypatch.setenv("NDF_USAGE_DIR", str(d))
    monkeypatch.setenv("NDF_SUPERVISE_CLAUDE", f"{PY} {fake_claude(tmp_path, CLAUDE_OUT)}")
    monkeypatch.delenv("NDF_SUPERVISE_CLAUDE_FALLBACK", raising=False)
    return d


def rows(d: Path) -> list[dict]:
    return usage_ledger.read_all(d)


# --- ライブラリ ---

def test_record_keeps_usage_and_picks_main_model():
    r = usage_ledger.UsageRecord.from_claude(CLAUDE_OUT, source="supervise", kind="work", plan="p", step="s",
                                             seconds=1.5)
    d = json.loads(r.to_json())
    assert d["usage"]["cache_creation"] == USAGE["cache_creation"]
    assert d["model"] == "claude-opus-5-5" and d["model_usage"] == MODEL_USAGE
    assert (d["cost_usd"], d["turns"], d["seconds"], d["session_id"]) == (0.06, 4, 1.5, "sess-1")
    assert d["at"].endswith("Z") and d["ndf_version"] and d["source"] == "supervise"


def test_missing_values_stay_empty_not_zero():
    d = json.loads(usage_ledger.UsageRecord.from_claude({}, source="mvv-gate", kind="mvv").to_json())
    assert d["model"] == "" and d["model_usage"] is None
    assert d["cost_usd"] is None and d["turns"] is None and d["seconds"] is None and d["usage"] == {}


def test_cache_writes_split_5m_and_1h():
    assert usage_ledger.cache_writes(USAGE) == (100, 200)
    assert usage_ledger.cache_writes({"cache_creation_input_tokens": 70}) == (70, 0)
    assert usage_ledger.cache_writes({}) == (0, 0)


def test_append_only_adds_lines(tmp_path):
    led = usage_ledger.UsageLedger(tmp_path, tmp_path / "u")
    for kind in ("work", "judge"):
        led.append(usage_ledger.UsageRecord(source="supervise", kind=kind))
    assert [r["kind"] for r in led.records()] == ["work", "judge"]
    assert led.path.name == "unknown.jsonl"  # origin の無いリポジトリ


def test_slug_comes_from_origin(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", "git@github.com:acme/widget.git"],
                   check=True)
    assert usage_ledger.UsageLedger(tmp_path, tmp_path / "u").path.name == "acme__widget.jsonl"


def test_ledger_dir_order(tmp_path):
    env = {"HOME": str(tmp_path)}
    assert usage_ledger.ledger_dir(env) == tmp_path / ".local/state/ndf/usage"
    assert usage_ledger.ledger_dir({**env, "XDG_STATE_HOME": "/x"}) == Path("/x/ndf/usage")
    assert usage_ledger.ledger_dir({**env, "XDG_STATE_HOME": "/x", "CLAUDE_PLUGIN_DATA": "/d"}) == Path("/d/usage")
    assert usage_ledger.ledger_dir({**env, "CLAUDE_PLUGIN_DATA": "/d", "NDF_USAGE_DIR": "/n"}) == Path("/n")


def test_append_safely_reports_failure(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("")
    assert not usage_ledger.append_safely(tmp_path, usage_ledger.UsageRecord(source="supervise", kind="work"),
                                          blocker / "usage")
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "使用量の帳簿" in err[0]


# --- supervise.py の呼び出し（ClaudeRunner に当たる口） ---

def run_plan(tmp_path, steps, plan_path=None):
    plan = {"フェーズ": "試験", "課題": [1142], "作業場所": str(tmp_path), "steps": steps}
    s = engine.Engine(plan, tmp_path / "state", plan_path=plan_path)
    return s, s.run()


def test_each_call_appends_one_row_with_5m_and_1h(tmp_path, ledger):
    plan_file = tmp_path / "plan-x.json"
    plan_file.write_text("{}")
    s, text = run_plan(tmp_path, [{"id": "impl", "type": "work", "prompt": "直す", "next": "j"},
                                  {"id": "j", "type": "judge", "question": "?", "choices": ["end"]}],
                       plan_path=str(plan_file))
    got = rows(ledger)
    assert [(r["kind"], r["step"]) for r in got] == [("work", "impl"), ("judge", "j")], text
    assert all(r["source"] == "supervise" and r["plan"] == str(plan_file.resolve()) for r in got)
    assert got[0]["usage"]["cache_creation"]["ephemeral_1h_input_tokens"] == 200
    assert got[0]["model"] == "claude-opus-5-5" and got[0]["session_id"] == "sess-1"
    llm = s.state.log[0]["llm"]
    assert (llm["cache_write"], llm["cache_write_5m"], llm["cache_write_1h"]) == (300, 100, 200)
    assert llm["models"]["claude-opus-5-5"]["calls"] == 1 and llm["models"]["claude-haiku-4-5"]["output"] == 10
    assert (s.state.dir / "state.json").is_file()


def test_ledger_failure_keeps_the_call_result(tmp_path, ledger, monkeypatch, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    monkeypatch.setenv("NDF_USAGE_DIR", str(blocker / "usage"))
    s, text = run_plan(tmp_path, [{"id": "impl", "type": "work", "prompt": "直す", "next": "end"}])
    assert "結果: 完了" in text
    assert s.state.log[0]["llm"]["cost"] == 0.06
    assert "使用量の帳簿" in capsys.readouterr().err


def test_call_kinds_follow_the_system_prompt():
    assert claude.claude_kind(prompts.JUDGE_SYSTEM, False) == "judge"
    assert claude.claude_kind(prompts.SLOW_SYSTEM, False) == "slow"
    assert claude.claude_kind(prompts.PR_SYSTEM, False) == "pr"
    assert claude.claude_kind(prompts.WORK_SYSTEM, False) == "work"
    assert claude.claude_kind(prompts.FULL_SYSTEM, True) == "full"


def test_nested_drive_keeps_inner_metrics(tmp_path, monkeypatch):
    inner = tmp_path / "inner.py"
    inner.write_text("import json\nprint(json.dumps({'status': 'ok', 'metrics': "
                     "{'rounds': 3, 'findings': 5, 'review_status': 'approved'}}))\n")
    res = tmp_path / "gate-result.json"
    outer = tmp_path / "outer.py"
    outer.write_text(
        "import json, pathlib\n"
        f"res = pathlib.Path({str(res)!r})\n"
        "if res.is_file():\n"
        "    print(json.dumps({'status': 'ok', 'metrics': {'adopted': 2}}))\n"
        "else:\n"
        "    print(json.dumps({'status': 'gate', 'items': [{'pause': 'final', 'result_file': str(res),"
        f" 'command': {f'{PY} {inner}'!r}}}]}}))\n")
    monkeypatch.setitem(paths.DRIVES, "fake", outer)
    s, text = run_plan(tmp_path, [{"id": "refactor", "type": "drive", "drive": "fake", "next": "end"}])
    assert "結果: 完了" in text, text
    counts = s.state.log[0]["counts"]
    assert counts["adopted"] == 2
    assert counts["inner"] == {"rounds": 3, "findings": 5, "review_status": "approved"}
    assert json.loads(res.read_text()) == {"review_status": "approved"}


# --- mvv-gate.py ---

def test_mvv_ask_appends_one_row(tmp_path, monkeypatch):
    d = tmp_path / "usage"
    monkeypatch.setenv("NDF_USAGE_DIR", str(d))
    out = {**CLAUDE_OUT, "result": '{"verdict": "follow", "reasons": [], "boundary": []}', "duration_ms": 2000}
    monkeypatch.setenv("NDF_MVV_CLAUDE", f"{PY} {fake_claude(tmp_path, out)}")
    monkeypatch.chdir(tmp_path)
    verdict, _, usage = mvv.ask("材料")
    assert verdict["verdict"] == "follow" and usage["cost_usd"] == 0.06
    [row] = rows(d)
    assert (row["source"], row["kind"], row["plan"], row["step"]) == ("mvv-gate", "mvv", "", "")
    assert row["seconds"] == 2.0 and row["usage"]["cache_creation"] == USAGE["cache_creation"]


# --- プランの状態の置き場所 ---

@pytest.fixture
def temp_root(tmp_path, monkeypatch):
    """tmp_path/tmp を一時ディレクトリとみなし、状態の実体の置き場所を tmp_path/state にする。"""
    t = tmp_path / "tmp"
    t.mkdir()
    monkeypatch.setattr(paths, "temp_roots", lambda: (t.resolve(),))
    monkeypatch.setenv("NDF_SV_STATE_DIR", str(tmp_path / "state" / "sv"))
    return t


def test_state_under_temp_is_symlink_to_state_home(tmp_path, temp_root):
    plan = temp_root / "r1" / "plan-impl.json"
    plan.parent.mkdir()
    plan.write_text('{"steps": []}')
    d = paths.state_dir_of(str(plan))
    assert d == plan.parent / "plan-impl-state" and d.is_symlink()
    real = d.resolve()
    assert real.parent == (tmp_path / "state" / "sv").resolve()
    assert real.name.startswith("plan-impl-") and len(real.name) == len("plan-impl-") + 8
    assert (real / "plan.json").read_text() == '{"steps": []}'
    assert paths.state_dir_of(str(plan)).resolve() == real  # 2 度目も同じ実体


def test_state_outside_temp_stays_a_directory_path(tmp_path, temp_root):
    plan = tmp_path / "keep" / "plan-impl.json"
    plan.parent.mkdir()
    plan.write_text("{}")
    d = paths.state_dir_of(str(plan))
    assert d == plan.parent / "plan-impl-state" and not d.exists()


def test_existing_state_directory_is_kept(tmp_path, temp_root):
    plan = temp_root / "plan-impl.json"
    plan.write_text("{}")
    (temp_root / "plan-impl-state").mkdir()
    d = paths.state_dir_of(str(plan))
    assert d.is_dir() and not d.is_symlink()


def test_state_home_under_temp_makes_no_symlink(tmp_path, temp_root, monkeypatch):
    monkeypatch.setenv("NDF_SV_STATE_DIR", str(temp_root / "sv"))
    plan = temp_root / "plan-impl.json"
    plan.write_text("{}")
    assert not paths.state_dir_of(str(plan)).is_symlink()


# --- phase_cost.py の既定の置き場所 ---

def test_phase_cost_reads_state_home_by_default(tmp_path):
    state = tmp_path / "xdg" / "ndf" / "sv"
    for d in (state / "r1" / "plan-a-state", state / "plan-b-1a2b3c4d"):
        d.mkdir(parents=True)
        (d / "state.json").write_text(json.dumps({"log": [{"id": "w", "type": "work", "exit": 0,
                                                           "llm": {"cost": 0.1, "turns": 1}}]}))
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "XDG_STATE_HOME": str(tmp_path / "xdg")}
    p = subprocess.run([PY, str(SCRIPTS / "experimental" / "phase_cost.py")], capture_output=True, text=True,
                       env=env)
    assert p.returncode == 0, p.stderr
    assert "計画 2 件・ステップ 2 件" in p.stdout
