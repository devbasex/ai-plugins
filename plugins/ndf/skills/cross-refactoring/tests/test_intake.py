"""取り込みの共通手順（#728 / #933）。

結果を残さなかった起動を閉じる手順（範囲の確定 → 取り消し → 結末の記録）を確かめる。
版 2 でこの手順を使うのは最終ゲートの修正（`merge-final-fix`）である。**共通層の読み取りは差し替えない。** 一時ディレクトリに
結果ファイルと監視の結果ファイルを置いて本物を通す。
"""
from __future__ import annotations

import json
import types

import pytest

from crossref_helpers import make_state_v2, read_state


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

    outcome = gitfacts.read_result(state, "agy", "implement")

    assert (outcome.payload, outcome.reason) == (None, "missing")
    assert capsys.readouterr() == ("", "")


def test_the_monitor_reason_decides_whether_the_same_agent_can_be_relaunched(
    gitfacts, tmp_path
):
    """AC2: 無進捗は起動し直せる。利用上限は起動し直せない。"""
    state = {"id": 130, "tmp_dir": str(tmp_path)}
    state_path = tmp_path / "dummy"

    (tmp_path / "agy-implement-rf130-monitor.json").write_text(
        json.dumps({"reason": "stalled", "detail": "無進捗"}), encoding="utf-8")
    stalled = gitfacts.read_result(state, "agy", "implement")

    (tmp_path / "claude-final-fix-monitor.json").write_text(
        json.dumps({"reason": "usage_limit", "detail": "上限"}), encoding="utf-8")
    limited = gitfacts.read_result(state, "claude", "final-fix")

    assert (stalled.reason, stalled.relaunch_same_agent) == ("stalled", True)
    assert (limited.reason, limited.relaunch_same_agent) == ("usage_limit", False)
    assert state_path.exists() is False


def test_the_stem_matches_the_template_the_orchestrator_passes_to_the_monitor(paths):
    """AC3: 名前の幹は、骨組みが監視へ渡す雛形を担当名で埋めた値と一致する（I3）。"""
    expected = {
        "propose": "{agent}-propose-rf$ID",
        "plan": "{agent}-plan-rf$ID",
        "add-tests": "{agent}-add-tests-rf$ID",
        "implement": "{agent}-implement-rf$ID",
        "fix": "{agent}-fix-rf$ID",
        "judge-test-changes": "{agent}-judge-test-changes-rf$ID",
        "final-fix": "{agent}-final-fix",
    }
    for phase, template in expected.items():
        built = template.replace("{agent}", "codex").replace("$ID", "130")
        assert paths.stem_for("codex", phase, 130) == built


# ---------- 取り込みの共通手順（AC4〜AC8） ----------

def _scope(intake, gate, attempt=1):
    return intake.IntakeScope(
        holder=gate, base_key="fix_base_sha", records=gate, phase="final-fix",
        attempt=attempt, impl="agy", label=f"final-gate-fix{attempt}",
    )


@pytest.fixture
def gated(tmp_path):
    """最終ゲートの修正を待つ版 2 の状態ファイル。"""
    return make_state_v2(
        tmp_path, tmp_path / "work", phase="final",
        final_gate={"fix_rounds": 1, "checks": [], "impl": "agy", "fix_base_sha": "base0"},
    )


def test_a_commit_in_range_is_reverted_and_the_base_moves_to_the_new_head(
    intake, patch_lib, gated, no_git
):
    """AC4 / AC5: 範囲のコミットを取り消し、起点を取り消し後の先端へ進める。"""
    state = read_state(gated)
    gate = state["final_gate"]
    patch_lib("commits_in_range", lambda work, base, head: ["c2", "c1"])
    patch_lib("git_out", lambda work, args, **kw: "newhead")

    closed = intake.close_without_result(
        gated, state, _scope(intake, gate), _outcome(reason="stalled", detail="無進捗"))

    assert (closed.reverted, closed.range_unknown) == (2, False)
    assert gate["fix_base_sha"] == "newhead"
    assert gate["failed_attempts"] == [{
        "phase": "final-fix", "attempt": 1, "impl": "agy", "reason": "stalled",
        "detail": "無進捗", "at": gate["failed_attempts"][0]["at"], "reverted": 2,
    }]
    assert [c[-1] for c in no_git if c[:2] == ["git", "revert"]] == ["c2", "c1"]
    assert read_state(gated)["final_gate"]["fix_base_sha"] == "newhead"


def test_an_empty_range_runs_neither_revert_nor_push(intake, patch_lib, gated, no_git):
    """AC6: 範囲にコミットが無ければ、取り消しも公開もしない。"""
    state = read_state(gated)
    gate = state["final_gate"]
    patch_lib("commits_in_range", lambda work, base, head: [])
    patch_lib("git_out", lambda work, args, **kw: "head0")

    closed = intake.close_without_result(gated, state, _scope(intake, gate), _outcome("missing"))

    assert closed.reverted == 0
    assert [c for c in no_git if c[:2] in (["git", "revert"], ["git", "push"])] == []
    assert len(gate["failed_attempts"]) == 1


def test_the_same_attempt_is_not_recorded_twice(intake, patch_lib, gated):
    """AC7: 同じ工程・同じ試行番号は記録済みと分かる。"""
    state = read_state(gated)
    gate = state["final_gate"]
    patch_lib("commits_in_range", lambda work, base, head: [])
    patch_lib("git_out", lambda work, args, **kw: "head0")
    scope = _scope(intake, gate)

    assert intake.already_closed(scope) is False
    intake.close_without_result(gated, state, scope, _outcome("missing"))
    assert intake.already_closed(scope) is True
    assert intake.already_closed(_scope(intake, gate, attempt=2)) is False

    intake.close_without_result(gated, state, scope, _outcome("missing"))
    assert len(gate["failed_attempts"]) == 2, "呼べば足すのは共通手順の責務である"


def test_a_range_that_cannot_be_determined_records_nothing(intake, patch_lib, gated, no_git):
    """AC8: 範囲を確定できないときは、取り消しも記録もしない。"""
    state = read_state(gated)
    gate = state["final_gate"]
    patch_lib("commits_in_range", lambda work, base, head: None)
    patch_lib("git_out", lambda work, args, **kw: "head0")

    closed = intake.close_without_result(gated, state, _scope(intake, gate), _outcome("missing"))

    assert closed.range_unknown is True
    assert "failed_attempts" not in gate
    assert [c for c in no_git if c[:2] == ["git", "revert"]] == []


def test_the_failed_agents_are_listed_in_the_order_they_were_recorded(intake, gated):
    """結果を残さなかった担当を、記録の順で読めること（工程ごと）。"""
    state = read_state(gated)
    gate = state["final_gate"]
    gate["failed_attempts"] = [
        {"phase": "final-fix", "attempt": 1, "impl": "agy", "reason": "stalled"},
        {"phase": "other", "attempt": 1, "impl": "kiro", "reason": "missing"},
        {"phase": "final-fix", "attempt": 2, "impl": "codex", "reason": "missing"},
    ]

    assert intake.failed_impls(_scope(intake, gate)) == ["agy", "codex"]
