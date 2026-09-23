"""取り込みの共通手順（#728）。

3 つの取り込み（適用・修正・最終ゲートの修正）が、結果を残さなかった起動を同じ
手順で閉じることを確かめる。**共通層の読み取りは差し替えない。** 一時ディレクトリに
結果ファイルと監視の結果ファイルを置いて本物を通す。
"""
from __future__ import annotations

import json
import types

import pytest

from crossref_helpers import make_state, read_state


def _outcome(reason=None, payload=None, detail="", relaunch=True):
    """`LaunchOutcome` と同じ欄を持つ値。読む側は 5 つの欄しか見ない。"""
    return types.SimpleNamespace(
        payload=payload, reason=reason, detail=detail, monitor=None,
        relaunch_same_agent=relaunch,
    )


def _write_monitor(state_path, stem, reason, detail="打ち切りました"):
    out = state_path.parent / f"{stem}-monitor.json"
    out.write_text(
        json.dumps({"reason": reason, "detail": detail}, ensure_ascii=False),
        encoding="utf-8",
    )
    return out


# ---------- 結末の読み取り（AC1 / AC2 / AC3） ----------

def test_a_missing_result_file_is_read_as_a_value(gitfacts, tmp_path, capsys):
    """AC1: 結果ファイルが無くても中断せず、何も出力しない。"""
    state = {"id": 130, "tmp_dir": str(tmp_path)}

    outcome = gitfacts.read_result(state, "agy", "apply", 1)

    assert (outcome.payload, outcome.reason) == (None, "missing")
    assert capsys.readouterr() == ("", "")


def test_the_monitor_reason_decides_whether_the_same_agent_can_be_relaunched(
    gitfacts, tmp_path
):
    """AC2: 無進捗は起動し直せる。利用上限は起動し直せない。"""
    state = {"id": 130, "tmp_dir": str(tmp_path)}
    state_path = tmp_path / "dummy"

    (tmp_path / "agy-apply-r1-monitor.json").write_text(
        json.dumps({"reason": "stalled", "detail": "無進捗"}), encoding="utf-8")
    stalled = gitfacts.read_result(state, "agy", "apply", 1)

    (tmp_path / "claude-apply-r1-monitor.json").write_text(
        json.dumps({"reason": "usage_limit", "detail": "上限"}), encoding="utf-8")
    limited = gitfacts.read_result(state, "claude", "apply", 1)

    assert (stalled.reason, stalled.relaunch_same_agent) == ("stalled", True)
    assert (limited.reason, limited.relaunch_same_agent) == ("usage_limit", False)
    assert state_path.exists() is False


def test_the_stem_matches_the_template_the_orchestrator_passes_to_the_monitor(paths):
    """AC3: 名前の幹は、骨組みが監視へ渡す雛形を担当名で埋めた値と一致する。"""
    expected = {
        "apply": "{agent}-apply-r$ROUND",
        "fix": "{agent}-fix-r$ROUND",
        "final-fix": "{agent}-final-fix",
    }
    for phase, template in expected.items():
        built = template.replace("{agent}", "codex").replace("$ROUND", "2")
        assert paths.stem_for("codex", phase, 130, 2) == built


# ---------- 取り込みの共通手順（AC4〜AC8） ----------

def _scope(intake, holder, records, phase, base_key="fix_base_sha", mirror=None):
    return intake.IntakeScope(
        holder=holder, base_key=base_key, records=records, phase=phase,
        attempt=1, impl="agy", label="R1-A1", mirror=mirror,
    )


@pytest.fixture
def one_round(tmp_path):
    """1 提案ラウンド・1 群の状態ファイル。"""
    return make_state(
        tmp_path,
        items=[{"item_id": "R1-001", "path": "src/a.py", "symbol": "f",
                "smell": "long_method", "status": "pending", "round": 1,
                "commits": []}],
        rounds=[{
            "round": 1, "impl": "codex", "items": ["R1-001"],
            "apply_base_sha": "base0", "fix_base_sha": "base0",
            "fix_rounds": 0, "fix_attempts": 1,
            "apply_rounds": [{
                "apply_round": 1, "impl": "agy",
                "impl_model": {"requested": None, "observed": None},
                "items": ["R1-001"], "status": "pending",
                "base_sha": "base0", "head_sha": None, "fix_rounds": 0,
                "attempt": 1,
            }],
            "apply_round": 1, "apply": {"applied": [], "failed": []},
            "durations": {}, "reviews": [],
        }],
        final_gate={"fix_rounds": 1, "checks": [], "impl": "agy",
                    "fix_base_sha": "base0"},
    )


@pytest.mark.parametrize(
    "phase,base_key,records_from",
    [("apply", "apply_base_sha", "group"),
     ("fix", "fix_base_sha", "group"),
     ("final-fix", "fix_base_sha", "gate")],
)
def test_a_commit_in_range_is_reverted_and_the_base_moves_to_the_new_head(
    intake, patch_lib, one_round, no_git, phase, base_key, records_from
):
    """AC4 / AC5: 範囲のコミットを取り消し、起点を取り消し後の先端へ進める。"""
    state = read_state(one_round)
    entry = state["rounds"][0]
    group = entry["apply_rounds"][0]
    gate = state["final_gate"]
    holder = gate if records_from == "gate" else entry
    records = gate if records_from == "gate" else group
    patch_lib("commits_in_range", lambda work, base, head: ["c2", "c1"])
    patch_lib("git_out", lambda work, args, **kw: "newhead")
    scope = _scope(intake, holder, records, phase, base_key=base_key,
                   mirror=group if phase == "apply" else None)

    closed = intake.close_without_result(
        one_round, state, scope, _outcome(reason="stalled", detail="無進捗"))

    assert (closed.reverted, closed.range_unknown) == (2, False)
    assert holder[base_key] == "newhead"
    if phase == "apply":
        assert group["base_sha"] == "newhead"
    assert records["failed_attempts"] == [{
        "phase": phase, "attempt": 1, "impl": "agy", "reason": "stalled",
        "detail": "無進捗", "at": records["failed_attempts"][0]["at"], "reverted": 2,
    }]
    assert [c[-1] for c in no_git if c[:2] == ["git", "revert"]] == ["c2", "c1"]


def test_an_empty_range_runs_neither_revert_nor_push(
    intake, patch_lib, one_round, no_git
):
    """AC6: 範囲にコミットが無ければ、取り消しも公開もしない。"""
    state = read_state(one_round)
    entry = state["rounds"][0]
    group = entry["apply_rounds"][0]
    patch_lib("commits_in_range", lambda work, base, head: [])
    patch_lib("git_out", lambda work, args, **kw: "head0")
    scope = _scope(intake, entry, group, "fix")

    closed = intake.close_without_result(one_round, state, scope, _outcome("missing"))

    assert closed.reverted == 0
    assert [c for c in no_git if c[:2] in (["git", "revert"], ["git", "push"])] == []
    assert len(group["failed_attempts"]) == 1


def test_the_same_attempt_is_not_recorded_twice(intake, patch_lib, one_round):
    """AC7: 同じ工程・同じ試行番号は 1 件しか記録しない。"""
    state = read_state(one_round)
    entry = state["rounds"][0]
    group = entry["apply_rounds"][0]
    patch_lib("commits_in_range", lambda work, base, head: [])
    patch_lib("git_out", lambda work, args, **kw: "head0")
    scope = _scope(intake, entry, group, "fix")

    intake.close_without_result(one_round, state, scope, _outcome("missing"))
    assert intake.already_closed(scope) is True

    intake.close_without_result(one_round, state, scope, _outcome("missing"))
    assert len(group["failed_attempts"]) == 2, "呼べば足すのは共通手順の責務である"


def test_a_range_that_cannot_be_determined_records_nothing(
    intake, patch_lib, one_round, no_git
):
    """AC8: 範囲を確定できないときは、取り消しも記録もしない。"""
    state = read_state(one_round)
    entry = state["rounds"][0]
    group = entry["apply_rounds"][0]
    patch_lib("commits_in_range", lambda work, base, head: None)
    patch_lib("git_out", lambda work, args, **kw: "head0")
    scope = _scope(intake, entry, group, "fix")

    closed = intake.close_without_result(one_round, state, scope, _outcome("missing"))

    assert closed.range_unknown is True
    assert "failed_attempts" not in group
    assert [c for c in no_git if c[:2] == ["git", "revert"]] == []


def test_the_failed_agents_are_listed_in_the_order_they_were_recorded(
    intake, patch_lib, one_round
):
    """交代先を決めるために、失敗した担当を記録の順で読めること。"""
    state = read_state(one_round)
    entry = state["rounds"][0]
    group = entry["apply_rounds"][0]
    group["failed_attempts"] = [
        {"phase": "apply", "attempt": 1, "impl": "agy", "reason": "stalled"},
        {"phase": "fix", "attempt": 1, "impl": "kiro", "reason": "missing"},
        {"phase": "apply", "attempt": 2, "impl": "codex", "reason": "missing"},
    ]
    scope = _scope(intake, entry, group, "apply", base_key="apply_base_sha")

    assert intake.failed_impls(scope) == ["agy", "codex"]
