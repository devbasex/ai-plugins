"""収束ループの終わり方から結果を決める（#2370・#2444・PR #3462 の保留）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.contracts import result
from ndf.domains.crossreview import finish_decide as fin

from ._repos import run_cli


def _summary(tmp_path: Path, final: str, rounds: int, findings: list[dict]) -> Path:
    path = tmp_path / "summary.json"
    path.write_text(json.dumps({"final": final, "rounds": rounds, "findings": findings}), encoding="utf-8")
    return path


def _rebuild(tmp_path: Path, items: list) -> Path:
    path = tmp_path / "rebuild.json"
    path.write_text(json.dumps(result.result("rebuild", "ok", "足すべき内容", items)), encoding="utf-8")
    return path


FIXED = {"id": "F1", "text": "語の定義が無い", "outcome": fin.FIXED}
DEFERRED = {"id": "F2", "text": "読み順", "outcome": fin.SKIPPED, "reason": "認可に当たるため #9 で扱う"}


@pytest.mark.parametrize("final", [fin.MAX_ROUNDS, fin.ERROR])
def test_issue_2370_limit_with_no_unresolved_findings_moves_on_and_records_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], final: str
) -> None:
    """#2370: 上限や途中の誤りで終わっても、未解決 0 件・見送りに理由があれば ok で次の工程へ進み、理由を decisions に残す。"""
    code, got = run_cli(capsys, "finish", "--summary", str(_summary(tmp_path, final, 4, [FIXED, DEFERRED])))
    assert code == 0 and got["status"] == "ok"
    assert any(final in d["decision"] and "次の工程へ進む" in d["decision"] for d in got["decisions"])
    assert got["warnings"]


def test_issue_2370_unresolved_findings_stop_as_incomplete_with_gaps(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """#2370: 未解決が残れば incomplete（gaps つき）で止まり、プランの incomplete のルートが受ける。"""
    open_one = {"id": "F3", "text": "コントラクトが食い違う", "outcome": fin.UNRESOLVED}
    no_reason = {"id": "F4", "text": "名前", "outcome": fin.SKIPPED}
    code, got = run_cli(capsys, "finish", "--summary", str(_summary(tmp_path, fin.MAX_ROUNDS, 4, [FIXED, open_one, no_reason])))
    assert got["cause"]["kind"] == "incomplete" and code == result.code_for(got)
    assert [(g["kind"], g["name"]) for g in got["gaps"]] == [(fin.GAP_UNRESOLVED, "F3"), (fin.GAP_NO_REASON, "F4")]


def test_issue_2444_rebuild_findings_each_become_fixed_or_deferred(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """#2444 の残り: 組み直しの読み手役の「足すべき内容」は、レビューで 1 件ずつ「直した」「見送った（理由）」になり、
    どちらにもならなかった件は欠けとして結果に残る。"""
    rebuild = _rebuild(tmp_path, [{"id": "R1", "text": "出すコミットの固定"}, {"id": "R2", "text": "項目名の対応"}, "記録の行のキー"])
    handled = [
        {"id": "R1", "text": "出すコミットの固定", "outcome": fin.FIXED},
        {"id": "R2", "text": "項目名の対応", "outcome": fin.SKIPPED, "reason": "別の設計で決める"},
    ]
    code, got = run_cli(capsys, "finish", "--summary", str(_summary(tmp_path, fin.CONVERGED, 2, handled)), "--findings", str(rebuild))
    assert got["cause"]["kind"] == "incomplete"
    assert [d["decision"] for d in got["decisions"]] == ["足すべき内容 R1 を直した", "足すべき内容 R2 を見送った（理由: 別の設計で決める）"]
    assert got["gaps"] == [
        {"kind": fin.GAP_UNHANDLED, "name": "rebuild-3", "reason": "レビューで直したとも見送ったとも決めていない: 記録の行のキー"}
    ]
    handled.append({"id": "rebuild-3", "text": "記録の行のキー", "outcome": fin.FIXED})
    code, got = run_cli(capsys, "finish", "--summary", str(_summary(tmp_path, fin.CONVERGED, 2, handled)), "--findings", str(rebuild))
    assert code == 0 and len(got["decisions"]) == 3 and "warnings" not in got


def test_pr_3462_incomplete_review_keeps_gaps_in_its_result_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """PR #3462 の保留: incomplete を返すとき、未解決の gaps を結果 JSON に載せる（プランの結果へ引き継ぐ元）。"""
    open_one = {"id": "F3", "text": "コントラクトが食い違う", "outcome": fin.UNRESOLVED}
    code, got = run_cli(capsys, "finish", "--summary", str(_summary(tmp_path, fin.CONVERGED, 1, [open_one])))
    assert result.errors(got, code) == []
    assert got["gaps"][0]["name"] == "F3"
    assert got["outputs"] == {"gaps": got["gaps"]}


def test_mvv_findings_left_open_are_a_gap_of_their_own(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """MVV に反する指摘の未解決は、ほかの未解決と別の種類の欠けにする。"""
    mvv = {"id": "F1", "text": "反する", "outcome": fin.UNRESOLVED, "severity": "major", "mvv": "Value 5"}
    other = {"id": "F2", "text": "止まる", "outcome": fin.UNRESOLVED, "severity": "major"}
    deferred = {**mvv, "id": "F3", "outcome": fin.SKIPPED, "reason": "反しない"}
    _, got = run_cli(capsys, "finish", "--summary", str(_summary(tmp_path, fin.MAX_ROUNDS, 2, [mvv, other, deferred])))
    assert [(g["kind"], g["name"]) for g in got["outputs"]["gaps"]] == [(fin.GAP_MVV, "F1"), (fin.GAP_UNRESOLVED, "F2")]
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "mvv_unresolved") and "F1（Value 5）" in got["summary"]


@pytest.mark.parametrize("final", [fin.CONVERGED, fin.MAX_ROUNDS])
def test_unfixed_citation_errors_and_a_failed_check_stop_the_review(tmp_path: Path, capsys: pytest.CaptureFixture[str], final: str) -> None:
    """根拠の記述の誤りが残るか、最後のコミットで確かめられなければ、指摘が 0 件でも止まる。直っていれば止まらない。"""
    wrong = {"id": "r1-n1", "text": "誤り", "outcome": fin.UNRESOLVED, "severity": "major", "by_ndf": True}
    for findings, checked, names in [
        ([wrong], True, ["r1-n1"]),
        ([], False, [fin.CITATION_CHECK]),
        ([wrong], False, ["r1-n1", fin.CITATION_CHECK]),
        ([{**wrong, "outcome": fin.FIXED}], True, []),
    ]:
        path = _summary(tmp_path, final, 2, findings)
        path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), "mvv_checked": checked}), encoding="utf-8")
        _, got = run_cli(capsys, "finish", "--summary", str(path))
        assert [(g["kind"], g["name"]) for g in got["outputs"]["gaps"]] == [(fin.GAP_MVV, n) for n in names]
        assert got["status"] == ("stopped" if names else "ok")
        assert not names or got["cause"]["kind"] == "mvv_unresolved"


def test_unread_reviewer_with_findings_left_is_still_a_valid_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """相手役が差分を読めずに止まったときの欠けは、結果 JSON の outputs にだけ載せる（最上位の gaps は incomplete だけが持てる）。"""
    left = {"id": "F1", "text": "止まる", "outcome": fin.UNRESOLVED, "severity": "major"}
    code, got = run_cli(capsys, "finish", "--summary", str(_summary(tmp_path, fin.INCOMPLETE_REVIEWERS, 1, [left])))
    assert result.errors(got, code) == [] and got["cause"]["kind"] == "work_stopped"
    assert [g["name"] for g in got["outputs"]["gaps"]] == ["F1"]


@pytest.mark.parametrize("severity", ["major", "minor"])
def test_redlines_are_not_gaps_but_are_counted(tmp_path: Path, capsys: pytest.CaptureFixture[str], severity: str) -> None:
    """必ず人の承認が要る操作の指摘だけが残っていれば収束とし、件数を要約に載せる。"""
    left = {"id": "F1", "text": "消す", "outcome": fin.UNRESOLVED, "severity": severity, "mvv": "P1", "redline": True}
    shown = {**left, "id": "F2", "outcome": fin.SKIPPED}
    code, got = run_cli(capsys, "finish", "--summary", str(_summary(tmp_path, fin.CONVERGED, 1, [left, shown])))
    assert (code, got["status"]) == (0, "ok")
    assert got["summary"].endswith("指摘 2 件（直した 0・見送った 1・未解決 0・人の承認を待つ 1）")


def test_error_before_any_round_is_work_stopped() -> None:
    assert fin.finish(fin.Review(fin.ERROR, 0, ())).kind == "work_stopped"


@pytest.mark.parametrize(("final", "outcome"), [("done", fin.FIXED), (fin.CONVERGED, "maybe")])
def test_unknown_words_are_invalid(final: str, outcome: str) -> None:
    review = fin.Review(final, 1, (fin.Finding("F1", "x", outcome, None),))
    assert fin.finish(review).kind == "invalid"


def test_malformed_summary_is_invalid(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, got = run_cli(capsys, "finish", "--summary", str(_summary(tmp_path, fin.CONVERGED, 1, ["x"])))
    assert code != 0 and got["cause"]["kind"] == "invalid"
