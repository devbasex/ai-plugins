"""`run --from` の再開で、ステップの入力へ前の実行の結果を渡す（#1676 の 1676-2〜1676-4・I7・I8）。

ステップの結果はハンドラーをスタブに替えて決め、judge のスタブは渡された入力の節（`StepContext.inputs_text`）を残す。
同じ状態ディレクトリで実行を重ね、前の実行の範囲（`--from` の無い最後の実行より後）と、今の結果が先に効くことを見る。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
from supervise_lib import engine  # noqa: E402

PLAN = {
    "フェーズ": "試験",
    "課題": [1676],
    "上限": 10,
    "steps": [
        {"id": "pick", "type": "run", "cmd": "true", "on_fail": "review", "next": "early"},
        {"id": "early", "type": "run", "cmd": "true", "next": "review"},
        {"id": "review", "type": "drive", "cmd": "true", "on_fail": "fix-review", "next": "stopper"},
        {"id": "fix-review", "type": "work", "kind": "修正", "prompt": "直す", "inputs": ["review"], "next": "review"},
        {"id": "stopper", "type": "run", "cmd": "true", "next": "gate"},
        {"id": "gate", "type": "judge", "inputs": ["early", "review"], "question": "関門へ", "choices": ["gate", "stop"]},
    ],
}


class _Fake:
    def __init__(self, kind: str, codes: dict[str, list[int]], label: str) -> None:
        self.kind, self.codes, self.label = kind, codes, label

    def execute(self, ctx, step: dict):
        queue = self.codes.get(step["id"]) or [0]
        code = queue.pop(0) if len(queue) > 1 else queue[0]
        ctx.state.cur.update(exit=code, text=f"{self.label} の {step['id']}（exit={code}）", seconds=0.0)
        return code == 0, ""


class _Judge:
    kind = "judge"

    def __init__(self, seen: list[str]) -> None:
        self.seen = seen

    def execute(self, ctx, step: dict) -> dict:
        self.seen.append(ctx.inputs_text(step))
        ctx.state.cur.update(exit=0, text=json.dumps({"decision": "gate"}), seconds=0.0)
        return {"decision": "gate"}


def run(tmp: Path, label: str, codes: dict[str, list[int]], start: str | None = None) -> tuple[list[str], list[str], str]:
    """同じ状態ディレクトリで 1 回流し、(流したステップ, judge が受けた入力の節, 報告) を返す。"""
    plan = json.loads(json.dumps(PLAN))
    plan["作業場所"] = str(tmp)
    eng = engine.Engine(plan, tmp / "state")
    for kind in ("run", "work", "drive", "pr"):
        eng.handlers[kind] = _Fake(kind, dict(codes), label)
    seen: list[str] = []
    eng.handlers["judge"] = _Judge(seen)
    text = eng.run(start)
    return [e["id"] for e in eng.state.log], seen, text


def test_resuming_at_the_gate_gives_the_judge_the_last_result_of_the_previous_run(tmp_path):
    """1676-2: 前の実行で review が 2 回流れていれば、後の結果が `前の実行` の印つきで渡る。"""
    ran, _, text = run(tmp_path, "一回目", {"review": [1, 0], "stopper": [1]})
    assert ran == ["pick", "early", "review", "fix-review", "review", "stopper"] and "結果: 止まった" in text
    ran, seen, text = run(tmp_path, "再開", {}, start="gate")
    assert ran == ["gate"] and "結果: 関門" in text
    assert "### ステップ review（exit=0、前の実行）\n一回目 の review（exit=0）" in seen[0]
    assert "### ステップ early（exit=0、前の実行）\n一回目 の early（exit=0）" in seen[0]
    assert "（入力なし）" not in seen[0]


def test_results_before_the_last_run_without_from_are_not_given(tmp_path):
    """1676-3・1676-4: `--from` の無い実行より前の結果は渡らず、無い入力は名前と理由が載る。"""
    run(tmp_path, "一回目", {"stopper": [1]})  # pick → early → review → stopper で止まる
    ran, _, _ = run(tmp_path, "二回目", {"pick": [1], "stopper": [1]})  # early を通らない
    assert ran == ["pick", "review", "stopper"]
    _, seen, _ = run(tmp_path, "再開", {}, start="gate")
    assert "### ステップ review（exit=0、前の実行）\n二回目 の review（exit=0）" in seen[0]
    assert "### ステップ early（今の実行でまだ流れておらず、前の実行の記録にも無い）" in seen[0]
    assert "一回目" not in seen[0]


def test_a_step_run_in_this_run_wins_over_the_previous_run(tmp_path):
    """1676-4: 今の実行で流れたステップは、前の実行の結果より今の結果が先に効く。"""
    run(tmp_path, "一回目", {"stopper": [1]})
    ran, seen, _ = run(tmp_path, "再開", {}, start="review")
    assert ran == ["review", "stopper", "gate"]
    assert "### ステップ review（exit=0）\n再開 の review（exit=0）" in seen[0]
    assert "### ステップ early（exit=0、前の実行）\n一回目 の early（exit=0）" in seen[0]


def test_a_fresh_run_names_inputs_that_have_not_run_yet(tmp_path):
    """`--from` の無い実行では前の結果を読まず、まだ流れていない入力を名前つきで渡す。"""
    run(tmp_path, "一回目", {"stopper": [1]})
    _, seen, _ = run(tmp_path, "二回目", {"pick": [1]})
    assert "### ステップ early（この実行でまだ流れていない）" in seen[0] and "一回目" not in seen[0]
