"""提案の統合と候補の切り出し（#933 の AC9 と決定 17）。

鍵（`path` + `symbol` + `smell`）が同じ提案を機械的にまとめ、語彙外は `vocabulary`、
しきい値未満は `threshold` で見送る。残りを `path` + `symbol` の組の単位で上位 30 組、
組の中は上位 3 件まで改修計画へ渡し、外れたものは `rank` で見送る。
"""
from __future__ import annotations

import argparse

import pytest

from crossref_helpers import make_state_v2, read_state, write_result


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


def _reasons(deferred):
    return [reason for _, reason in deferred]


# ---------- 鍵が同じ提案の統合 ----------

def test_same_target_and_smell_is_merged(proposals):
    candidates, _ = proposals.build_candidates({
        "codex": [proposal()],
        "agy": [proposal(rationale="短い")],
        "kiro": [proposal(path="src/bar.py")],
    })
    assert len(candidates) == 2
    merged = next(i for i in candidates if i["path"] == "src/foo.py")
    assert sorted(merged["proposed_by"]) == ["agy", "codex"]


def test_merged_item_keeps_the_most_specific_text(proposals):
    """`rationale` と `plan` は最も具体的なもの（長い方）を採る。"""
    candidates, _ = proposals.build_candidates({
        "codex": [proposal(rationale="長い", plan="短")],
        "agy": [proposal(rationale="短", plan="とても長い手順の説明")],
    })
    assert candidates[0]["rationale"] == "長い"
    assert candidates[0]["plan"] == "とても長い手順の説明"


def test_merged_item_takes_the_higher_severity_and_larger_estimate(proposals):
    """見積りを楽観側へ倒さない。差分予算の検証が甘くなるため。"""
    candidates, _ = proposals.build_candidates({
        "codex": [proposal(severity="minor", estimated_diff_lines=10)],
        "agy": [proposal(severity="critical", estimated_diff_lines=90)],
    })
    assert candidates[0]["severity"] == "critical"
    assert candidates[0]["estimated_diff_lines"] == 90


def test_a_proposal_without_path_or_symbol_is_ignored(proposals):
    candidates, deferred = proposals.build_candidates({"codex": [proposal(symbol="")]})
    assert candidates == [] and deferred == []


# ---------- 語彙としきい値（AC9） ----------

@pytest.mark.parametrize("field", ["smell", "technique", "severity"])
def test_an_out_of_vocabulary_proposal_is_deferred_as_vocabulary(proposals, field):
    candidates, deferred = proposals.build_candidates({"codex": [proposal(**{field: "日本語"})]})
    assert candidates == []
    assert _reasons(deferred) == ["vocabulary"]


def test_a_proposal_below_the_threshold_is_deferred_as_threshold(proposals):
    candidates, deferred = proposals.build_candidates(
        {"codex": [proposal(severity="minor")]}, threshold="major")
    assert candidates == []
    assert _reasons(deferred) == ["threshold"]


# ---------- 候補の切り出し（決定 17） ----------

def test_candidates_are_ordered_by_agreement_then_severity(proposals):
    candidates, _ = proposals.build_candidates({
        "codex": [proposal(symbol="a", severity="critical"), proposal(symbol="b")],
        "kiro": [proposal(symbol="b")],
    })
    assert [c["symbol"] for c in candidates] == ["b", "a"]
    assert [c["id"] for c in candidates] == ["C-001", "C-002"]


def test_only_the_top_30_groups_are_passed_to_the_plan(proposals):
    items = [proposal(symbol=f"f{n:02d}") for n in range(35)]
    candidates, deferred = proposals.build_candidates({"codex": items})
    assert len(candidates) == 30
    assert _reasons(deferred) == ["rank"] * 5


def test_a_group_passes_at_most_three_proposals_and_does_not_use_a_group_slot(proposals):
    """組の中は 3 件まで。4 件目は `rank`。組の数は件数で減らない。"""
    same = [proposal(smell=s) for s in ("long_method", "deep_nesting", "magic_value",
                                         "dead_code")]
    others = [proposal(symbol=f"g{n:02d}") for n in range(29)]
    candidates, deferred = proposals.build_candidates({"codex": same + others})
    assert len([c for c in candidates if c["symbol"] == "Foo.handle"]) == 3
    assert len({(c["path"], c["symbol"]) for c in candidates}) == 30
    assert _reasons(deferred) == ["rank"]


# ---------- 取り込み（merge-proposals） ----------

def _run(cmd_propose, path):
    cmd_propose.cmd_merge_proposals(argparse.Namespace(id=130))


def test_merge_proposals_records_candidates_and_the_propose_phase(
        tmp_path, cmd_propose, env_tmp_dir):
    path = make_state_v2(tmp_path, tmp_path / "work")
    env_tmp_dir(path)
    write_result(path, "codex-propose-rf130", {"items": [proposal()]})
    write_result(path, "kiro-propose-rf130", {"items": [proposal(), proposal(smell="日本語")]})

    _run(cmd_propose, path)

    state = read_state(path)
    assert [c["proposed_by"] for c in state["candidates"]] == [["codex", "kiro"]]
    assert [d["defer_reason"] for d in state["deferred_items"]] == ["vocabulary"]
    assert state["proposed"] == {"claude": None, "codex": 1, "kiro": 2}
    assert state["phases"]["propose"]["ended_at"]


def test_merge_proposals_with_no_candidates_exits_2_and_is_idempotent(
        tmp_path, cmd_propose, env_tmp_dir):
    path = make_state_v2(tmp_path, tmp_path / "work")
    env_tmp_dir(path)
    write_result(path, "codex-propose-rf130", {"items": []})
    for _ in range(2):
        with pytest.raises(SystemExit) as exc:
            _run(cmd_propose, path)
        assert exc.value.code == 2
    assert read_state(path)["phase"] == "final"


def test_a_broken_result_counts_as_no_proposal(tmp_path, cmd_propose, env_tmp_dir):
    path = make_state_v2(tmp_path, tmp_path / "work")
    env_tmp_dir(path)
    (path.parent / "codex-propose-rf130-result.json").write_text("{", encoding="utf-8")
    write_result(path, "kiro-propose-rf130", ["not", "an", "object"])
    write_result(path, "claude-propose-rf130", {"items": [proposal()]})

    _run(cmd_propose, path)

    assert read_state(path)["proposed"] == {"claude": 1, "codex": None, "kiro": 0}
