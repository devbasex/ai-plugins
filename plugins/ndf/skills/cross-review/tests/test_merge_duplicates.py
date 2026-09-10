"""同じ指摘を 1 件へ束ねる（#156 の 3 本目 Task 1、1 段目）。

**過剰な統合は指摘を失うが、統合し損ねても失われるものは無い。** 束ねられた側は区分と
収束の判定から外れるため、別の修正事項を誤って結ぶと消える。統合し損ねた場合は両方が
区分に載り、`origin_runtimes` が 1 者ずつになるだけである。**非対称であるため厳しい側へ
倒す**（近傍かつ本文の一致）。
"""
from __future__ import annotations

import pytest


def _finding(fid, agent, path, line, body, **over):
    f = {
        "finding_id": fid, "agent": agent, "path": path, "line": line,
        "body": body, "severity": "major", "pr": 1, "round": 1,
        "evidence": "", "falsification": "", "suggested_check": "",
        "posted_to": "inline", "has_evidence": False,
    }
    f.update(over)
    return f


def merge(state_mod, findings):
    st = {"review_findings": list(findings)}
    state_mod._merge_duplicates(st, round_no=1)
    return st["review_findings"]


def by_id(findings):
    return {f["finding_id"]: f for f in findings}


# ---------- 束ねる ----------

def test_the_same_finding_from_two_agents_is_merged(state_mod):
    out = by_id(merge(state_mod, [
        _finding("codex-r1-0", "codex", "api.py", 40, "null 入力で落ちる"),
        _finding("kiro-r1-2", "kiro", "api.py", 42, "null 入力で落ちる"),
    ]))
    assert out["codex-r1-0"]["origin_runtimes"] == ["codex", "kiro"]
    assert out["codex-r1-0"]["merged_from"] == ["kiro-r1-2"]
    assert out["kiro-r1-2"]["merged_into"] == "codex-r1-0"


def test_the_representative_is_the_first_imported(state_mod):
    """代表は先に取り込まれた担当の指摘である。"""
    out = merge(state_mod, [
        _finding("kiro-r1-0", "kiro", "api.py", 40, "同じ本文"),
        _finding("codex-r1-1", "codex", "api.py", 40, "同じ本文"),
    ])
    assert out[0]["finding_id"] == "kiro-r1-0"
    assert "merged_into" not in out[0]
    assert out[1]["merged_into"] == "kiro-r1-0"


def test_the_merged_side_is_kept(state_mod):
    """束ねられた側は消さない。消すと反証の結び先を失う。"""
    out = merge(state_mod, [
        _finding("codex-r1-0", "codex", "a.py", 1, "x"),
        _finding("kiro-r1-0", "kiro", "a.py", 1, "x"),
    ])
    assert len(out) == 2


# ---------- 束ねない ----------

def test_a_near_line_with_a_different_body_is_not_merged(state_mod):
    """近傍だけで結ぶと、別の修正事項が消える。"""
    out = by_id(merge(state_mod, [
        _finding("codex-r1-0", "codex", "api.py", 40, "null 入力で落ちる"),
        _finding("kiro-r1-0", "kiro", "api.py", 42, "認可の判定が漏れている"),
    ]))
    assert "merged_from" not in out["codex-r1-0"]
    assert "merged_into" not in out["kiro-r1-0"]


def test_a_near_line_with_a_different_body_becomes_a_candidate(state_mod):
    """位置の一致は候補の抽出までである。"""
    out = by_id(merge(state_mod, [
        _finding("codex-r1-0", "codex", "api.py", 40, "null 入力で落ちる"),
        _finding("kiro-r1-0", "kiro", "api.py", 42, "認可の判定が漏れている"),
    ]))
    assert out["codex-r1-0"]["duplicate_candidates"] == ["kiro-r1-0"]
    assert out["kiro-r1-0"]["duplicate_candidates"] == ["codex-r1-0"]


def test_a_distant_line_is_not_a_candidate(state_mod):
    """行差が 3 を超えれば候補にもしない。"""
    out = by_id(merge(state_mod, [
        _finding("codex-r1-0", "codex", "api.py", 40, "同じ本文"),
        _finding("kiro-r1-0", "kiro", "api.py", 50, "同じ本文"),
    ]))
    assert out["codex-r1-0"].get("duplicate_candidates", []) == []
    assert "merged_from" not in out["codex-r1-0"]


def test_a_different_file_is_never_merged(state_mod):
    out = by_id(merge(state_mod, [
        _finding("codex-r1-0", "codex", "a.py", 40, "同じ本文"),
        _finding("kiro-r1-0", "kiro", "b.py", 40, "同じ本文"),
    ]))
    assert "merged_from" not in out["codex-r1-0"]


def test_the_same_agent_is_not_merged_with_itself(state_mod):
    """1 者が近い行へ 2 件出したときは、独立した指摘として残す。"""
    out = by_id(merge(state_mod, [
        _finding("codex-r1-0", "codex", "a.py", 40, "同じ本文"),
        _finding("codex-r1-1", "codex", "a.py", 41, "同じ本文"),
    ]))
    assert "merged_from" not in out["codex-r1-0"]


# ---------- 集約 ----------

def test_the_highest_severity_is_carried(state_mod):
    """代表は組の中で最も高い重要度を引き継ぐ。"""
    out = merge(state_mod, [
        _finding("codex-r1-0", "codex", "a.py", 1, "x", severity="minor"),
        _finding("kiro-r1-0", "kiro", "a.py", 1, "x", severity="critical"),
    ])
    assert out[0]["severity"] == "critical"


def test_the_evidence_is_taken_from_one_element(state_mod):
    """**対は同じ要素から採る。** 継ぎ合わせると誰も書いていない組ができる。"""
    out = merge(state_mod, [
        _finding("codex-r1-0", "codex", "a.py", 1, "x", evidence="根拠だけ"),
        _finding("kiro-r1-0", "kiro", "a.py", 1, "x",
                 evidence="両方ある", falsification="反証もある", has_evidence=True),
    ])
    assert out[0]["evidence"] == "両方ある"
    assert out[0]["falsification"] == "反証もある"
    assert out[0]["has_evidence"] is True
    assert out[0]["evidence_from"] == "kiro-r1-0"


def test_no_evidence_leaves_the_flag_false(state_mod):
    out = merge(state_mod, [
        _finding("codex-r1-0", "codex", "a.py", 1, "x", evidence="根拠だけ"),
        _finding("kiro-r1-0", "kiro", "a.py", 1, "x", falsification="反証だけ"),
    ])
    assert out[0]["has_evidence"] is False


def test_nothing_happens_without_findings(state_mod):
    st = {}
    state_mod._merge_duplicates(st, round_no=1)
    assert st.get("review_findings", []) == []


# ---------- 2 段目（申告による統合） ----------

def merge_declared(state_mod, findings):
    st = {"review_findings": list(findings)}
    state_mod._merge_declared_duplicates(st, round_no=1)
    return st["review_findings"]


def _with_critique(finding, agent, verdict, target=None):
    c = {"agent": agent, "verdict": verdict, "reason": "r"}
    if target:
        c["duplicate_of"] = target
    finding.setdefault("critiques", []).append(c)
    return finding


def test_a_mutual_duplicate_is_merged(state_mod):
    """**機械では結べない重複を担当が見つける。** 相互の申告で束ねる。"""
    a = _with_critique(
        _finding("codex-r1-0", "codex", "api.py", 40, "null で落ちる"),
        "agy", "duplicate", "kiro-r1-0")
    b = _with_critique(
        _finding("kiro-r1-0", "kiro", "api.py", 42, "None が渡る"),
        "agy", "duplicate", "codex-r1-0")

    out = by_id(merge_declared(state_mod, [a, b]))

    assert out["codex-r1-0"]["merged_from"] == ["kiro-r1-0"]
    assert out["kiro-r1-0"]["merged_into"] == "codex-r1-0"
    assert out["codex-r1-0"]["origin_runtimes"] == ["codex", "kiro"]


def test_a_one_sided_duplicate_is_not_merged(state_mod):
    """**2 段目は相互の申告に限る。** 片側だけでは束ねない。"""
    a = _with_critique(
        _finding("codex-r1-0", "codex", "api.py", 40, "null で落ちる"),
        "agy", "duplicate", "kiro-r1-0")
    b = _finding("kiro-r1-0", "kiro", "api.py", 42, "None が渡る")

    out = by_id(merge_declared(state_mod, [a, b]))

    assert "merged_from" not in out["codex-r1-0"]
    assert "merged_into" not in out["kiro-r1-0"]


def test_a_declared_merge_aggregates_the_severity(state_mod):
    a = _with_critique(
        _finding("codex-r1-0", "codex", "a.py", 1, "x", severity="minor"),
        "agy", "duplicate", "kiro-r1-0")
    b = _with_critique(
        _finding("kiro-r1-0", "kiro", "a.py", 2, "y", severity="critical"),
        "agy", "duplicate", "codex-r1-0")

    out = merge_declared(state_mod, [a, b])

    assert out[0]["severity"] == "critical"


def test_a_declared_merge_does_not_rerun_the_verification(state_mod):
    """**2 段目は記録済みの結果を選び直すだけである。** 実行し直さない。"""
    a = _with_critique(
        _finding("codex-r1-0", "codex", "a.py", 1, "x",
                 verification={"result": "not_run", "exit_code": None}),
        "agy", "duplicate", "kiro-r1-0")
    b = _with_critique(
        _finding("kiro-r1-0", "kiro", "a.py", 2, "y",
                 verification={"result": "reproduced", "exit_code": 1,
                               "finding_id": "kiro-r1-0"}),
        "agy", "duplicate", "codex-r1-0")

    out = merge_declared(state_mod, [a, b])

    assert out[0]["verification"]["result"] == "reproduced"
    assert out[0]["verification"]["finding_id"] == "kiro-r1-0"


def test_a_declared_merge_carries_the_evidence(state_mod):
    a = _with_critique(
        _finding("codex-r1-0", "codex", "a.py", 1, "x"),
        "agy", "duplicate", "kiro-r1-0")
    b = _with_critique(
        _finding("kiro-r1-0", "kiro", "a.py", 2, "y",
                 evidence="根拠", falsification="反証", has_evidence=True),
        "agy", "duplicate", "codex-r1-0")

    out = merge_declared(state_mod, [a, b])

    assert out[0]["has_evidence"] is True
    assert out[0]["evidence_from"] == "kiro-r1-0"


def test_an_already_merged_finding_is_left_alone(state_mod):
    """1 段目で束ねた組へ 2 段目を掛けない。"""
    a = _finding("codex-r1-0", "codex", "a.py", 1, "x")
    b = _with_critique(
        _finding("kiro-r1-0", "kiro", "a.py", 1, "x", merged_into="codex-r1-0"),
        "agy", "duplicate", "codex-r1-0")

    out = by_id(merge_declared(state_mod, [a, b]))

    assert out["kiro-r1-0"]["merged_into"] == "codex-r1-0"
    assert out["codex-r1-0"].get("merged_from", []) == []
