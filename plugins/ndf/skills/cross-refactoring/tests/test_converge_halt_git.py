"""検証で落ちた項目の次の手（`converge._plan_failing`）の現状固定テスト。

| 経路 | 確かめること |
| --- | --- |
| 判定の依頼 | 初めて落ちた項目は、実装担当と別の参加者へ成立の判定を頼み、その担い手を返す |
| 担い手が居ない | 参加者が実装担当だけなら「判定できない」を積み、修正を続ける（`None`） |
| 成立しない | 判定が成立しないなら `infeasible` の理由で打ち消す |
| 停滞・振動 | 修正担当を 1 回だけ替える。替えた後も停滞したら `stalled_after_switch` で打ち消す |
| 止める | 判定し直す担い手が居ない（`no_judge`）・替える修正担当が居ない（`no_fixer`）・打ち消しの衝突（`revert_conflict`） |

打ち消しの `git revert` は `revert_items` を差し替えて、渡した引数だけを記録する。
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def reverts(cmd_converge, monkeypatch):
    calls = []

    def fake(path, state, item_ids, reason):
        calls.append((list(item_ids), reason))
        return {}

    monkeypatch.setattr(cmd_converge, "revert_items", fake)
    return calls


def _state(runtimes=("claude", "codex")):
    return {"implementer": "claude", "host": "claude", "runtimes": list(runtimes)}


def _item(item_id="I-001", **extra):
    item = {"id": item_id, "status": "failing", "fix_count": 0, "failed_check": {"tests": ["tests/test_a.py::test_a"]}}
    item.update(extra)
    return item


def _stalled(item_id="I-001", verdict="stalled", **extra):
    history = [{"round": 1, "by": "claude", "failing": ["tests/test_a.py::test_a"], "progress": verdict}]
    return _item(item_id, fix_count=1, fix_history=history, **extra)


def _judged(feasible, rejudge, seq=1, by="codex"):
    return {"by": by, "feasible": feasible, "reason": "", "evidence": "", "at": "x", "rejudge": rejudge, "seq": seq}


def test_first_failure_asks_other_runtime_to_judge(cmd_converge, tmp_path, reverts):
    path = tmp_path / "state.json"
    state = _state()
    item = _item()

    assert cmd_converge._plan_failing(path, state, [item]) == "codex"

    req = state["feasibility_request"]
    assert req["items"] == ["I-001"]
    assert req["rejudge"] == {"I-001": False}
    assert req["by"] == "codex"
    assert item["fix_history"] == [{"round": 0, "by": "claude", "failing": ["tests/test_a.py::test_a"], "progress": "first"}]
    assert json.loads(path.read_text(encoding="utf-8"))["feasibility_request"]["by"] == "codex"
    assert reverts == []


def test_first_failure_without_judge_notes_unjudged_and_continues(cmd_converge, tmp_path, reverts):
    path = tmp_path / "state.json"
    state = _state(runtimes=("claude",))
    item = _item()

    assert cmd_converge._plan_failing(path, state, [item]) is None

    assert "feasibility_request" not in state
    [entry] = item["feasibility"]
    assert entry["by"] is None and entry["feasible"] is None
    assert entry["rejudge"] is False and entry["seq"] == 0
    assert "halt" not in state
    assert reverts == []
    assert path.exists()


def test_already_judged_first_failure_keeps_fixing(cmd_converge, tmp_path, reverts):
    state = _state()
    item = _item(feasibility=[_judged(True, False, seq=0)])

    assert cmd_converge._plan_failing(tmp_path / "state.json", state, [item]) is None

    assert "feasibility_request" not in state
    assert "fixer" not in item
    assert reverts == []


def test_infeasible_item_is_reverted(cmd_converge, tmp_path, reverts):
    state = _state()
    item = _item(feasibility=[_judged(False, False, seq=0)])
    other = _item("I-002", feasibility=[_judged(True, False, seq=0)])

    assert cmd_converge._plan_failing(tmp_path / "state.json", state, [item, other]) is None

    assert reverts == [(["I-001"], cmd_converge.fixloop.REASON_TEXT[cmd_converge.fixloop.INFEASIBLE])]
    assert item["revert_reason"] == cmd_converge.fixloop.INFEASIBLE
    assert "revert_reason" not in other


def test_stalled_item_without_rejudge_asks_for_rejudge(cmd_converge, tmp_path, reverts):
    state = _state()
    item = _stalled(feasibility=[_judged(True, False, seq=0)])

    assert cmd_converge._plan_failing(tmp_path / "state.json", state, [item]) == "codex"

    assert state["feasibility_request"]["rejudge"] == {"I-001": True}
    assert reverts == []


@pytest.mark.parametrize("verdict", ["stalled", "oscillating"])
def test_stalled_and_feasible_switches_fixer_once(cmd_converge, tmp_path, reverts, verdict, capsys):
    state = _state()
    item = _stalled(verdict=verdict, feasibility=[_judged(True, True)])

    assert cmd_converge._plan_failing(tmp_path / "state.json", state, [item]) is None

    assert item["fixer"] == "codex"
    assert item["fixer_switched"] is True
    assert "halt" not in state
    assert reverts == []
    word = "停滞" if verdict == "stalled" else "振動"
    assert f"I-001 の修正が{word}したため、修正担当を codex へ替えます" in capsys.readouterr().err


def test_stalled_after_switch_is_reverted(cmd_converge, tmp_path, reverts):
    state = _state()
    item = _stalled(fixer="codex", fixer_switched=True, feasibility=[_judged(True, True)])

    assert cmd_converge._plan_failing(tmp_path / "state.json", state, [item]) is None

    assert reverts == [(["I-001"], cmd_converge.fixloop.REASON_TEXT[cmd_converge.fixloop.STALLED_AFTER_SWITCH])]
    assert item["revert_reason"] == cmd_converge.fixloop.STALLED_AFTER_SWITCH


def test_stalled_without_rejudger_halts_no_judge(cmd_converge, tmp_path, reverts):
    path = tmp_path / "state.json"
    state = _state(runtimes=("claude",))
    item = _stalled(feasibility=[_judged(True, False, seq=0)])

    with pytest.raises(cmd_converge.Halt, match="成立を判定し直す担い手が居ない"):
        cmd_converge._plan_failing(path, state, [item])

    assert state["halt"]["reason"] == "no_judge"
    assert state["halt"]["item"] == "I-001"
    assert item["feasibility"][-1]["rejudge"] is True and item["feasibility"][-1]["feasible"] is None
    assert json.loads(path.read_text(encoding="utf-8"))["halt"]["reason"] == "no_judge"
    assert reverts == []


def test_stalled_without_other_fixer_halts_no_fixer(cmd_converge, tmp_path, reverts):
    path = tmp_path / "state.json"
    state = _state(runtimes=("claude",))
    item = _stalled(feasibility=[_judged(True, True)])

    with pytest.raises(cmd_converge.Halt, match="替える修正担当が居ない"):
        cmd_converge._plan_failing(path, state, [item])

    assert state["halt"]["reason"] == "no_fixer"
    assert state["halt"]["detail"] == "I-001 の修正が停滞し、替える修正担当が居ない"
    assert json.loads(path.read_text(encoding="utf-8"))["halt"]["reason"] == "no_fixer"
    assert reverts == []


def test_revert_conflict_halts(cmd_converge, undo, tmp_path, monkeypatch):
    def conflict(path, state, item_ids, reason):
        raise undo.UndoConflict("打ち消しが衝突した", item_ids[0], "abc123")

    monkeypatch.setattr(cmd_converge, "revert_items", conflict)
    path = tmp_path / "state.json"
    state = _state()
    item = _item(feasibility=[_judged(False, False, seq=0)])

    with pytest.raises(cmd_converge.Halt, match="打ち消しが衝突した"):
        cmd_converge._plan_failing(path, state, [item])

    assert state["halt"]["reason"] == "revert_conflict"
    assert state["halt"]["item"] == "I-001"
    assert json.loads(path.read_text(encoding="utf-8"))["halt"]["reason"] == "revert_conflict"
