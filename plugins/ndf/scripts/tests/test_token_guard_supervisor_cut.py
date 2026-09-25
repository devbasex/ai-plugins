"""token-guard.sh の判定「スイッチポイント」（#954 の AC5）。

寿命 5 分の supervisor（入力の agent_type が ndf:supervisor）が cross-review / cross-refactoring を
起動するとき、自身の記録の最初の呼び出しの文脈 P と最後の呼び出しの文脈 C を比べ、C ≥ 比 × P なら止める。
偽の会話の記録（<セッション>.jsonl と <セッション>/subagents/agent-<ID>.jsonl）を一時ディレクトリに作って渡す。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "token-guard.sh"
P = 10_000
AGENT_ID = "a1b2c3"


def call(tokens: int) -> dict:
    return {"type": "assistant", "message": {"usage": {
        "input_tokens": 10, "cache_read_input_tokens": tokens - 10 - 100, "cache_creation_input_tokens": 100}}}


def user() -> dict:
    return {"type": "user", "message": {"content": "…"}}


def write(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def transcript(tmp_path, own_rows, parent_last=P):
    """親（conductor）の記録と supervisor 自身の記録を作り、親の記録のパスを返す。"""
    parent = tmp_path / "proj" / "sess.jsonl"
    write(parent, [call(parent_last)])
    write(parent.with_suffix("") / "subagents" / f"agent-{AGENT_ID}.jsonl", own_rows)
    return parent


def payload(tp, skill="cross-review", agent_type="ndf:supervisor", agent_id=AGENT_ID):
    p = {"tool_name": "Skill", "tool_input": {"skill": skill, "args": "#1"},
         "session_id": "sess", "transcript_path": str(tp)}
    if agent_id is not None:
        p["agent_id"] = agent_id
    if agent_type is not None:
        p["agent_type"] = agent_type
    return p


def run(data, tmp_path, env=None):
    e = {k: v for k, v in os.environ.items() if not k.startswith("NDF_")}
    e["CLAUDE_PLUGIN_DATA"] = str(tmp_path / "plugin-data")
    e.update(env or {})
    proc = subprocess.run(["bash", str(SCRIPT)], input=json.dumps(data), capture_output=True,
                          text=True, env=e, timeout=20)
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return None
    spec = json.loads(proc.stdout)["hookSpecificOutput"]
    assert spec["permissionDecision"] == "deny"
    return spec["permissionDecisionReason"]


def grown(ratio):
    return [call(P), user(), call(int(P * (1 + ratio) / 2)), user(), call(int(P * ratio))]


# 表の # は設計のテスト（issue-954-design-tests.md の「AC5 の入力の表」）の行番号
@pytest.mark.parametrize("agent_type,skill,ratio,env,stop", [
    ("ndf:supervisor", "cross-review", 1.5, {}, True),                          # 1
    ("ndf:supervisor", "ndf:cross-review", 1.5, {}, True),                      # 2
    ("ndf:supervisor", "cross-refactoring", 3, {}, True),                       # 3
    ("ndf:supervisor", "ndf:cross-refactoring", 3, {}, True),                   # 4
    ("ndf:supervisor", "cross-review", 1.4, {}, False),                         # 5
    ("ndf:supervisor", "pr", 3, {}, False),                                     # 6
    ("ndf:worker", "cross-review", 3, {}, False),                               # 9
    ("general-purpose", "cross-review", 3, {}, False),                          # 10
    (None, "cross-review", 3, {}, False),                                       # 11
    ("ndf:supervisor-waits", "cross-review", 5, {}, False),                     # 17
    ("ndf:supervisor", "cross-review", 3, {"NDF_CONTEXT_GUARD": "0"}, True),    # 18
    ("ndf:supervisor", "cross-review", 3, {"NDF_SUPERVISOR_CUT_GUARD": "0"}, False),  # 12
    ("ndf:supervisor", "cross-review", 2.5, {"NDF_SUPERVISOR_CUT_RATIO": "3"}, False),  # 15
    ("ndf:supervisor", "cross-review", 3, {"NDF_SUPERVISOR_CUT_RATIO": "3"}, True),     # 16
])
def test_cut_by_ratio(tmp_path, agent_type, skill, ratio, env, stop):
    tp = transcript(tmp_path, grown(ratio))
    reason = run(payload(tp, skill=skill, agent_type=agent_type), tmp_path, env)
    if stop:
        assert reason is not None
        assert "結果: スイッチポイント" in reason and "次の工程" in reason
    else:
        assert reason is None


def test_keeps_stopping_the_same_launch(tmp_path):
    # 7: 1 度だけ通すことをしない
    tp = transcript(tmp_path, grown(1.5))
    for _ in range(3):
        assert run(payload(tp), tmp_path) is not None


def test_conductor_is_not_cut(tmp_path):
    # 8: agent_id が無い（conductor）。conductor の判定の上限より文脈は小さい
    tp = transcript(tmp_path, grown(3))
    assert run(payload(tp, agent_type=None, agent_id=None), tmp_path) is None


def test_empty_own_record_passes(tmp_path):
    # 11b: 自身の記録が空で P・C が読めない
    tp = transcript(tmp_path, [])
    assert run(payload(tp), tmp_path) is None


def test_missing_own_record_passes(tmp_path):
    parent = tmp_path / "proj" / "sess.jsonl"
    write(parent, [call(P * 10)])
    assert run(payload(parent), tmp_path) is None


def test_reads_its_own_record_not_the_parent(tmp_path):
    # 13: 親の記録の最後の文脈は P の 10 倍でも、自身の記録で 1.4 倍なら通す
    tp = transcript(tmp_path, grown(1.4), parent_last=P * 10)
    assert run(payload(tp), tmp_path) is None


def test_first_call_is_read_from_the_head(tmp_path):
    # 14: 最初の呼び出しが末尾 200 行の外にある。末尾 200 行の最も古い呼び出しは 1.4P
    rows = [call(P)] + [user() for _ in range(150)] + [call(int(P * 1.4))]
    rows += [user() for _ in range(160)] + [call(int(P * 1.5))]
    assert len(rows) > 300
    tp = transcript(tmp_path, rows)
    assert run(payload(tp), tmp_path) is not None
