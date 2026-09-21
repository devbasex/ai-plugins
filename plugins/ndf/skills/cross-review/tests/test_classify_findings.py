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


def test_support_without_evidence_needs_judgment(state_mod):
    """**支持が付いた `major` は、根拠の 2 項目を欠いても人の判断待ちである**（#706。実測 F）。

    別の担当が支持を返した時点で「確かめる」目的は果たされている。根拠の欠けで落とすと、
    2 人が同じことを言っている情報が判定に効かない。
    """
    assert classify(state_mod, _finding(
        has_evidence=False, critiques=[_critique("kiro", "support")],
    )) == "needs_human_judgment"


def test_two_proposers_are_enough_without_evidence(state_mod):
    """2 者が独立に出した `major` は、根拠の 2 項目を欠いても人の判断待ちである（実測 H）。"""
    assert classify(state_mod, _finding(
        has_evidence=False, origin_runtimes=["codex", "kiro"],
    )) == "needs_human_judgment"


# ---------- 順 5: 未反証（#732 #624 #706） ----------

def test_a_major_nothing_matched_is_unrefuted(state_mod):
    """誰にも誤りを示されていない `major` は数える側へ入る（実測 B）。"""
    assert classify(state_mod, _finding()) == "unrefuted"


def test_a_lone_major_with_evidence_is_unrefuted(state_mod):
    """担当 1 者で反証する相手がいない `major` は未反証である（#624。実測 A）。"""
    assert classify(state_mod, _finding(has_evidence=True)) == "unrefuted"


@pytest.mark.parametrize("verdict", ["insufficient_evidence", "out_of_scope"])
def test_a_major_the_other_could_not_verify_is_unrefuted(state_mod, verdict):
    """**「立証できない」「範囲外」は誤りだという主張ではない**（#706。実測 D・E）。"""
    assert classify(state_mod, _finding(
        has_evidence=True, critiques=[_critique("kiro", verdict)],
    )) == "unrefuted"


def test_not_run_is_not_the_same_as_not_reproduced(state_mod):
    """**実行できなかったことを、再現しなかったことと同じにしない。**"""
    assert classify(state_mod, _finding(
        verification={"result": "not_run", "exit_code": None},
        has_evidence=True, critiques=[_critique("kiro", "support")],
    )) == "needs_human_judgment"


def test_a_finding_without_verification_is_readable(state_mod):
    f = _finding()
    del f["verification"]
    assert classify(state_mod, f) == "unrefuted"


# ---------- 順 6: 立証不足（軽微な指摘の残余） ----------

def test_a_lone_minor_with_evidence_is_insufficient(state_mod):
    """**`minor` 以下は反証の有無によらず数えない**（実測 C）。"""
    assert classify(state_mod, _finding(
        severity="minor", has_evidence=True)) == "insufficient_evidence"


# ---------- 未反証の理由 ----------

def test_an_unrefuted_finding_without_critiques_says_no_critique(state_mod):
    f = _finding(has_evidence=True)
    state_mod._apply_classification(f)
    assert f["classification"] == "unrefuted"
    assert f["unrefuted_reason"] == "no_critique"


def test_an_unrefuted_finding_with_critiques_says_not_supported(state_mod):
    f = _finding(has_evidence=True, critiques=[_critique("kiro", "insufficient_evidence")])
    state_mod._apply_classification(f)
    assert f["classification"] == "unrefuted"
    assert f["unrefuted_reason"] == "not_supported"


def test_the_unrefuted_reason_is_dropped_when_the_classification_changes(state_mod):
    """**理由は区分が未反証のときだけ存在する**（棄却の理由と同じ扱い）。"""
    f = _finding(has_evidence=True)
    state_mod._apply_classification(f)
    assert f["unrefuted_reason"] == "no_critique"

    f["critiques"] = [_critique("kiro", "refute")]
    state_mod._apply_classification(f)

    assert f["classification"] == "rejected"
    assert "unrefuted_reason" not in f
    assert "rejection_reason" in f


def test_other_classifications_carry_no_unrefuted_reason(state_mod):
    for f in (
        _finding(verification=_verified("reproduced")),
        _finding(has_evidence=True, critiques=[_critique("kiro", "support")]),
        _finding(severity="minor"),
    ):
        state_mod._apply_classification(f)
        assert f["classification"] != "unrefuted"
        assert "unrefuted_reason" not in f


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


def test_only_three_classifications_are_counted(state_mod):
    """**数えないのは棄却（`rejected`）と軽微な指摘だけである**（#732）。

    誰にも誤りを示されていない `major`（`d`。未反証）は数える。
    """
    out = counted(state_mod, [
        _finding(finding_id="a", verification=_verified("reproduced")),
        _finding(finding_id="b", has_evidence=True,
                 critiques=[_critique("kiro", "support")]),
        _finding(finding_id="c", verification=_verified("not_reproduced")),
        _finding(finding_id="d"),
        _finding(finding_id="e", severity="minor",
                 verification=_verified("reproduced")),
        _finding(finding_id="f", severity="minor"),
    ])
    assert sorted(out) == ["a", "b", "d"]


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
        "evidence_rounds": [1],
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


def test_an_old_review_findings_does_not_switch_to_the_classification(
        state_mod, tmp_path, monkeypatch):
    """**旧形式の `review_findings` は絞り込みの合図にならない**（#549 レビュー対応）。

    取り込み（`cmd_read_result`）はこの変更より前から `review_findings[]` を積む。
    存在だけで絞り込むと、`verification` も `critiques` も持たない旧いラウンドの
    `major` が `insufficient_evidence` へ落ち、**修正必須の指摘が残ったまま新規
    0 件で収束する**。
    """
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    st = {
        "current_pr": 1, "repo": "o/r",
        "rounds": [{"round": 1, "pr": 1}],
        # 旧形式: 区分も verification も critiques も無い
        "review_findings": [
            {"finding_id": "codex-r1-0", "agent": "codex", "path": "a.py",
             "line": 1, "body": "x", "severity": "major", "pr": 1, "round": 1},
        ],
    }
    _payload(tmp_path, "codex", 1, 1, [
        {"path": "a.py", "line": 1, "body": "x", "severity": "major"}])

    count, measurable = state_mod._new_finding_count(st, 1)

    assert measurable is True
    assert count == 1          # 従来どおり全件を数える（0 件にしない）


def test_the_marker_is_written_by_the_last_step_of_the_pipeline(
        state_mod, tmp_path, monkeypatch):
    """印を付けるのは経路の最後（`collect-critiques`）である。

    **対象ごとに有効な反証が揃ったときだけ付く**（#549 レビュー対応）。round 1 の
    担当は `agy` / `kiro` であるため、両方の結果ファイルを用意する。
    """
    import argparse
    import json

    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    st = {
        "current_pr": 1, "repo": "o/r", "only": None, "host": "claude",
        "rounds": [{"round": 1, "pr": 1}],
        "review_findings": [_finding(finding_id="codex-r1-0")],
        "final": None,
    }
    (tmp_path / "cross-review-pr1-state.json").write_text(json.dumps(st))
    for agent in ("agy", "kiro"):
        (tmp_path / f"{agent}-critique-pr1-round1.json").write_text(json.dumps(
            {"critiques": [{"finding_id": "codex-r1-0",
                            "verdict": "insufficient_evidence", "reason": "?"}]}))

    state_mod.cmd_collect_critiques(argparse.Namespace(pr=1))

    got = json.loads((tmp_path / "cross-review-pr1-state.json").read_text())
    assert got["evidence_rounds"] == [1]
    assert state_mod._evidence_completed(got, 1) is True


def test_measurability_is_decided_before_narrowing(state_mod, tmp_path, monkeypatch):
    """**測れたかどうかは区分で絞る前に決める。**

    全件が `rejected` になったラウンドを「測れなかった」と扱うと、元の
    `REQUEST_CHANGES` のまま終わらない。
    """
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    st = {
        "current_pr": 1, "repo": "o/r",
        "rounds": [{"round": 1, "pr": 1}],
        "evidence_rounds": [1],
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


# ---------- 担当 1 者・起動し直した担当の指摘を数える（#732 #624 #706） ----------

def _judge_rc(state_mod, pr):
    import argparse
    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=pr))
    return e.value.code


def _single_reviewer_state(tmp_path, finding, intent="REQUEST_CHANGES"):
    """担当 1 者（`only: "codex"`）で印の付いたラウンドが 1 つある状態ファイル。

    担当 1 者は `only` で表す（`_round_reviewers` が最初に読む値）。判定が読めるよう、
    状態ファイルと担当の payload を `CROSS_REVIEW_TMP_DIR` へ書く。
    """
    import json
    st = {
        "current_pr": 1, "repo": "o/r", "max_rounds": 12, "rotate_after": 8,
        "only": "codex", "host": "claude",
        "rounds": [{"round": 1, "pr": 1, "started_at": "2026-09-19T00:00:00+00:00",
                    "codex": {"intent": intent,
                              "by_severity": {finding["severity"]: 1}}}],
        "evidence_rounds": [1],
        "review_findings": [finding],
        "deferred_nits": [], "carried_over": None, "final": None,
    }
    (tmp_path / "cross-review-pr1-state.json").write_text(json.dumps(st))
    _payload(tmp_path, "codex", 1, 1, [
        {"path": finding["path"], "line": finding["line"], "body": finding["body"],
         "severity": finding["severity"]}])
    return st


def test_a_single_reviewer_unrefuted_major_is_counted(state_mod, tmp_path, monkeypatch):
    """AC1: 反証する相手のいない `major` を数え、収束させない（#624 の再現）。

    変更前は `(0, True)` で新規 0 件となり、`REQUEST_CHANGES` のまま終了コード 0
    （承認）で終わっていた。
    """
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    st = _single_reviewer_state(tmp_path, _finding(has_evidence=True))

    assert state_mod._new_finding_count(st, 1) == (1, True)
    assert st["review_findings"][0]["classification"] == "unrefuted"
    assert _judge_rc(state_mod, 1) == 2


def test_a_single_reviewer_major_without_evidence_is_still_counted(
        state_mod, tmp_path, monkeypatch):
    """AC2: 根拠の 2 項目を欠いても数える。`has_evidence` の値は残る。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    st = _single_reviewer_state(tmp_path, _finding(has_evidence=False))

    assert state_mod._new_finding_count(st, 1) == (1, True)
    assert st["review_findings"][0]["classification"] == "unrefuted"
    assert st["review_findings"][0]["has_evidence"] is False
    assert _judge_rc(state_mod, 1) == 2


def test_a_single_reviewer_minor_is_not_counted(state_mod, tmp_path, monkeypatch):
    """AC3: `minor` は担当 1 者でも数えない。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    st = _single_reviewer_state(
        tmp_path, _finding(severity="minor", has_evidence=True))

    assert state_mod._new_finding_count(st, 1) == (0, True)
    assert st["review_findings"][0]["classification"] == "insufficient_evidence"


def test_a_relaunched_reviewers_major_without_critiques_is_counted(
        state_mod, tmp_path, monkeypatch):
    """AC4: 反証を取り込んだ後に入った担当の `major` を数える（#583 の収束の部分）。

    `agy` + `kiro` のラウンドで、`kiro` の指摘には反証（`refute`）が付いて棄却され、
    `agy` の根拠を持つ `major` は反証 0 件のまま入っている。
    """
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    agy = _finding(finding_id="agy-r1-0", agent="agy", origin_runtimes=["agy"],
                   path="a.py", line=1, body="x", has_evidence=True)
    kiro = _finding(finding_id="kiro-r1-0", agent="kiro", origin_runtimes=["kiro"],
                    path="b.py", line=2, body="y", has_evidence=True,
                    critiques=[_critique("agy", "refute")])
    st = {
        "current_pr": 1, "repo": "o/r",
        "rounds": [{"round": 1, "pr": 1, "reviewers": ["agy", "kiro"]}],
        "evidence_rounds": [1],
        "review_findings": [agy, kiro],
    }
    _payload(tmp_path, "agy", 1, 1, [
        {"path": "a.py", "line": 1, "body": "x", "severity": "major"}])
    _payload(tmp_path, "kiro", 1, 1, [
        {"path": "b.py", "line": 2, "body": "y", "severity": "major"}])

    assert state_mod._new_finding_count(st, 1) == (1, True)
    assert agy["classification"] == "unrefuted"
    assert agy["unrefuted_reason"] == "no_critique"
    assert kiro["classification"] == "rejected"
