"""遅れの見張りが、出力の行とコミットのほかの形で進むステップを打ち切らない（#1623 の 1623-1・1623-2、#1496 の 1496-1〜1496-3）。

- 要求・設計の work（会話を残す claude -p）は、セッションの記録（`<CLAUDE_CONFIG_DIR>/projects/*/<id>.jsonl`）の伸びを見る
- drive（cross-review など）は、前の確認より後の Pull Request の投稿・レビュー・コミットを見る

claude は NDF_SUPERVISE_CLAUDE の偽物、gh は `gh_call.RUNNER` の差し替えで答える。待ちの秒は expected と default で縮める。
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
PY = sys.executable
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import gh_call  # noqa: E402
import slow_step as ss  # noqa: E402
from supervise_lib import engine  # noqa: E402

FAKE_CLAUDE = r"""
import json, os, pathlib, sys, time
argv = sys.argv[1:]
prompt = sys.stdin.read()
open(os.environ["FAKE_CLAUDE_LOG"], "a").write(json.dumps({"judge": "## 経過と想定" in prompt, "argv": argv}) + "\n")
if "## 経過と想定" in prompt:
    answer = json.dumps({"decision": "wait", "reason": "待つ", "wait_seconds": 1})
    print(json.dumps({"result": answer, "usage": {}, "total_cost_usd": 0.0, "num_turns": 1}))
    sys.exit(0)
sid = argv[argv.index("--session-id") + 1] if "--session-id" in argv else ""
path = pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "-work-repo" / f"{sid}.jsonl"
path.parent.mkdir(parents=True, exist_ok=True)
for _ in range(15):  # 標準エラーへも作業の行へも書かず、コミットもしない
    if os.environ.get("FAKE_GROW") == "1" and sid:
        with open(path, "a") as f:
            f.write('{"type": "assistant"}\n')
    time.sleep(0.1)
print(json.dumps({"result": "## 作業の報告\n- 結果: 完了", "usage": {}, "total_cost_usd": 0.01, "num_turns": 1, "session_id": sid}))
"""

DRIVE = r"""
import json, sys, time
for _ in range(int(sys.argv[1])):  # ラウンドの進みは PR への投稿とコミットにだけ残し、段の出力へ書かない
    time.sleep(0.4)
print(json.dumps({"tool": "cross-review", "status": "ok", "summary": "収束した", "items": [], "metrics": {"rounds": int(sys.argv[1])}}))
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE_CLAUDE)
    (tmp_path / "drive.py").write_text(DRIVE)
    monkeypatch.setenv("NDF_SUPERVISE_CLAUDE", f"{PY} {fake}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude.log"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    for k in ("FAKE_GROW", "NDF_SUPERVISE_CLAUDE_FALLBACK"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def run_plan(tmp: Path, steps: list[dict], slow: dict | None = None, **extra):
    plan = {
        "フェーズ": "試験",
        "課題": [1623],
        "作業場所": str(tmp),
        "report_interval": 0.2,
        "steps": steps,
        "slow": {"history": str(tmp / "history.jsonl"), "max_waits": 20, **(slow or {})},
        **extra,
    }
    s = engine.Engine(plan, tmp / "state")
    return s, s.run()


def lines(tmp: Path, kind: str) -> list[dict]:
    p = tmp / "state" / "progress.jsonl"
    return [d for d in map(json.loads, p.read_text().splitlines()) if d["kind"] == kind]


def judge_calls(tmp: Path) -> list[dict]:
    p = tmp / "claude.log"
    return [d for d in map(json.loads, p.read_text().splitlines()) if d["judge"]] if p.is_file() else []


DESIGN = {"id": "design", "type": "work", "kind": "設計", "full": True, "prompt": "/ndf:design 1623", "expected": 0.3, "next": "end"}


def test_1623_1_a_design_worker_growing_its_session_is_not_cut(env, monkeypatch):
    monkeypatch.setenv("FAKE_GROW", "1")
    s, text = run_plan(env, [DESIGN])
    assert "結果: 完了" in text and [e["id"] for e in s.state.log] == ["design"]
    slow = lines(env, "slow")
    assert slow and all(d["probe"]["class"] == "progress" and d["probe"]["name"] == "worker" for d in slow)
    assert "セッションの記録が" in slow[0]["probe"]["summary"] and not judge_calls(env)
    argv = json.loads((env / "claude.log").read_text().splitlines()[0])["argv"]
    assert argv[argv.index("--session-id") + 1] == s.state.session["id"]


def test_1623_2_a_worker_whose_session_does_not_grow_goes_to_the_judge(env):
    _, text = run_plan(env, [DESIGN])
    slow = lines(env, "slow")
    assert slow[0]["probe"]["class"] == "silent" and judge_calls(env)


def test_session_size_is_zero_when_the_record_is_missing(tmp_path):
    assert ss.session_size({"id": "nope", "config": str(tmp_path)}) == 0
    assert ss.session_size({}) == 0


def gh_latest(monkeypatch, latest):
    """`gh pr view` の最も新しい時刻を、呼ばれた時刻から決めて返す（None なら gh が落ちる）。"""
    calls: list[list[str]] = []

    def runner(args, stdin=None, cwd=None):
        calls.append(list(args))
        at = latest()
        if at is None:
            return gh_call.GhResult(1, "", "gh: not found")
        return gh_call.GhResult(0, datetime.fromtimestamp(at, timezone.utc).isoformat() + "\n", "")

    monkeypatch.setattr(gh_call, "RUNNER", runner)
    return calls


def review_step(rounds: int) -> dict:
    return {"id": "review", "type": "drive", "kind": "実装レビュー", "cmd": f"{PY} {{dir}}/drive.py {rounds}", "next": "end"}


def drive_plan(env, rounds: int) -> list[dict]:
    step = review_step(rounds)
    step["cmd"] = step["cmd"].replace("{dir}", str(env))
    return [step]


def seed_history(env: Path, n: int) -> None:
    """同じステップの履歴を n 件（min_samples の 3 に満たなければ既定の想定が効く）。"""
    for _ in range(n):
        ss.append_history(env / "history.jsonl", ss.history_record("試験", "review", "drive", 0.2, 0, "", "2026-10-16T00:00:00+09:00"))


def test_1496_1_and_2_a_converging_cross_review_is_not_cut_for_silence(env, monkeypatch):
    """#1496 の再現: 履歴 2 件で既定の想定、4 ラウンドで想定を超えて収束する。打ち切らず 1 回で終わる。"""
    seed_history(env, 2)
    calls = gh_latest(monkeypatch, time.time)  # ラウンドごとに PR へ投稿とコミットが積まれる
    s, text = run_plan(env, drive_plan(env, 4), slow={"default": 0.5}, **{"Pull Request": "454"})
    assert "結果: 完了" in text and [e["id"] for e in s.state.log] == ["review"]
    slow = lines(env, "slow")
    assert slow and slow[0]["basis"]["source"] == "default"
    assert all(d["probe"]["name"] == "drive" and d["probe"]["class"] == "progress" and d["act"] == "wait" for d in slow)
    assert not judge_calls(env) and len(calls) == len(slow)
    assert all(c[:3] == ["pr", "view", "454"] for c in calls)


@pytest.mark.parametrize("latest", ["old", "gh-fails"])
def test_1496_3_a_drive_with_nothing_new_goes_to_the_judge(env, monkeypatch, latest):
    started = time.time()
    gh_latest(monkeypatch, (lambda: started - 60) if latest == "old" else (lambda: None))
    _, text = run_plan(env, drive_plan(env, 3), slow={"default": 0.5}, **{"Pull Request": "454"})
    first = lines(env, "slow")[0]
    assert first["probe"]["name"] == "drive" and first["probe"]["class"] == "silent" and judge_calls(env)


def test_a_step_that_never_gets_slow_does_not_call_gh(env, monkeypatch):
    calls = gh_latest(monkeypatch, time.time)
    _, text = run_plan(env, drive_plan(env, 1), slow={"default": 30}, **{"Pull Request": "454"})
    assert "結果: 完了" in text and calls == []
