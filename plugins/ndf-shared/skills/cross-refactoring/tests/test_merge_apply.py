"""適用結果の検証（トレーラー / テスト / 固定テストの先行 / 差分予算）のテスト。

読ませても手順を守るとは限らないため、**結果側から手順の遵守を確かめる**。
"""
from __future__ import annotations

import pytest

from conftest import make_state, read_state, write_result


def trailers(item_id="R1-001", round_no="1", runtime="codex", model="gpt-5.5"):
    return {
        "Item-Id": item_id, "Round": round_no,
        "Impl-Runtime": runtime, "Impl-Model": model,
    }


def commit(sha="abc1234", **over):
    base = {
        "sha": sha, "test_status": "pass",
        "characterization_test": False, "trailers": trailers(),
    }
    base.update(over)
    return base


def item(**over):
    base = {
        "item_id": "R1-001", "round": 1, "path": "src/foo.py", "symbol": "Foo.handle",
        "smell": "long_method", "technique": "extract_method", "severity": "major",
        "rationale": "", "plan": "", "test_gap": False,
        "estimated_diff_lines": 40, "proposed_by": ["codex"],
        "status": "pending", "commits": [],
    }
    base.update(over)
    return base


def reported(**over):
    base = {"item_id": "R1-001", "diff_lines": 30, "commits": [commit()]}
    base.update(over)
    return base


# ---------- コミットトレーラー ----------

def test_all_four_trailers_pass(refactor):
    assert refactor.verify_commit_trailers(commit()) is None


@pytest.mark.parametrize("missing", ["Item-Id", "Round", "Impl-Runtime", "Impl-Model"])
def test_missing_any_trailer_fails(refactor, missing):
    t = trailers()
    del t[missing]
    problem = refactor.verify_commit_trailers(commit(trailers=t))
    assert problem is not None and missing in problem


def test_blank_trailer_value_fails(refactor):
    problem = refactor.verify_commit_trailers(commit(trailers=trailers(model="  ")))
    assert problem is not None and "Impl-Model" in problem


def test_item_fails_when_trailer_missing(refactor):
    t = trailers()
    del t["Impl-Model"]
    problem = refactor.verify_apply_item(reported(commits=[commit(trailers=t)]), item())
    assert problem is not None and "Impl-Model" in problem


# ---------- 1 手 1 コミット ----------

def test_zero_commits_fails(refactor):
    problem = refactor.verify_apply_item(reported(commits=[]), item())
    assert problem is not None and "コミットが 1 件も" in problem


def test_commit_belonging_to_another_item_fails(refactor):
    """複数の項目を 1 コミットにまとめると、取り消し範囲が項目単位で決まらない。"""
    problem = refactor.verify_apply_item(
        reported(commits=[commit(trailers=trailers(item_id="R1-002"))]), item()
    )
    assert problem is not None and "Item-Id" in problem


def test_failed_test_on_any_commit_fails(refactor):
    problem = refactor.verify_apply_item(
        reported(commits=[commit(), commit(sha="def5678", test_status="fail")]), item()
    )
    assert problem is not None and "テストが成功していません" in problem


# ---------- 現状固定テストの先行 ----------

def test_test_gap_requires_characterization_test_first(refactor):
    problem = refactor.verify_apply_item(reported(), item(test_gap=True))
    assert problem is not None and "現状固定テスト" in problem


def test_test_gap_passes_when_characterization_test_leads(refactor):
    payload = reported(commits=[
        commit(sha="aaa", characterization_test=True),
        commit(sha="bbb"),
    ])
    assert refactor.verify_apply_item(payload, item(test_gap=True)) is None


def test_no_test_gap_does_not_require_characterization_test(refactor):
    assert refactor.verify_apply_item(reported(), item(test_gap=False)) is None


# ---------- 差分予算 ----------

def test_diff_within_budget_passes(refactor):
    assert refactor.verify_apply_item(
        reported(diff_lines=80), item(estimated_diff_lines=40)
    ) is None


def test_diff_over_budget_fails(refactor):
    problem = refactor.verify_apply_item(
        reported(diff_lines=81), item(estimated_diff_lines=40)
    )
    assert problem is not None and "差分予算" in problem


def test_zero_estimate_disables_budget_check(refactor):
    """見積り 0 は「見積れなかった」を意味する。予算 0 で必ず落とす方が害が大きい。"""
    assert refactor.verify_apply_item(
        reported(diff_lines=500), item(estimated_diff_lines=0)
    ) is None


# ---------- サブコマンド ----------

def _state_with_items(tmp_path, items, **over):
    return make_state(
        tmp_path,
        items=items,
        rounds=[{
            "round": 1, "impl": "codex", "reviewers": ["gemini", "kiro"],
            "impl_model": {"requested": "gpt-5.5", "observed": None},
            "reviewer_models": {"gemini": {"requested": None, "observed": None},
                                "kiro": {"requested": None, "observed": None}},
            "proposed": {}, "merged": 2, "adopted": len(items), "deferred": 0,
            "items": [i["item_id"] for i in items],
            "apply": {"applied": [], "failed": []}, "fix_rounds": 0,
            "durations": {}, "reviews": [],
        }],
        **over,
    )


def test_partial_failure_keeps_the_rest(refactor, tmp_path, env_tmp_dir):
    """1 件の失敗でラウンドを止めず、失敗した項目だけを見送りにする。"""
    items = [item(item_id="R1-001"), item(item_id="R1-002")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    write_result(state_path, "codex-apply-r1", {
        "base_sha": "aaa", "head_sha": "ccc", "elapsed_seconds": 100,
        "items": [
            {"item_id": "R1-001", "diff_lines": 30, "commits": [commit()]},
            {"item_id": "R1-002", "diff_lines": 30, "commits": []},
        ],
    })
    refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1})())

    state = read_state(state_path)
    assert state["rounds"][0]["apply"]["applied"] == ["R1-001"]
    assert state["rounds"][0]["apply"]["failed"] == ["R1-002"]
    assert state["items"][0]["status"] == "reviewing"
    assert state["items"][1]["status"] == "abandoned"
    assert state["phase"] == "review"


def test_all_failed_exits_2(refactor, tmp_path, env_tmp_dir):
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    write_result(state_path, "codex-apply-r1", {"items": [
        {"item_id": "R1-001", "diff_lines": 0, "commits": []},
    ]})
    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1})())
    assert e.value.code == 2
    assert read_state(state_path)["phase"] == "propose"


def test_missing_item_in_result_is_a_failure(refactor, tmp_path, env_tmp_dir):
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    write_result(state_path, "codex-apply-r1", {"items": []})
    with pytest.raises(SystemExit):
        refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1})())
    assert read_state(state_path)["items"][0]["status"] == "abandoned"


def test_red_baseline_blocks_every_item(refactor, tmp_path, env_tmp_dir):
    """着手前のテストが失敗していたら、適用へ着手せず全項目を blocked にする。"""
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(
        tmp_path, items,
        baseline_test={"command": "pytest -q", "status": "red", "checked_at": "x"},
    )
    env_tmp_dir(state_path)
    write_result(state_path, "codex-apply-r1", {"items": []})
    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1})())
    assert e.value.code == 2
    assert read_state(state_path)["items"][0]["status"] == "blocked"
