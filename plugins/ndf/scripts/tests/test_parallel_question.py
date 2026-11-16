"""`lib/parallel_question.py` の同じ問いの判定（#3603 の決定 7・8・9）: 対象の行・前の依頼の引き当て・結果の復元・`asked`。"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
import parallel_question as pq  # noqa: E402

SHA = "81d3baa5" + "0" * 32
TARGET = f"sprint/lr-t2@{SHA}"


def ti(question: str) -> dict:
    return {"questions": [{"question": question, "options": [{"label": "消す"}]}], "metadata": {"source": "ndf-ask:redline:4"}}


QUESTION = "ブランチを消してよいか。\n対象: sprint/lr-t2"
OPTIONS = ["消す", "残す"]


def ask(
    rid: str, lane: str = "p6-rel17", source: str = "ndf-ask:redline:4", target=TARGET, ledger_id: str = "", question=QUESTION, options=None
) -> dict:
    msg = {"kind": "ask", "id": rid, "lane": lane, "source": source, "target": target, "ledger_id": ledger_id}
    msg.update(question=question, options=list(OPTIONS if options is None else options))
    return {"kind": "ask", "id": rid, "msg": msg}


def prior(rows, lane="p6-rel17", source="ndf-ask:redline:4", target=TARGET, question=QUESTION, options=None):
    return pq.prior_of(rows, lane, source, target, question, OPTIONS if options is None else options)


def answer(rid: str, choice=("消す",), result: str = "answered") -> dict:
    return {"kind": "answer", "id": rid, "msg": {"kind": "answer", "id": rid, "result": result, "choice": list(choice)}}


@pytest.mark.parametrize(
    "question, want",
    [
        ("消してよいか。\n対象: sprint/lr-t2", "sprint/lr-t2"),
        ("対象: a\n  対象:  #12  ", "#12"),  # 2 行あれば最後・前後の空白は読み捨てる
        ("対象の行が無い問い sprint/lr-t2", ""),
    ],
)
def test_target_line_reads_the_last_target_line(question, want):
    assert pq.target_line(ti(question)) == want


def test_target_line_reads_only_the_first_question():
    """受け入れ条件 17: 対象の行は最初の問いの文からだけ読む。"""
    got = {"questions": [{"question": "一つ目"}, {"question": "対象: sprint/x"}]}
    assert pq.target_line(got) == ""


def test_prior_of_returns_the_newest_same_question_and_its_answer():
    """受け入れ条件 13: 同じ名前・区分・対象の依頼のうち最も新しいものと答え。"""
    rows = [ask("p6-rel17-qa"), answer("p6-rel17-qa", ["残す"]), ask("p6-rel17-qb"), answer("p6-rel17-qb")]
    got = prior(rows)
    assert got == pq.Prior("p6-rel17-qb", {"result": "answered", "choice": ["消す"]})


def test_prior_of_without_answer_returns_the_waiting_id():
    """受け入れ条件 15: 答え待ちなら id だけを返す（答えは None）。"""
    assert prior([ask("p6-rel17-qa")]) == pq.Prior("p6-rel17-qa", None)


@pytest.mark.parametrize(
    "lane, source, target",
    [
        ("p6-rel18", "ndf-ask:redline:4", TARGET),  # 受け入れ条件 16: 別の名前の一時レーン
        ("p6-rel17", "ndf-ask:cost", TARGET),  # 別の区分
        ("p6-rel17", "ndf-ask:redline:4", "sprint/lr-t2@" + "1" * 40),  # 受け入れ条件 14: 先端が動いた
        ("p6-rel17", "ndf-ask:redline:4", ""),  # 受け入れ条件 18: 対象が空
    ],
)
def test_prior_of_does_not_match_other_questions(lane, source, target):
    rows = [ask("p6-rel17-qa"), answer("p6-rel17-qa")]
    assert prior(rows, lane, source, target) is None


def test_prior_of_never_matches_empty_targets_with_each_other():
    """I6: 対象が空の前の依頼どうしを同じ問いにしない。"""
    rows = [ask("p6-rel17-qa", target=None), answer("p6-rel17-qa")]
    assert prior(rows, target="") is None


@pytest.mark.parametrize(
    "question, options",
    [
        ("強制 push してよいか。\n対象: sprint/lr-t2", OPTIONS),  # 同じ区分・同じ先端で問いの中身が違う（PR #3652 の指摘）
        (QUESTION, ["残す", "消す"]),  # 選択肢の並びが違う
    ],
)
def test_prior_of_redline_needs_the_same_question_and_options(question, options):
    """受け入れ条件 13（PR #3652 で足した）: レッドラインの区分は問いの文面と選択肢の並びも一致したときだけ引き当てる。"""
    rows = [ask("p6-rel17-qa"), answer("p6-rel17-qa")]
    assert prior(rows, question=question, options=options) is None


def test_prior_of_redline_ignores_whitespace_in_the_question():
    """空白（改行・字下げ）の違いは同じ問いとみなす。"""
    rows = [ask("p6-rel17-qa"), answer("p6-rel17-qa")]
    got = prior(rows, question="  ブランチを消してよいか。\n\n  対象:  sprint/lr-t2 ")
    assert got == pq.Prior("p6-rel17-qa", {"result": "answered", "choice": ["消す"]})


def test_prior_of_other_sources_compare_only_the_target():
    """レッドラインでない区分は今どおり名前・区分・対象だけで引き当てる。"""
    rows = [ask("p6-rel17-qa", source="ndf-ask:cost"), answer("p6-rel17-qa")]
    assert prior(rows, source="ndf-ask:cost", question="別の文面", options=["はい"]).id == "p6-rel17-qa"


def test_outcome_of_restores_each_kind():
    """I10: 判定の記録の id を持つ行から request-ask の metrics と同じ形を戻す。"""
    gate1 = {"kind": "gate1", "id": "la-g12-x", "msg": {"id": "la-g12-x", "ledger_id": "L1", "sprint_state": "/w/s.json"}}
    reused = {"kind": "reused", "id": "p6-rel17-qa", "msg": {"id": "p6-rel17-qa", "ledger_id": "L3", "answered": True, "target": TARGET}}
    rows = [gate1, ask("p6-rel17-qa", ledger_id="L2"), answer("p6-rel17-qa"), reused]
    assert pq.outcome_of(rows, "L1") == {"kind": "gate1", "id": "la-g12-x", "target": None, "sprint_state": "/w/s.json"}
    assert pq.outcome_of(rows, "L2") == {"kind": "ask", "id": "p6-rel17-qa", "target": TARGET}
    assert pq.outcome_of(rows, "L3")["answer"] == {"result": "answered", "choice": ["消す"]}
    assert pq.outcome_of(rows, "L9") is None and pq.outcome_of(rows, "") is None


def test_asked_of_carries_the_answer_only_for_targeted_asks():
    """決定 9: 対象を持つ ask の答えには中身を、対象の無い ask には answered だけを載せる。"""
    rows = [ask("p6-rel17-qa", source="ndf-ask:cost"), answer("p6-rel17-qa"), ask("p6-rel17-qb", target=None), answer("p6-rel17-qb")]
    rows += [ask("p6-rel17-qc"), answer("p6-rel17-qc")]
    got = {i["id"]: i for i in pq.asked_of(rows, "p6-rel17")}
    assert got["p6-rel17-qa"]["answer"] == {"result": "answered", "choice": ["消す"]}
    assert got["p6-rel17-qb"].get("answered") is True and "answer" not in got["p6-rel17-qb"]
    # レッドラインの答えは中身を載せない（引き継ぎは問いの中身を比べる request-ask の判定だけが行う）
    assert got["p6-rel17-qc"].get("answered") is True and "answer" not in got["p6-rel17-qc"]
