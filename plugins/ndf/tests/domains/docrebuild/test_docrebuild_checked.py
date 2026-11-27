"""`drive._checked`（worker の結果ファイルを確かめて結果にする）の今の振る舞いを固定する。

確かめの記録を整形して `warnings` を作る部分を切り出す前（I-004）に、結果の全体を値で押さえる。
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from ndf.domains.docrebuild import drive
from ndf.domains.docrebuild.select_decide import Target

DESIGN = "issues/issue-75-design.md"
PLAN = "issues/issue-75-plan.md"
DOC_A = "docs/a.md"
DOC_B = "docs/b.md"
TARGETS = (Target(DESIGN, "design"), Target(PLAN, "plan"), Target(DOC_B, "manuscript"), Target(DOC_A, "manuscript"))


def _report(items: Any = (), readers: Any = None, *, metrics: Any = None) -> dict[str, Any]:
    if metrics is None:
        metrics = {"readers": readers}
    return {
        "tool": "worker",
        "status": "ok",
        "summary": "組み直した",
        "items": list(items) if isinstance(items, tuple) else items,
        "metrics": metrics,
    }


def _pass(doc: str, reason: Optional[str] = None, **extra: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {"doc": doc, "verdict": "pass", "checks": [{"verdict": "pass", "restatements": ["要点"]}], **extra}
    if reason is not None:
        rec["reason"] = reason
    return rec


def _complete_records() -> list[Any]:
    """対象 4 件の記録（並びは対象と違う）と、対象の外の記録 2 件・辞書でない要素 1 件。"""
    return [
        {
            "doc": DOC_A,
            "verdict": "fail",
            "checks": [{"verdict": "fail", "restatements": ["a1"], "reason": "質問 3 が誤読された", "extra": 1}],
            "reason": "2 回目を行えなかった",
            "note": "外す",
        },
        "not-a-dict",
        {"doc": PLAN, "verdict": "self", "checks": [], "reason": "計画は自分で確かめる"},
        {"doc": "docs/outside.md", "verdict": "fail", "checks": [{"verdict": "fail", "restatements": ["q"], "reason": "外"}]},
        {
            "doc": DESIGN,
            "verdict": "pass",
            "checks": [
                {"verdict": "fail", "restatements": ["x"], "reason": "質問 1 が誤読された"},
                {"verdict": "pass", "restatements": ["y"], "reason": "直った"},
            ],
        },
        {"doc": "docs/x.md", "verdict": "?"},
        {
            "doc": DOC_B,
            "verdict": "fail",
            "checks": [
                {"verdict": "fail", "restatements": ["b1"], "reason": "1 回目"},
                {"verdict": "fail", "restatements": ["b2"], "reason": "質問 2 が誤読された"},
            ],
            "reason": "   ",
        },
    ]


def test_checked_builds_the_whole_ok_result() -> None:
    """記録は対象の順に整形し、不合格はパスの順に `reader-<番号>`。`warnings` は外したパス・不合格・対象の外の件数の順。"""
    items = [{"id": "R9", "text": "足す 1", "doc": DOC_B}, {"text": "足す 2", "doc": DESIGN}]
    got = drive._checked(_report(items, _complete_records()), TARGETS, ["skip.md: リンク"])
    reader_a = f"読み手役が {DOC_A} を不合格とした: 質問 3 が誤読された・2 回目を行えなかった"
    reader_b = f"読み手役が {DOC_B} を不合格とした: 質問 2 が誤読された"
    assert got == {
        "tool": drive.TOOL,
        "status": "ok",
        "summary": "4 件の文書を組み直した（足すべき内容 2 件・読み手役の不合格 2 件）",
        "items": [
            {"id": "add-1", "text": "足す 1", "doc": DOC_B},
            {"id": "add-2", "text": "足す 2", "doc": DESIGN},
            {"id": "reader-1", "text": reader_a, "doc": DOC_A},
            {"id": "reader-2", "text": reader_b, "doc": DOC_B},
        ],
        "metrics": {
            "targets": [DESIGN, PLAN, DOC_B, DOC_A],
            "readers": [
                {
                    "doc": DESIGN,
                    "verdict": "pass",
                    "checks": [
                        {"verdict": "fail", "restatements": ["x"], "reason": "質問 1 が誤読された"},
                        {"verdict": "pass", "restatements": ["y"]},
                    ],
                },
                {"doc": PLAN, "verdict": "self", "checks": [], "reason": "計画は自分で確かめる"},
                {
                    "doc": DOC_B,
                    "verdict": "fail",
                    "checks": [
                        {"verdict": "fail", "restatements": ["b1"], "reason": "1 回目"},
                        {"verdict": "fail", "restatements": ["b2"], "reason": "質問 2 が誤読された"},
                    ],
                },
                {
                    "doc": DOC_A,
                    "verdict": "fail",
                    "checks": [{"verdict": "fail", "restatements": ["a1"], "reason": "質問 3 が誤読された"}],
                    "reason": "2 回目を行えなかった",
                },
            ],
        },
        # 辞書でない要素も対象の外の件数に入る（記録の総数 - 対象の数）
        "warnings": ["skip.md: リンク", reader_a, reader_b, "対象の外の文書の記録を 3 件外した"],
    }


def test_checked_without_warnings_omits_the_key_and_keeps_a_pass_reason() -> None:
    """外したパス・不合格・対象の外が無ければ `warnings` を置かない。合格の記録の `reason` は残す。"""
    targets = (Target(DESIGN, "design"),)
    got = drive._checked(_report((), [_pass(DESIGN, reason="読めた", extra="外す")]), targets, [])
    assert got == {
        "tool": drive.TOOL,
        "status": "ok",
        "summary": "1 件の文書を組み直した（足すべき内容 0 件・読み手役の不合格 0 件）",
        "items": [],
        "metrics": {
            "targets": [DESIGN],
            "readers": [{"doc": DESIGN, "verdict": "pass", "checks": [{"verdict": "pass", "restatements": ["要点"]}], "reason": "読めた"}],
        },
    }


def test_checked_keeps_skipped_only_warnings() -> None:
    targets = (Target(DESIGN, "design"),)
    got = drive._checked(_report((), [_pass(DESIGN)]), targets, ["a.md: 1", "b.md: 2"])
    assert got["warnings"] == ["a.md: 1", "b.md: 2"]
    assert got["items"] == []


def test_checked_reports_bad_items_before_missing_records() -> None:
    """足すべき内容の誤りが先に止める（記録が欠けていても `bad_report`）。"""
    got = drive._checked(_report("not-a-list", None), TARGETS, ["skip.md: x"])
    assert got["status"] == "stopped"
    assert got["cause"]["kind"] == "bad_report"
    assert got["summary"] == "結果ファイルの items が並びでない"
    assert "gaps" not in got and "warnings" not in got


@pytest.mark.parametrize(
    "report",
    [
        _report((), None),
        _report((), "x"),
        _report((), metrics="not-a-dict"),
        {"tool": "worker", "status": "ok", "summary": "s", "items": []},
    ],
)
def test_checked_stops_incomplete_when_records_are_not_a_list(report: dict[str, Any]) -> None:
    got = drive._checked(report, TARGETS, ["skip.md: x"])
    assert got["status"] == "stopped"
    assert got["cause"]["kind"] == "incomplete"
    assert got["summary"] == "確かめの記録が欠けた文書が 4 件ある"
    assert got["gaps"] == [{"kind": "reader", "name": t.path, "reason": "記録（metrics.readers）が並びでない"} for t in TARGETS]
    assert "warnings" not in got and got["items"] == []


def test_checked_stops_incomplete_only_for_the_missing_documents() -> None:
    targets = (Target(DESIGN, "design"), Target(DOC_A, "manuscript"))
    got = drive._checked(_report((), [_pass(DOC_A), _pass("docs/outside.md")]), targets, [])
    assert got["status"] == "stopped"
    assert got["summary"] == "確かめの記録が欠けた文書が 1 件ある"
    assert got["gaps"] == [{"kind": "reader", "name": DESIGN, "reason": "記録が無い"}]


def test_checked_stops_precondition_naming_every_document_not_run() -> None:
    """読み手役を立てられなかった文書は対象の順に並べ、記録の欠け（`incomplete`）より先に止める。"""
    targets = (Target(DESIGN, "design"), Target(PLAN, "plan"), Target(DOC_A, "manuscript"))
    records = [{"doc": doc, "verdict": "not_run", "checks": [], "reason": "Tool が無い"} for doc in (DOC_A, DESIGN)]
    got = drive._checked(_report((), records), targets, ["skip.md: x"])
    detail = f"読み手役を立てられなかった文書がある: {DESIGN}・{DOC_A}。読み手役を立てられる環境に直してから打ち直す"
    assert got["status"] == "stopped"
    assert got["cause"]["kind"] == "precondition"
    assert got["cause"]["detail"] == got["summary"] == detail
    assert "gaps" not in got and "warnings" not in got and got["items"] == []
