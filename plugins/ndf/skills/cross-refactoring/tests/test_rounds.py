"""提案ラウンドの開始・再開・収束判定のテスト。"""
from __future__ import annotations

import json

import pytest

from crossref_helpers import make_state, read_state


def _args(state_id=130):
    return type("A", (), {"id": state_id})()


def _round(round_no, **over):
    base = {
        "round": round_no, "impl": "codex", "reviewers": ["gemini", "kiro"],
        "impl_model": {"requested": None, "observed": None},
        "reviewer_models": {}, "proposed": {}, "merged": 1, "adopted": 1, "deferred": 0,
        "items": [], "apply": {"applied": [], "failed": []},
        "fix_rounds": 0, "durations": {}, "reviews": [],
        "proposal_keys": [["src/a.py", "A", "long_method"]],
    }
    base.update(over)
    return base


# ---------- start-round ----------

def test_start_round_opens_and_records_assignment(refactor, tmp_path, env_tmp_dir):
    state_path = make_state(tmp_path)
    env_tmp_dir(state_path)
    refactor.cmd_start_round(_args())

    state = read_state(state_path)
    assert len(state["rounds"]) == 1
    entry = state["rounds"][0]
    assert entry["impl"] == "codex"
    assert entry["reviewers"] == ["gemini", "kiro"]
    assert entry["impl"] not in entry["reviewers"]
    assert state["phase"] == "propose"


def test_start_round_is_idempotent_on_resume(refactor, tmp_path, env_tmp_dir):
    """同一ラウンドを開き直しても担当が変わらないこと。"""
    state_path = make_state(tmp_path)
    env_tmp_dir(state_path)
    refactor.cmd_start_round(_args())
    first = read_state(state_path)["rounds"][0]

    # ラウンドが未完了のまま再実行しても新しいラウンドを開かない
    state = read_state(state_path)
    state["rounds"] = [first]
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    refactor.cmd_start_round(_args())

    state = read_state(state_path)
    assert len(state["rounds"]) == 2, "2 回目は次のラウンドを開く"
    assert state["rounds"][0] == first, "既に開いたラウンドの割り当ては変わらない"


def test_start_round_stops_at_max_outer_rounds(refactor, tmp_path, env_tmp_dir):
    state_path = make_state(tmp_path, max_outer_rounds=2,
                            rounds=[_round(1), _round(2)])
    env_tmp_dir(state_path)
    with pytest.raises(SystemExit) as e:
        refactor.cmd_start_round(_args())
    assert e.value.code == 1
    assert read_state(state_path)["final"] == "max_outer_rounds"


def test_start_round_stops_when_already_final(refactor, tmp_path, env_tmp_dir):
    state_path = make_state(tmp_path, final="no_more_proposals")
    env_tmp_dir(state_path)
    with pytest.raises(SystemExit) as e:
        refactor.cmd_start_round(_args())
    assert e.value.code == 1


# ---------- advance（収束判定） ----------

def test_advance_continues_when_progress_is_made(refactor, tmp_path, env_tmp_dir):
    state_path = make_state(tmp_path, rounds=[_round(1)])
    env_tmp_dir(state_path)
    refactor.cmd_advance(_args())
    assert read_state(state_path)["final"] is None


def test_advance_stops_when_nothing_adopted(refactor, tmp_path, env_tmp_dir):
    state_path = make_state(tmp_path, rounds=[_round(1, adopted=0)])
    env_tmp_dir(state_path)
    with pytest.raises(SystemExit) as e:
        refactor.cmd_advance(_args())
    assert e.value.code == 1
    assert read_state(state_path)["final"] == "no_more_proposals"


def test_advance_stops_at_max_outer_rounds(refactor, tmp_path, env_tmp_dir):
    state_path = make_state(tmp_path, max_outer_rounds=1, rounds=[_round(1)])
    env_tmp_dir(state_path)
    with pytest.raises(SystemExit):
        refactor.cmd_advance(_args())
    assert read_state(state_path)["final"] == "max_outer_rounds"


def test_advance_stops_on_duplicate_proposals(refactor, tmp_path, env_tmp_dir):
    """同じ提案が毎ラウンド出続けて終わらない状態を収束と判定する。"""
    keys = [["src/a.py", "A", "long_method"], ["src/b.py", "B", "duplication"]]
    state_path = make_state(
        tmp_path, max_outer_rounds=5,
        rounds=[_round(1, proposal_keys=keys), _round(2, proposal_keys=keys)],
    )
    env_tmp_dir(state_path)
    with pytest.raises(SystemExit):
        refactor.cmd_advance(_args())
    assert read_state(state_path)["final"] == "duplicate_proposals"


def test_advance_allows_partially_overlapping_proposals(refactor, tmp_path, env_tmp_dir):
    """重複率がしきい値未満なら続ける。"""
    prev = [["src/a.py", "A", "long_method"], ["src/b.py", "B", "duplication"]]
    cur = [["src/a.py", "A", "long_method"], ["src/c.py", "C", "deep_nesting"],
           ["src/d.py", "D", "dead_code"]]
    state_path = make_state(
        tmp_path, max_outer_rounds=5,
        rounds=[_round(1, proposal_keys=prev), _round(2, proposal_keys=cur)],
    )
    env_tmp_dir(state_path)
    refactor.cmd_advance(_args())
    assert read_state(state_path)["final"] is None


# ---------- 報告 ----------

def test_report_renders_tables(refactor, tmp_path, env_tmp_dir, capsys):
    state_path = make_state(
        tmp_path,
        rounds=[_round(1, items=["R1-001"], apply={"applied": ["R1-001"], "failed": []},
                       reviews=[{"round": 1, "gemini": "APPROVE", "kiro": "APPROVE",
                                 "findings": []}])],
        items=[{"item_id": "R1-001", "round": 1, "path": "src/a.py", "symbol": "A",
                "smell": "long_method", "technique": "extract_method",
                "severity": "major", "proposed_by": ["codex", "gemini"],
                "status": "done", "commits": ["abc"]}],
        deferred_items=[{"path": "src/z.py", "symbol": "Z", "smell": "duplication",
                         "round": 1, "defer_reason": "しきい値未満"}],
    )
    env_tmp_dir(state_path)
    refactor.cmd_report(type("A", (), {"id": 130, "metrics": True})())

    out = capsys.readouterr().out
    assert "## ラウンド" in out
    assert "## 改善項目" in out
    assert "## 見送った提案" in out
    assert "比較として読むときの限界" in out
    assert "R1-001" in out


def test_status_reports_cohorts(refactor, tmp_path, env_tmp_dir, capsys):
    state_path = make_state(tmp_path)
    env_tmp_dir(state_path)
    refactor.cmd_status(_args())
    out = capsys.readouterr().out
    assert "提案・レビュー: codex / gemini / kiro" in out
    assert "適用の母集合: claude / codex / kiro" in out
