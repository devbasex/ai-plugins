"""ステップの打ち直しの同じ原因の上限（#2343 の受け入れ条件 5・不変条件 I11・I12、#1983 の再現）。

判定役（judge）が毎回同じステップをもう一度流すと返しても、同じ原因の失敗が方針 `supervise.step-rerun` の回数に達したら
プランの実行が止まり、理由に原因の要旨と回数が出る。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_supervise import progress, run_plan, seq  # noqa: E402,F401

import limits  # noqa: E402
from supervise_lib import rerun  # noqa: E402
from supervise_lib.state import RunState  # noqa: E402

JUDGE_RERUN = {"out": {"result": '{"decision": "release", "reason": "外部の待ちの揺れなのでもう 1 度"}', "usage": {}, "total_cost_usd": 0.0}}

# #1983: release のステップが同じ CI の失敗（run の番号だけが違う）で落ち続ける
SAME_CI_FAILURE = (
    'n=$(cat count 2>/dev/null || echo 0); n=$((n+1)); echo $n > count; '
    'echo "{\\"status\\": \\"stopped\\", \\"summary\\": \\"PR #1976 の CI が失敗: runtime-smoke (agy)（run 3835337858$n）\\"}"; exit 1'
)


def _steps(cmd: str) -> list[dict]:
    return [
        {"id": "release", "type": "run", "on_fail": "j", "next": "end", "cmd": cmd},
        {"id": "j", "type": "judge", "question": "直すか", "choices": ["release", "stop"]},
    ]


def test_the_1983_plan_stops_at_the_declared_count(tmp_path, seq, monkeypatch):
    set_responses, calls = seq
    set_responses(*[JUDGE_RERUN] * 7)  # 判定役は 7 回とも「もう 1 度」を返す（#1983）
    monkeypatch.chdir(tmp_path)
    s, text = run_plan(tmp_path, _steps(SAME_CI_FAILURE), 上限=20)
    cap = limits.value("supervise.step-rerun", "same_cause")
    assert (tmp_path / "count").read_text().strip() == str(cap)
    assert "結果: 止まった" in text
    assert "同じ原因（exit=1: PR #<数> の CI が失敗: runtime-smoke (agy)（run <数>）" in text and f"{cap} 回失敗した" in text
    rows = [r for r in progress(s) if r["kind"] == "same-cause"]
    assert rows == [{**rows[0], "step": "release", "count": cap, "policy": "supervise.step-rerun"}]


def test_a_changed_cause_keeps_running(tmp_path, seq, monkeypatch):
    """原因の鍵が変わった失敗は別に数え、流し続ける。"""
    set_responses, _ = seq
    set_responses(*[JUDGE_RERUN] * 3)
    monkeypatch.chdir(tmp_path)
    cmd = (
        "n=$(cat count 2>/dev/null || echo 0); n=$((n+1)); echo $n > count; "
        'case $n in 1) echo "lint が失敗"; exit 1;; 2) echo "test が失敗"; exit 1;; 3) echo "build が失敗"; exit 1;; esac; exit 0'
    )
    s, text = run_plan(tmp_path, _steps(cmd), 上限=20)
    assert "結果: 完了" in text and (tmp_path / "count").read_text().strip() == "4"
    assert [r for r in progress(s) if r["kind"] == "same-cause"] == []


def test_the_counts_survive_a_resume(tmp_path):
    """I12: 落ちた後の続き（耐久の記録からの再開）でも回数を数え直さずに引き継ぐ。"""
    plan = {"フェーズ": "試験", "作業場所": str(tmp_path), "steps": []}
    before = RunState(tmp_path / "a", plan)
    failed = {"exit": 1, "text": json.dumps({"status": "stopped", "summary": "CI が失敗: runtime-smoke（run 1）"})}
    rerun.count(before.cause_counts, "release", failed)
    after = RunState(tmp_path / "b", plan)
    after.replay({"n": 1, "sid": "release", "cur": failed, "acc": before.snapshot()})
    assert rerun.stop_reason(after, "release") is None  # 1 回目の後はまだ流す
    rerun.count(after.cause_counts, "release", {**failed, "text": failed["text"].replace("run 1", "run 2")})
    assert "2 回失敗した" in rerun.stop_reason(after, "release")


def test_a_rate_limited_failure_is_left_to_the_recovery_wait(tmp_path):
    plan = {"フェーズ": "試験", "作業場所": str(tmp_path), "steps": []}
    st = RunState(tmp_path / "a", plan)
    limited = {"exit": 1, "text": "GraphQL: API rate limit already exceeded for user ID 1."}
    for _ in range(3):
        rerun.count(st.cause_counts, "t", limited)
    st.results["t"] = limited
    assert st.cause_counts == {} and rerun.stop_reason(st, "t") is None
