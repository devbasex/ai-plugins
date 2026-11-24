"""プランの承認資料の「MVV 判定」の節（`gate.mvv_lines`。#3959 の受け入れ条件 3）。

判定の出力 `mvv_gate` を、質問の PR と資料を作る時点の head に照らして載せる。一致しなければ判定の本文を載せない。
"""

from __future__ import annotations

from ndf.domains.records import mvv_material_decide as mm
from ndf.progress import gate, model

STOP_TEXT = mm.STOP_REASONS["mvv_unapproved"]


def judged(**over):
    """PR #7・head h1 の判定（理由 2 件・レッドライン 1 件・保留 2 件・why 1 件・コメントあり）。"""
    base = {
        "gate": "design",
        "skip": False,
        "verdict": {"verdict": "follow", "reasons": ["理由 A", "理由 B"], "boundary": ["線 C"], "basis": ["Value 1", "Value 4"]},
        "why": ["省かない理由 D"],
        "holds": ["保留 E", "保留 F"],
        "noted": True,
        "note_url": "https://github.com/o/r/pull/7#issuecomment-9",
        "pr": 7,
        "head": "h1",
    }
    return {**base, **over}


def text(out, gate_name="design", pr=7, head="h1", **kw):
    return "\n".join(gate.mvv_lines(out, gate_name, pr, head, **kw))


FREE = ("理由 A", "理由 B", "線 C", "省かない理由 D", "保留 E", "保留 F")


def test_a_matching_verdict_lists_the_pr_the_verdict_why_and_every_hold():
    got = text(judged())
    assert got.splitlines()[0] == "- 判定した PR: #7・head h1"
    assert all(w in got for w in (*FREE, "follow", "Value 1・Value 4", "今回の判定のコメント: https://github.com/o/r/pull/7#issuecomment-9"))


def test_empty_holds_read_as_none_without_claiming_the_records_were_read():
    got = text(judged(holds=[]))
    assert "レビューの保留: 無し（レビューの記録に保留が無い。記録がまだ作られていない場合を含む）" in got and "確かめた" not in got


def test_null_holds_read_as_unconfirmed_with_the_reason_of_each_case():
    assert "未確認（保留を読む前に判定が止まった: ベースを読めない）" in text(judged(verdict=None, holds=None, why=["ベースを読めない"]))
    assert "未確認（リリースの保留は #4105 まで読まない（省かない））" in text(judged(gate="release", holds=None), "release")
    # 単位 2 が入り単位 3 がまだの間の fast で、検査がすべて通った判定（why が空）
    got = text(judged(holds=None, why=[]))
    assert "未確認（この判定は保留を読んでいない）" in got and "止まった" not in got
    assert "未確認（保留を読む前に判定が止まった）" in text(judged(verdict=None, holds=None, why=[]))


def test_a_verdict_without_a_comment_says_so_and_has_no_url():
    got = text(judged(noted=False, note_url=None))
    assert "今回は PR に判定のコメントを載せていない" in got and "issuecomment" not in got


def test_no_verdict_output_is_unjudged_and_unconfirmed():
    got = text(None)
    assert "判定できない" in got and "レビューの保留: 未確認" in got


def test_an_older_head_of_the_same_pr_shows_no_free_text():
    got = text(judged(), head="h2")
    assert "承認を求める PR #7・head h2 の判定はまだ無い" in got and "head h1 に対するもの" in got
    assert "未確認（今の head の判定が無い）" in got and not any(w in got for w in FREE) and "issuecomment" not in got


def test_another_pr_hides_its_number_and_head_even_with_the_same_head():
    for out in (judged(pr=99), judged(pr=99, head="h9")):
        got = text(out)
        assert "承認を求める PR #7・head h1 に対する判定はまだありません" in got and "未確認" in got
        assert "#99" not in got and "h9" not in got and "issuecomment" not in got and not any(w in got for w in FREE)


def test_a_verdict_that_cannot_be_matched_says_it_cannot_be_confirmed():
    old = judged()
    del old["pr"]
    for got in (text(old), text(judged(), head=None)):
        assert "確かめられません" in got and "判定した PR" not in got and not any(w in got for w in FREE)


def test_a_stop_before_the_pr_shows_the_fixed_stop_text_without_a_pr_or_head():
    got = text(mm.stop_outcome("design", "no_pr", ["PR を読めない"]))
    assert got.startswith("- 対象の PR を特定できなかったため、MVV 判定は済んでいない。" + mm.STOP_REASONS["no_pr"])
    assert "今ある判定は" not in got and "未確認" in got


def withheld(**over):
    """閲覧範囲を限れない保存先から読み戻した判定（自由記述が伏せてある）。"""
    return model.mvv_stored_view(judged(**over))


def test_an_unrestricted_store_shows_counts_and_points_to_the_comment():
    got = text(withheld(), private=False)
    assert "理由 2 件・レッドライン 1 件" in got and "レビューの保留: 2 件" in got and "省かない理由 1 件" in got
    assert "今回の判定のコメント https://github.com/o/r/pull/7#issuecomment-9 で読めます" in got and model.MVV_WITHHELD not in got


def test_an_unrestricted_store_without_a_comment_asks_to_redo_the_verdict():
    assert "読める場所がありません" in text(withheld(noted=False, note_url=None), private=False)
    # 保留の本文があるなら、判定が無くても例外にしない
    assert "読める場所がありません" in text(withheld(verdict=None, holds=["保留 E"], noted=False, note_url=None), private=False)


def test_an_unrestricted_store_with_nothing_to_read_does_not_ask_to_redo():
    stop = {"kind": "mvv_unapproved", "text": STOP_TEXT}
    for holds in (None, []):
        got = text(withheld(verdict=None, holds=holds, why=["x"], noted=False, note_url=None, stopped_by=stop), private=False)
        assert f"判定できない。理由: {STOP_TEXT}" in got and "読める場所がありません" not in got and "やり直し" not in got
