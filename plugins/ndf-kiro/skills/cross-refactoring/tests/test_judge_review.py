"""レビュー判定と差し戻しのテスト。

指摘には**改善項目 ID を必須**とする。取り消しを項目単位で行うために必要で、
そのラウンドに無い ID や欠落は承認にも変更要求にもせず差し戻す。
"""
from __future__ import annotations

import pytest

from conftest import make_state, read_state, write_result

REVIEWERS = ["gemini", "kiro"]
ROUND_ITEMS = ["R1-001", "R1-002"]


def review(verdict="APPROVE", findings=None):
    return {"verdict": verdict, "findings": findings or []}


def finding(item_id="R1-001", **over):
    base = {"item_id": item_id, "thread_id": "PRRT_x", "summary": "x", "resolved": False}
    base.update(over)
    return base


# ---------- 判定 ----------

def test_both_approve(refactor):
    verdict, problems = refactor.judge(
        {"gemini": review(), "kiro": review()}, REVIEWERS, ROUND_ITEMS
    )
    assert (verdict, problems) == ("approved", [])


def test_one_request_changes(refactor):
    verdict, _ = refactor.judge(
        {"gemini": review("REQUEST_CHANGES", [finding()]), "kiro": review()},
        REVIEWERS, ROUND_ITEMS,
    )
    assert verdict == "changes"


def test_missing_result_is_invalid(refactor):
    verdict, problems = refactor.judge({"gemini": review()}, REVIEWERS, ROUND_ITEMS)
    assert verdict == "invalid"
    assert any("kiro" in p for p in problems)


def test_comment_verdict_is_invalid(refactor):
    """判定は 2 値。疑義が残るなら通さない側に倒すため COMMENT は使わない。"""
    verdict, problems = refactor.judge(
        {"gemini": review("COMMENT"), "kiro": review()}, REVIEWERS, ROUND_ITEMS
    )
    assert verdict == "invalid"
    assert any("COMMENT" in p for p in problems)


# ---------- 指摘の項目 ID ----------

def test_finding_without_item_id_is_invalid(refactor):
    verdict, problems = refactor.judge(
        {"gemini": review("REQUEST_CHANGES", [{"thread_id": "t"}]), "kiro": review()},
        REVIEWERS, ROUND_ITEMS,
    )
    assert verdict == "invalid"
    assert any("item_id がありません" in p for p in problems)


def test_finding_with_unknown_item_id_is_invalid(refactor):
    verdict, problems = refactor.judge(
        {"gemini": review("REQUEST_CHANGES", [finding("R9-999")]), "kiro": review()},
        REVIEWERS, ROUND_ITEMS,
    )
    assert verdict == "invalid"
    assert any("R9-999" in p for p in problems)


def test_null_item_id_is_accepted(refactor):
    """ラウンド全体に対する指摘は null を明示させる。"""
    verdict, problems = refactor.judge(
        {"gemini": review("REQUEST_CHANGES", [finding(None)]), "kiro": review()},
        REVIEWERS, ROUND_ITEMS,
    )
    assert (verdict, problems) == ("changes", [])


# ---------- 未解決の指摘から取り消し対象を求める ----------

def test_unresolved_findings_select_their_items(refactor):
    history = [{"findings": [finding("R1-001"), finding("R1-002", resolved=True)]}]
    targets, whole = refactor.unresolved_item_ids(history, ROUND_ITEMS)
    assert (targets, whole) == (["R1-001"], False)


def test_null_item_id_selects_the_whole_round(refactor):
    history = [{"findings": [finding(None)]}]
    targets, whole = refactor.unresolved_item_ids(history, ROUND_ITEMS)
    assert (targets, whole) == (ROUND_ITEMS, True)


def test_resolved_findings_select_nothing(refactor):
    history = [{"findings": [finding("R1-001", resolved=True)]}]
    assert refactor.unresolved_item_ids(history, ROUND_ITEMS) == ([], False)


def test_findings_from_multiple_fix_rounds_accumulate(refactor):
    history = [
        {"findings": [finding("R1-001", resolved=True)]},
        {"findings": [finding("R1-002")]},
    ]
    targets, _ = refactor.unresolved_item_ids(history, ROUND_ITEMS)
    assert targets == ["R1-002"]


# ---------- サブコマンド ----------

def _state(tmp_path, **over):
    return make_state(
        tmp_path,
        items=[
            {"item_id": i, "round": 1, "path": "src/foo.py", "symbol": "S",
             "smell": "long_method", "technique": "extract_method", "severity": "major",
             "rationale": "", "plan": "", "test_gap": False,
             "estimated_diff_lines": 10, "proposed_by": ["codex"],
             "status": "reviewing", "commits": ["abc"]}
            for i in ROUND_ITEMS
        ],
        rounds=[{
            "round": 1, "impl": "codex", "reviewers": REVIEWERS,
            "impl_model": {"requested": None, "observed": None},
            "reviewer_models": {r: {"requested": None, "observed": None}
                                for r in REVIEWERS},
            "proposed": {}, "merged": 2, "adopted": 2, "deferred": 0,
            "items": list(ROUND_ITEMS),
            "apply": {"applied": list(ROUND_ITEMS), "failed": []},
            "fix_rounds": 0, "durations": {}, "reviews": [],
        }],
        **over,
    )


def test_judge_command_marks_items_done_on_approval(refactor, tmp_path, env_tmp_dir):
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    for r in REVIEWERS:
        write_result(state_path, f"{r}-review-r1", review())

    refactor.cmd_judge_review(type("A", (), {"id": 130, "round": 1})())

    state = read_state(state_path)
    assert all(i["status"] == "done" for i in state["items"])
    assert state["rounds"][0]["reviews"][0]["gemini"] == "APPROVE"
    assert state["phase"] == "propose"


def test_judge_command_exits_2_on_changes(
    refactor, tmp_path, env_tmp_dir, no_git
):
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    write_result(state_path, "gemini-review-r1", review("REQUEST_CHANGES", [finding()]))
    write_result(state_path, "kiro-review-r1", review())

    with pytest.raises(SystemExit) as e:
        refactor.cmd_judge_review(type("A", (), {"id": 130, "round": 1})())
    assert e.value.code == 2
    record = read_state(state_path)["rounds"][0]["reviews"][0]
    assert record["findings"][0]["reviewer"] == "gemini"
    assert record["findings"][0]["item_id"] == "R1-001"


def test_judge_command_exits_3_on_invalid_finding(refactor, tmp_path, env_tmp_dir):
    """未知の ID や欠落は差し戻して再レビューさせる。"""
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    write_result(state_path, "gemini-review-r1",
                 review("REQUEST_CHANGES", [finding("R9-999")]))
    write_result(state_path, "kiro-review-r1", review())

    with pytest.raises(SystemExit) as e:
        refactor.cmd_judge_review(type("A", (), {"id": 130, "round": 1})())
    assert e.value.code == 3
    assert all(i["status"] == "reviewing" for i in read_state(state_path)["items"])


def test_repeated_invalid_reviews_degrade_to_changes(refactor, tmp_path, env_tmp_dir):
    """差し戻しを無限に繰り返さない。上限を超えたら変更要求として扱う。

    紐づけ先が決まらないため、取り消しはラウンド全件が対象になる。
    """
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    write_result(state_path, "gemini-review-r1",
                 review("REQUEST_CHANGES", [finding("R9-999")]))
    write_result(state_path, "kiro-review-r1", review())
    args = type("A", (), {"id": 130, "round": 1})()

    for expected in (3, 2):
        with pytest.raises(SystemExit) as e:
            refactor.cmd_judge_review(args)
        assert e.value.code == expected

    entry = read_state(state_path)["rounds"][0]
    targets, whole = refactor.unresolved_item_ids(entry["reviews"], ROUND_ITEMS)
    assert whole is True
    assert targets == ROUND_ITEMS


def test_should_abandon_only_at_the_limit(refactor, tmp_path, env_tmp_dir):
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    args = type("A", (), {"id": 130, "round": 1})()

    with pytest.raises(SystemExit) as e:
        refactor.cmd_should_abandon(args)
    assert e.value.code == 2

    state = read_state(state_path)
    state["rounds"][0]["fix_rounds"] = 3
    state_path.write_text(__import__("json").dumps(state), encoding="utf-8")
    refactor.cmd_should_abandon(args)  # 上限到達で正常終了 = 見送りへ


# ---------- 出力の形が崩れていても落ちない ----------

def test_finding_that_is_not_an_object_is_invalid(refactor):
    """LLM が文字列を返しても落ちず、差し戻し扱いにする。"""
    verdict, problems = refactor.judge(
        {"gemini": review("REQUEST_CHANGES", ["item_id を含む文字列"]),
         "kiro": review()},
        REVIEWERS, ROUND_ITEMS,
    )
    assert verdict == "invalid"
    assert any("JSON オブジェクトではありません" in p for p in problems)


def test_findings_that_is_not_a_list_is_invalid(refactor):
    verdict, problems = refactor.judge(
        {"gemini": {"verdict": "REQUEST_CHANGES", "findings": "なにか"},
         "kiro": review()},
        REVIEWERS, ROUND_ITEMS,
    )
    assert verdict == "invalid"
    assert any("配列ではありません" in p for p in problems)


def test_review_result_that_is_not_an_object_is_invalid(refactor):
    verdict, problems = refactor.judge(
        {"gemini": "APPROVE", "kiro": review()}, REVIEWERS, ROUND_ITEMS
    )
    assert verdict == "invalid"
    assert any("JSON オブジェクトではありません" in p for p in problems)


def test_judge_command_survives_broken_review_json(refactor, tmp_path, env_tmp_dir):
    """`judge()` が invalid と判定する入力でも、記録生成で落ちないこと。"""
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    write_result(state_path, "gemini-review-r1", "オブジェクトですらない")
    write_result(state_path, "kiro-review-r1",
                 {"verdict": "APPROVE", "findings": ["壊れた指摘"]})

    with pytest.raises(SystemExit) as e:
        refactor.cmd_judge_review(type("A", (), {"id": 130, "round": 1})())
    assert e.value.code == 3
    record = read_state(state_path)["rounds"][0]["reviews"][0]
    assert record["findings"] == []


def test_judge_command_records_the_fix_base(refactor, tmp_path, env_tmp_dir, monkeypatch):
    """修正コミットの実在を確かめるため、変更要求の時点の HEAD を残すこと。"""
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    monkeypatch.setattr(refactor, "_git_out", lambda work, args: "FIX_BASE")
    write_result(state_path, "gemini-review-r1", review("REQUEST_CHANGES", [finding()]))
    write_result(state_path, "kiro-review-r1", review())

    with pytest.raises(SystemExit):
        refactor.cmd_judge_review(type("A", (), {"id": 130, "round": 1})())
    assert read_state(state_path)["rounds"][0]["fix_base_sha"] == "FIX_BASE"


def test_judge_command_advances_the_fix_attempt(
    refactor, tmp_path, env_tmp_dir, no_git
):
    """変更要求のたびに試行番号を進める。

    `merge-fix` が「叩き直し」と「次の修正ラウンド」を区別するのに使う。
    """
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    write_result(state_path, "gemini-review-r1", review("REQUEST_CHANGES", [finding()]))
    write_result(state_path, "kiro-review-r1", review())
    args = type("A", (), {"id": 130, "round": 1})()

    with pytest.raises(SystemExit):
        refactor.cmd_judge_review(args)
    assert read_state(state_path)["rounds"][0]["fix_attempts"] == 1

    with pytest.raises(SystemExit):
        refactor.cmd_judge_review(args)
    assert read_state(state_path)["rounds"][0]["fix_attempts"] == 2
