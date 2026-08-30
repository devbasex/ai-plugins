"""提案のマージ（重複排除 / 語彙 / しきい値 / 上限件数 / 優先度）のテスト。"""
from __future__ import annotations

import pytest

from crossref_helpers import make_state, read_state, write_result


def proposal(**over):
    base = {
        "path": "src/foo.py",
        "symbol": "Foo.handle",
        "smell": "long_method",
        "technique": "extract_method",
        "severity": "major",
        "rationale": "検証・変換・永続化が同居している",
        "plan": "1. 検証部を抽出\n2. 変換部を抽出",
        "test_gap": False,
        "estimated_diff_lines": 40,
    }
    base.update(over)
    return base


# ---------- 重複排除 ----------

def test_same_target_and_smell_is_merged(refactor):
    adopted, _ = refactor.merge_proposals({
        "codex": [proposal()],
        "gemini": [proposal(rationale="短い")],
        "kiro": [proposal(path="src/bar.py")],
    })
    assert len(adopted) == 2
    merged = next(i for i in adopted if i["path"] == "src/foo.py")
    assert sorted(merged["proposed_by"]) == ["codex", "gemini"]


def test_merged_item_keeps_the_most_specific_text(refactor):
    """`rationale` と `plan` は最も具体的なもの（長い方）を採る。"""
    adopted, _ = refactor.merge_proposals({
        "codex": [proposal(rationale="長い", plan="短")],
        "gemini": [proposal(rationale="短", plan="とても長い手順の説明")],
    })
    assert adopted[0]["rationale"] == "長い"
    assert adopted[0]["plan"] == "とても長い手順の説明"


def test_merged_item_takes_the_higher_severity_and_larger_estimate(refactor):
    """見積りを楽観側へ倒さない。差分予算の検証が甘くなるため。"""
    adopted, _ = refactor.merge_proposals({
        "codex": [proposal(severity="minor", estimated_diff_lines=10)],
        "gemini": [proposal(severity="critical", estimated_diff_lines=90)],
    })
    assert adopted[0]["severity"] == "critical"
    assert adopted[0]["estimated_diff_lines"] == 90


def test_test_gap_is_sticky(refactor):
    """1 者でもテストが乏しいと申告したら、固定テストの先行を要求する側へ倒す。"""
    adopted, _ = refactor.merge_proposals({
        "codex": [proposal(test_gap=False)],
        "gemini": [proposal(test_gap=True)],
    })
    assert adopted[0]["test_gap"] is True


def test_different_smell_on_same_target_is_not_merged(refactor):
    adopted, _ = refactor.merge_proposals({
        "codex": [proposal(smell="long_method")],
        "gemini": [proposal(smell="deep_nesting", technique="flatten_conditional")],
    })
    assert len(adopted) == 2


# ---------- 語彙 ----------

def test_unknown_smell_is_degraded_and_dropped(refactor):
    """語彙外は unknown へ降格し、しきい値で自動的に落ちる。"""
    adopted, deferred = refactor.merge_proposals({
        "codex": [proposal(smell="spaghetti")],
    })
    assert adopted == []
    assert deferred[0]["smell"] == "unknown"
    assert deferred[0]["severity"] == "unknown"


def test_unknown_technique_is_degraded(refactor):
    adopted, deferred = refactor.merge_proposals({
        "codex": [proposal(technique="make_it_nicer")],
    })
    assert adopted == []
    assert deferred[0]["technique"] == "unknown"


def test_unknown_severity_is_degraded(refactor):
    adopted, deferred = refactor.merge_proposals({
        "codex": [proposal(severity="blocker")],
    })
    assert adopted == []
    assert deferred[0]["severity"] == "unknown"


def test_proposal_without_target_is_ignored(refactor):
    adopted, deferred = refactor.merge_proposals({
        "codex": [proposal(symbol=""), proposal()],
    })
    assert len(adopted) == 1
    assert deferred == []


# ---------- しきい値 ----------

def test_below_threshold_is_deferred(refactor):
    adopted, deferred = refactor.merge_proposals(
        {"codex": [proposal(severity="minor")]}, threshold="major"
    )
    assert adopted == []
    assert "しきい値" in deferred[0]["defer_reason"]


def test_at_threshold_is_adopted(refactor):
    adopted, _ = refactor.merge_proposals(
        {"codex": [proposal(severity="major")]}, threshold="major"
    )
    assert len(adopted) == 1


# ---------- 優先度と上限件数 ----------

def test_priority_is_agreement_then_severity_then_size(refactor):
    """小さく合意の多いものから直す。"""
    adopted, _ = refactor.merge_proposals({
        "codex": [
            proposal(symbol="A", severity="minor", estimated_diff_lines=5),
            proposal(symbol="B", severity="critical", estimated_diff_lines=200),
            proposal(symbol="C", severity="major", estimated_diff_lines=10),
        ],
        "gemini": [proposal(symbol="C", severity="major", estimated_diff_lines=10)],
    })
    assert [i["symbol"] for i in adopted] == ["C", "B", "A"]


def test_max_items_cuts_the_tail_into_deferred(refactor):
    adopted, deferred = refactor.merge_proposals(
        {"codex": [proposal(symbol=f"S{n}") for n in range(8)]}, max_items=3
    )
    assert len(adopted) == 3
    assert len(deferred) == 5
    assert all("上限" in d["defer_reason"] for d in deferred)


def test_previously_deferred_keys_are_excluded(refactor):
    """見送った項目を毎ラウンド再提案されると収束しない。"""
    adopted, deferred = refactor.merge_proposals(
        {"codex": [proposal()]},
        excluded_keys=[("src/foo.py", "Foo.handle", "long_method")],
    )
    assert adopted == []
    assert "対象外" in deferred[0]["defer_reason"]


# ---------- 重複率 ----------

def test_duplicate_rate_counts_shared_keys(refactor):
    prev = [("a", "b", "c"), ("d", "e", "f")]
    assert refactor.duplicate_rate(prev, prev) == 1.0
    assert refactor.duplicate_rate([("a", "b", "c")], prev) == 1.0
    assert refactor.duplicate_rate([("x", "y", "z")], prev) == 0.0


def test_duplicate_rate_is_zero_without_previous_round(refactor):
    assert refactor.duplicate_rate([("a", "b", "c")], []) == 0.0


# ---------- サブコマンド ----------

def test_merge_proposals_command_creates_items(
    refactor, tmp_path, env_tmp_dir, no_git, capsys
):
    state_path = make_state(tmp_path, rounds=[{
        "round": 1, "impl": "codex", "reviewers": ["gemini", "kiro"],
        "impl_model": {"requested": None, "observed": None},
        "reviewer_models": {}, "proposed": {}, "items": [],
        "apply": {"applied": [], "failed": []}, "fix_rounds": 0,
        "durations": {}, "reviews": [],
    }])
    env_tmp_dir(state_path)
    write_result(state_path, "codex-propose-rf130-r1", {"items": [proposal()]})
    write_result(state_path, "gemini-propose-rf130-r1", {"items": [proposal()]})
    write_result(state_path, "kiro-propose-rf130-r1", {"items": []})

    refactor.cmd_merge_proposals(type("A", (), {"id": 130})())

    state = read_state(state_path)
    assert [i["item_id"] for i in state["items"]] == ["R1-001"]
    assert state["rounds"][0]["adopted"] == 1
    assert state["rounds"][0]["proposed"] == {"codex": 1, "gemini": 1, "kiro": 0}
    assert state["phase"] == "apply"


def test_merge_proposals_command_exits_2_when_nothing_adopted(
    refactor, tmp_path, env_tmp_dir, no_git
):
    state_path = make_state(tmp_path, rounds=[{
        "round": 1, "impl": "codex", "reviewers": ["gemini", "kiro"],
        "impl_model": {"requested": None, "observed": None},
        "reviewer_models": {}, "proposed": {}, "items": [],
        "apply": {"applied": [], "failed": []}, "fix_rounds": 0,
        "durations": {}, "reviews": [],
    }])
    env_tmp_dir(state_path)
    for rt in ("codex", "gemini", "kiro"):
        write_result(state_path, f"{rt}-propose-rf130-r1", {"items": []})

    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_proposals(type("A", (), {"id": 130})())
    assert e.value.code == 2
    state = read_state(state_path)
    assert state["phase"] == "converged"
    # 呼び出し側は終了コード 2 で繰り返しを抜けるため advance を通らない。
    # ここで終了理由を確定させないと報告が「未終了」のままになる。
    assert state["final"] == "no_more_proposals"


def test_non_object_proposal_result_is_treated_as_empty(
    refactor, tmp_path, env_tmp_dir, no_git
):
    """結果が配列でもクラッシュせず、その 1 者の提案なしとして続けること。"""
    state_path = make_state(tmp_path, rounds=[{
        "round": 1, "impl": "codex", "reviewers": ["gemini", "kiro"],
        "impl_model": {"requested": None, "observed": None},
        "reviewer_models": {}, "proposed": {}, "items": [],
        "apply": {"applied": [], "failed": []}, "fix_rounds": 0,
        "durations": {}, "reviews": [],
    }])
    env_tmp_dir(state_path)
    write_result(state_path, "codex-propose-rf130-r1", ["配列で返ってきた"])
    write_result(state_path, "gemini-propose-rf130-r1", {"items": [proposal()]})
    write_result(state_path, "kiro-propose-rf130-r1", {"items": [proposal()]})

    refactor.cmd_merge_proposals(type("A", (), {"id": 130})())

    state = read_state(state_path)
    assert state["rounds"][0]["proposed"]["codex"] == 0
    assert len(state["items"]) == 1


def test_merge_proposals_is_idempotent(refactor, tmp_path, env_tmp_dir, no_git):
    """同じラウンドで叩き直しても項目を二重に作らないこと。

    進行を止めても再開できることが前提なので、統合済みなら前回と同じ結果を返す。
    """
    state_path = make_state(tmp_path, rounds=[{
        "round": 1, "impl": "codex", "reviewers": ["gemini", "kiro"],
        "impl_model": {"requested": None, "observed": None},
        "reviewer_models": {}, "proposed": {}, "items": [],
        "apply": {"applied": [], "failed": []}, "fix_rounds": 0,
        "durations": {}, "reviews": [],
    }])
    env_tmp_dir(state_path)
    for rt in ("codex", "gemini", "kiro"):
        write_result(state_path, f"{rt}-propose-rf130-r1", {"items": [proposal()]})

    args = type("A", (), {"id": 130})()
    refactor.cmd_merge_proposals(args)
    first = read_state(state_path)
    refactor.cmd_merge_proposals(args)
    second = read_state(state_path)

    assert [i["item_id"] for i in second["items"]] == ["R1-001"]
    assert second["items"] == first["items"]
    assert second["rounds"][0]["adopted"] == 1


def test_merge_proposals_replays_the_converged_exit_code(
    refactor, tmp_path, env_tmp_dir, no_git
):
    """採用 0 件で終わったラウンドを叩き直しても、同じ終了コードを返す。"""
    state_path = make_state(tmp_path, rounds=[{
        "round": 1, "impl": "codex", "reviewers": ["gemini", "kiro"],
        "impl_model": {"requested": None, "observed": None},
        "reviewer_models": {}, "proposed": {}, "items": [],
        "apply": {"applied": [], "failed": []}, "fix_rounds": 0,
        "durations": {}, "reviews": [],
    }])
    env_tmp_dir(state_path)
    for rt in ("codex", "gemini", "kiro"):
        write_result(state_path, f"{rt}-propose-rf130-r1", {"items": []})

    args = type("A", (), {"id": 130})()
    for _ in range(2):
        with pytest.raises(SystemExit) as e:
            refactor.cmd_merge_proposals(args)
        assert e.value.code == 2
    assert read_state(state_path)["items"] == []
