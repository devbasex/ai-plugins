"""区分と収束の判定（#156 の 3 本目 Task 4）。

**上から順に見て、最初に当たった区分を採る。** 実行で再現した指摘を先に採るのは、
順序そのもので「実行の結果を担当の支持より先に見る」を表すためである。
"""
from __future__ import annotations

import pytest


def _finding(**over):
    f = {
        "finding_id": "codex-r1-0", "agent": "codex", "path": "a.py", "line": 1,
        "body": "x", "severity": "major", "pr": 1, "round": 1,
        "origin_runtimes": ["codex"], "has_evidence": False,
        "verification": {"result": "not_run", "exit_code": None},
        "critiques": [],
    }
    f.update(over)
    return f


def _verified(result):
    return {"result": result, "exit_code": 1 if result == "reproduced" else 0}


def _critique(agent, verdict):
    return {"agent": agent, "verdict": verdict, "reason": "r"}


def classify(state_mod, finding):
    return state_mod._classify_finding(finding)


# ---------- 順 1・2: 実行で再現した ----------

def test_a_reproduced_major_is_blocking(state_mod):
    assert classify(state_mod, _finding(
        verification=_verified("reproduced"), severity="major")) == "verified_blocking"


def test_a_reproduced_critical_is_blocking(state_mod):
    assert classify(state_mod, _finding(
        verification=_verified("reproduced"), severity="critical")) == "verified_blocking"


def test_a_reproduced_minor_is_non_blocking(state_mod):
    assert classify(state_mod, _finding(
        verification=_verified("reproduced"), severity="minor")) == "verified_non_blocking"


def test_a_reproduced_finding_survives_a_refute(state_mod):
    """**機械が再現した事実を、担当の再評価が覆さない。**"""
    assert classify(state_mod, _finding(
        verification=_verified("reproduced"),
        critiques=[_critique("kiro", "refute"), _critique("agy", "refute")],
    )) == "verified_blocking"


# ---------- 順 3: 棄却 ----------

def test_a_not_reproduced_finding_is_rejected(state_mod):
    assert classify(state_mod, _finding(
        verification=_verified("not_reproduced"))) == "rejected"


def test_a_not_reproduced_finding_is_rejected_despite_support(state_mod):
    """**実行で再現しない指摘は、支持が多くても棄却される。**"""
    assert classify(state_mod, _finding(
        verification=_verified("not_reproduced"),
        critiques=[_critique("kiro", "support"), _critique("agy", "support")],
        has_evidence=True,
    )) == "rejected"


def test_a_refuted_finding_is_rejected(state_mod):
    assert classify(state_mod, _finding(
        critiques=[_critique("kiro", "refute")])) == "rejected"


# ---------- 順 4: 人の判断 ----------

def test_a_supported_major_with_evidence_needs_judgment(state_mod):
    assert classify(state_mod, _finding(
        has_evidence=True, critiques=[_critique("kiro", "support")],
    )) == "needs_human_judgment"


def test_two_proposers_are_enough_without_support(state_mod):
    """2 者が独立に出した指摘は、提案者以外が 0 人でも数える。"""
    assert classify(state_mod, _finding(
        has_evidence=True, origin_runtimes=["codex", "kiro"],
    )) == "needs_human_judgment"


def test_a_supported_minor_is_not_judged(state_mod):
    """**`minor` は区分を問わず新規性へ入らない。**"""
    assert classify(state_mod, _finding(
        severity="minor", has_evidence=True, critiques=[_critique("kiro", "support")],
    )) == "insufficient_evidence"


def test_support_without_evidence_is_insufficient(state_mod):
    assert classify(state_mod, _finding(
        has_evidence=False, critiques=[_critique("kiro", "support")],
    )) == "insufficient_evidence"


# ---------- 順 5 ----------

def test_nothing_matched_is_insufficient(state_mod):
    assert classify(state_mod, _finding()) == "insufficient_evidence"


def test_not_run_is_not_the_same_as_not_reproduced(state_mod):
    """**実行できなかったことを、再現しなかったことと同じにしない。**"""
    assert classify(state_mod, _finding(
        verification={"result": "not_run", "exit_code": None},
        has_evidence=True, critiques=[_critique("kiro", "support")],
    )) == "needs_human_judgment"


def test_a_finding_without_verification_is_readable(state_mod):
    f = _finding()
    del f["verification"]
    assert classify(state_mod, f) == "insufficient_evidence"


# ---------- 棄却の理由 ----------

def test_a_rejection_carries_its_reason(state_mod):
    f = _finding(critiques=[_critique("kiro", "refute")])
    state_mod._apply_classification(f)
    assert f["classification"] == "rejected"
    assert "refute" in f["rejection_reason"] or "kiro" in f["rejection_reason"]


def test_a_not_reproduced_rejection_names_the_run(state_mod):
    f = _finding(verification=_verified("not_reproduced"))
    state_mod._apply_classification(f)
    assert f["classification"] == "rejected"
    assert f["rejection_reason"]


def test_a_kept_finding_has_no_rejection_reason(state_mod):
    f = _finding(verification=_verified("reproduced"))
    state_mod._apply_classification(f)
    assert f["classification"] == "verified_blocking"
    assert "rejection_reason" not in f


# ---------- 収束の判定が数える対象 ----------

def _round_state(findings, rounds=1):
    return {
        "current_pr": 1, "repo": "o/r",
        "rounds": [{"round": n, "pr": 1} for n in range(1, rounds + 1)],
        "review_findings": list(findings),
    }


def counted(state_mod, findings, round_no=1):
    st = _round_state(findings, rounds=round_no)
    return state_mod._counted_finding_ids(st, round_no)


def test_only_two_classifications_are_counted(state_mod):
    """**`rejected` と `insufficient_evidence` は数えない。**"""
    out = counted(state_mod, [
        _finding(finding_id="a", verification=_verified("reproduced")),
        _finding(finding_id="b", has_evidence=True,
                 critiques=[_critique("kiro", "support")]),
        _finding(finding_id="c", verification=_verified("not_reproduced")),
        _finding(finding_id="d"),
        _finding(finding_id="e", severity="minor",
                 verification=_verified("reproduced")),
    ])
    assert sorted(out) == ["a", "b"]


def test_a_merged_side_is_not_counted(state_mod):
    """判定が読むのは代表の 1 件である。"""
    out = counted(state_mod, [
        _finding(finding_id="a", verification=_verified("reproduced")),
        _finding(finding_id="b", verification=_verified("reproduced"),
                 merged_into="a"),
    ])
    assert out == ["a"]


def test_nothing_counted_without_findings(state_mod):
    assert counted(state_mod, []) == []


# ---------- 新規性への接続 ----------

def _payload(tmp_dir, agent, pr, round_no, comments):
    import json
    (tmp_dir / f"{agent}-review-pr{pr}-round{round_no}-payload.json").write_text(
        json.dumps({"comments": comments}))


def test_the_new_count_uses_the_classification(state_mod, tmp_path, monkeypatch):
    """**新規性は区分で絞った集合を数える。**"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    st = {
        "current_pr": 1, "repo": "o/r",
        "rounds": [{"round": 1, "pr": 1}],
        "review_findings": [
            _finding(finding_id="a", path="a.py", line=1,
                     verification=_verified("reproduced")),
            _finding(finding_id="b", path="b.py", line=2,
                     verification=_verified("not_reproduced")),
        ],
    }
    _payload(tmp_path, "codex", 1, 1, [
        {"path": "a.py", "line": 1, "body": "x", "severity": "major"},
        {"path": "b.py", "line": 2, "body": "y", "severity": "major"},
    ])

    count, measurable = state_mod._new_finding_count(st, 1)

    assert measurable is True
    assert count == 1          # rejected の 1 件は数えない


def test_the_old_path_is_used_without_classifications(state_mod, tmp_path, monkeypatch):
    """**区分を持たないラウンドは、従来の数え方へ落ちる。**"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    st = {"current_pr": 1, "repo": "o/r", "rounds": [{"round": 1, "pr": 1}]}
    _payload(tmp_path, "codex", 1, 1, [
        {"path": "a.py", "line": 1, "body": "x", "severity": "major"},
        {"path": "b.py", "line": 2, "body": "y", "severity": "major"},
    ])

    count, measurable = state_mod._new_finding_count(st, 1)

    assert measurable is True
    assert count == 2          # 区分が無いため全件を数える


def test_measurability_is_decided_before_narrowing(state_mod, tmp_path, monkeypatch):
    """**測れたかどうかは区分で絞る前に決める。**

    全件が `rejected` になったラウンドを「測れなかった」と扱うと、元の
    `REQUEST_CHANGES` のまま終わらない。
    """
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    st = {
        "current_pr": 1, "repo": "o/r",
        "rounds": [{"round": 1, "pr": 1}],
        "review_findings": [
            _finding(finding_id="a", path="a.py", line=1,
                     verification=_verified("not_reproduced")),
        ],
    }
    _payload(tmp_path, "codex", 1, 1, [
        {"path": "a.py", "line": 1, "body": "x", "severity": "major"}])

    count, measurable = state_mod._new_finding_count(st, 1)

    assert measurable is True   # 読めている
    assert count == 0           # 数える区分が 0 件
