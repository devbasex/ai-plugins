"""欠けの残る結果（stopped / 終了コード 6）を、既存の読み手が成功と読まない（#2227 の受け入れ条件 9・I4）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
from supervise_lib import engine  # noqa: E402

GAP_OUT = json.dumps(
    {
        "tool": "t",
        "status": "stopped",
        "summary": "収束しなかった",
        "items": [],
        "metrics": {},
        "gaps": [{"kind": "goal", "name": "収束", "reason": "max_rounds"}],
    },
    ensure_ascii=False,
)


def run_plan(tmp_path, steps):
    plan = {"フェーズ": "試験", "課題": [858], "作業場所": str(tmp_path), "steps": steps}
    s = engine.Engine(plan, tmp_path / "state")
    return s, s.run()


def test_exit_6_goes_to_on_fail(tmp_path):
    steps = [
        {"id": "a", "type": "run", "cmd": f"echo '{GAP_OUT}'; exit 6", "on_fail": "b", "next": "c"},
        {"id": "b", "type": "run", "cmd": "true", "next": "end"},
        {"id": "c", "type": "run", "cmd": "true", "next": "end"},
    ]
    s, _ = run_plan(tmp_path, steps)
    assert [e["id"] for e in s.state.log] == ["a", "b"]


def test_exit_6_without_on_fail_stops(tmp_path):
    steps = [
        {"id": "a", "type": "run", "cmd": f"echo '{GAP_OUT}'; exit 6", "next": "c"},
        {"id": "c", "type": "run", "cmd": "true", "next": "end"},
    ]
    s, text = run_plan(tmp_path, steps)
    assert [e["id"] for e in s.state.log] == ["a"] and "結果: 完了" not in text


def test_on_exit_6_goes_to_its_step(tmp_path):
    steps = [
        {"id": "a", "type": "run", "cmd": f"echo '{GAP_OUT}'; exit 6", "on_exit": {"6": "judge"}, "on_fail": "b"},
        {"id": "b", "type": "run", "cmd": "true", "next": "end"},
        {"id": "judge", "type": "run", "cmd": "true", "next": "end"},
    ]
    s, _ = run_plan(tmp_path, steps)
    assert [e["id"] for e in s.state.log] == ["a", "judge"]
