"""プランの承認資料の「MVV 判定」の欄（`gate.mvv_lines`。#3959 の受け入れ条件 3）。

判定の出力 `mvv_gate` を、質問の PR と資料を作る時点の head に照らして載せる。一致しなければ判定の本文を載せない。
閲覧範囲を限れない保存先（`private` が偽）から読んだ判定は、本文の代わりに件数と全文の参照先を載せる。
"""

from __future__ import annotations

import pytest

from ndf.domains.records import mvv, mvv_decide, mvv_gate
from ndf.domains.records import mvv_material_decide as mm
from ndf.progress import gate, model

URL = "https://github.com/o/r/pull/7#issuecomment-9"
STOP = {"kind": "mvv_unapproved", "text": mm.STOP_REASONS["mvv_unapproved"]}
FREE = ("理由 A", "理由 B", "線 C", "省かない理由 D", "保留 E", "保留 F")
REDO = "読める場所がありません"


def judged(**over):
    """PR #7・head h1 の判定（理由 2 件・レッドライン 1 件・保留 2 件・why 1 件・コメントあり）。"""
    base = {
        "gate": "design",
        "skip": False,
        "verdict": {"verdict": "follow", "reasons": ["理由 A", "理由 B"], "boundary": ["線 C"], "basis": ["Value 1", "Value 4"]},
        "why": ["省かない理由 D"],
        "holds": ["保留 E", "保留 F"],
        "noted": True,
        "note_url": URL,
        "pr": 7,
        "head": "h1",
    }
    return {**base, **over}


def _without_pr():
    out = judged()
    del out["pr"]
    return out


def _stored(**over):
    """閲覧範囲を限れない保存先から読み戻した判定（自由記述が伏せてある）。"""
    return model.mvv_stored_view(judged(**over))


# （判定の出力, 区分, 質問の head, private, 載る文, 載らない文）
CASES = {
    "matching": (
        judged(),
        "design",
        "h1",
        True,
        ("- 判定した PR: #7・head h1", *FREE, "follow", "Value 1・Value 4", f"今回の判定のコメント: {URL}"),
        (),
    ),
    "holds-empty": (
        judged(holds=[]),
        "design",
        "h1",
        True,
        ("レビューの保留: 無し（レビューの記録に保留が無い。記録がまだ作られていない場合を含む）",),
        ("確かめた",),
    ),
    "holds-null-stopped": (
        judged(verdict=None, holds=None, why=["ベースを読めない"]),
        "design",
        "h1",
        True,
        ("未確認（保留を読む前に判定が止まった: ベースを読めない）",),
        (),
    ),
    "holds-null-release": (
        judged(gate="release", holds=None),
        "release",
        "h1",
        True,
        ("未確認（リリースの保留は #4105 まで読まない（省かない））",),
        (),
    ),
    # 単位 2 が入り単位 3 がまだの間の fast で、検査がすべて通った判定（why が空）
    "holds-null-unread": (judged(holds=None, why=[]), "design", "h1", True, ("未確認（この判定は保留を読んでいない）",), ("止まった",)),
    "holds-null-empty-why": (
        judged(verdict=None, holds=None, why=[]),
        "design",
        "h1",
        True,
        ("未確認（保留を読む前に判定が止まった）",),
        (),
    ),
    "no-comment": (
        judged(noted=False, note_url=None),
        "design",
        "h1",
        True,
        ("今回は PR に判定のコメントを載せていない",),
        ("issuecomment",),
    ),
    "no-output": (None, "design", "h1", True, ("判定できない", "レビューの保留: 未確認"), ()),
    "older-head": (
        judged(),
        "design",
        "h2",
        True,
        ("承認を求める PR #7・head h2 の判定はまだ無い", "head h1 に対するもの", "未確認（今の head の判定が無い）"),
        (*FREE, "issuecomment"),
    ),
    "other-pr": (
        judged(pr=99),
        "design",
        "h1",
        True,
        ("承認を求める PR #7・head h1 に対する判定はまだありません", "未確認"),
        (*FREE, "#99", "issuecomment"),
    ),
    "other-pr-other-head": (
        judged(pr=99, head="h9"),
        "design",
        "h1",
        True,
        ("に対する判定はまだありません",),
        (*FREE, "#99", "h9", "issuecomment"),
    ),
    "no-pr": (_without_pr(), "design", "h1", True, ("確かめられません",), (*FREE, "判定した PR")),
    "no-head-read": (judged(), "design", None, True, ("確かめられません",), (*FREE, "判定した PR")),
    "stop-before-pr": (
        mm.stop_outcome("design", "no_pr", ["PR を読めない"]),
        "design",
        "h1",
        True,
        ("- 対象の PR を特定できなかったため、MVV 判定は済んでいない。" + mm.STOP_REASONS["no_pr"], "未確認"),
        ("今ある判定は",),
    ),
    # MVV を導入していないリポジトリは PR を読む前に止める（mvv_gate.mvv_declared_locally）。PR の特定の失敗と書かない
    "no-mvv-before-pr": (
        mm.stop_outcome("design", "mvv_unapproved", [mvv_gate.NO_DECL]),
        "design",
        "h1",
        True,
        ("- MVV 判定は打っていない。" + mm.STOP_REASONS["mvv_unapproved"], "未確認"),
        ("対象の PR を特定できなかった",),
    ),
    "unrestricted": (
        _stored(),
        "design",
        "h1",
        False,
        ("理由 2 件・レッドライン 1 件", "レビューの保留: 2 件", "省かない理由 1 件", f"今回の判定のコメント {URL} で読めます"),
        (model.MVV_WITHHELD,),
    ),
    "unrestricted-no-comment": (_stored(noted=False, note_url=None), "design", "h1", False, (REDO,), ()),
    # 保留の本文があるなら、判定が無くても例外にしない
    "unrestricted-hold-only": (_stored(verdict=None, holds=["保留 E"], noted=False, note_url=None), "design", "h1", False, (REDO,), ()),
    # 読むべき本文が無ければ、やり直しを求めない（承認済みの MVV が無いリポジトリでは毎回この形）
    "unrestricted-nothing-null": (
        _stored(verdict=None, holds=None, why=["x"], noted=False, note_url=None, stopped_by=STOP),
        "design",
        "h1",
        False,
        (f"判定できない。理由: {STOP['text']}",),
        (REDO, "やり直し"),
    ),
    "unrestricted-nothing-empty": (
        _stored(verdict=None, holds=[], why=["x"], noted=False, note_url=None, stopped_by=STOP),
        "design",
        "h1",
        False,
        (f"判定できない。理由: {STOP['text']}",),
        (REDO, "やり直し"),
    ),
}


@pytest.mark.parametrize("case", CASES.values(), ids=CASES.keys())
def test_the_mvv_lines_match_the_verdict_to_the_question_and_carry_no_citation(case):
    out, gate_name, head, private, present, absent = case
    lines = gate.mvv_lines(out, gate_name, 7, head, private=private)
    text = "\n".join(lines)
    assert all(w in text for w in present), text
    assert not any(w in text for w in absent), text
    # プランの資料は根拠の行を写さない。cite と同じ確かめに通して誤りが 0 件であることを固定する
    body = "\n".join(["# 承認資料", "", "## MVV 判定", "", *lines]) + "\n"
    fenced, decided = mvv.fenced_lines(body), mvv.decision_lines(body)
    rows = [("material.md", no, line, no in decided) for no, line in enumerate(body.splitlines(), 1) if no not in fenced]
    assert mvv_decide.citation_errors(rows, {11: ["Value 1", "Value 4"]}) == []


def test_the_stored_view_keeps_counts_and_every_mark_of_the_verdict():
    held = judged(skip=True, stopped_by={"kind": "pr_moved", "text": "定型文"}, why=["w1", "w2", "w3"])
    got, w = model.mvv_stored_view(held), model.MVV_WITHHELD
    assert got["verdict"]["reasons"] == [w, w] and got["verdict"]["boundary"] == [w] and got["why"] == [w] * 3 and got["holds"] == [w, w]
    assert {k: got[k] for k in ("skip", "pr", "head", "note_url", "noted", "stopped_by", "gate")} == {
        k: held[k] for k in ("skip", "pr", "head", "note_url", "noted", "stopped_by", "gate")
    }
    assert (got["verdict"]["verdict"], got["verdict"]["basis"]) == ("follow", ["Value 1", "Value 4"])
    assert [model.mvv_stored_view({**held, "holds": h})["holds"] for h in ([], None)] == [[], None]
    # モデルが根拠の項目へ写した自由な文字列は、閲覧範囲を限れない保存先と資料へ載せない（#3959 の r1-4）
    basis = ["Value 1", "C4", "P1", "Mission", "スプリント Value 2", "token=ghp_xxx", "Value 1 に沿う", 3]
    held = judged(verdict={**judged()["verdict"], "basis": basis})
    kept = ["Value 1", "C4", "P1", "Mission", "スプリント Value 2"]
    assert model.mvv_stored_view(held)["verdict"]["basis"] == kept
    text = "\n".join(gate.mvv_lines(held, "design", 7, "h1", private=False))
    assert "根拠の項目: " + "・".join(kept) in text and "ghp_xxx" not in text
