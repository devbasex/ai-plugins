"""`scripts/measure/ai-branching.py` の `_Conversation.targets` の今の振る舞いを固定する（I-006 の直しの前）。

固定する点:
- 呼び出しが無ければ `[("R03", "no_prior_result")]`。`next` は先頭のプランの 1 件だけ
- `--resume` が無ければ `--from` の対象だけ。空なら `[("R03", "no_prior_result")]` を返す
- `--resume` があれば `--from` の対象に、完了済みでも `--from` の対象でもないプランを足す。空のまま返す（架空の対象を足さない）
- 名前の分からないプランは、名前だけ分かる `--from` の対象とパスと結べなかった items の名前に当てて重ねて数えない
"""

import importlib.util
from pathlib import Path

import pytest

AB = Path(__file__).resolve().parent.parent / "measure" / "ai-branching.py"
NO_PRIOR = ("R03", "no_prior_result")
GATE = ("R13", "gate")


@pytest.fixture(scope="module")
def ab():
    spec = importlib.util.spec_from_file_location("ai_branching_targets_under_test", AB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def call(ab, cmd: str) -> dict:
    return ab.plan_call(cmd.split(), "/w")


def result(*items: dict, summary: str = "x") -> dict:
    return {"tool": "plan", "status": "gate", "summary": summary, "items": list(items)}


def gate_row(name: str) -> dict:
    return {"kind": "plan", "name": name, "step": "gate", "gate": True}


def done_row(name: str) -> dict:
    return {"kind": "plan", "name": name, "status": "ok"}


# ---------- --resume の無い形 ----------


def test_no_call_is_no_prior_result(ab):
    assert ab._Conversation().targets(None) == [NO_PRIOR]


@pytest.mark.parametrize("seen, expected", [(False, [NO_PRIOR]), (True, [GATE])])
def test_next_has_one_target_for_the_first_plan(ab, seen, expected):
    conv = ab._Conversation()
    if seen:
        conv.saw_result(call(ab, "ndf plan next /w/a.json"), result(gate_row("a")))
    assert conv.targets(call(ab, "ndf plan next /w/a.json")) == expected


def test_queue_without_resume_or_from_is_no_prior_result(ab):
    conv = ab._Conversation()
    conv.saw_result(call(ab, "ndf plan queue /w/a.json /w/b.json"), result(gate_row("a"), gate_row("b")))
    assert conv.targets(call(ab, "ndf plan queue /w/a.json /w/b.json")) == [NO_PRIOR]


def test_queue_without_resume_targets_only_from(ab):
    conv = ab._Conversation()
    conv.saw_result(call(ab, "ndf plan queue /w/a.json /w/b.json"), result(gate_row("a"), gate_row("b")))
    got = conv.targets(call(ab, "ndf plan queue /w/a.json /w/b.json --from a --from z=step"))
    assert got == [GATE, NO_PRIOR]


# ---------- --resume のある形 ----------


def test_resume_with_all_done_returns_empty(ab):
    conv = ab._Conversation()
    conv.saw_result(call(ab, "ndf plan queue /w/a.json /w/b.json"), result(done_row("a"), done_row("b")))
    assert conv.targets(call(ab, "ndf plan queue /w/a.json /w/b.json --resume")) == []


def test_resume_skips_done_plans_and_keeps_the_rest(ab):
    conv = ab._Conversation()
    conv.saw_result(call(ab, "ndf plan queue /w/a.json /w/b.json"), result(done_row("a"), gate_row("b")))
    assert conv.targets(call(ab, "ndf plan queue /w/a.json /w/b.json --resume")) == [GATE]


def test_resume_does_not_count_from_target_twice(ab):
    conv = ab._Conversation()
    conv.saw_result(call(ab, "ndf plan queue /w/a.json /w/b.json"), result(gate_row("a"), gate_row("b")))
    got = conv.targets(call(ab, "ndf plan queue /w/a.json /w/b.json --from a --resume"))
    assert got == [GATE, GATE]


def test_resume_without_history_counts_each_plan(ab):
    conv = ab._Conversation()
    assert conv.targets(call(ab, "ndf plan queue /w/a.json /w/b.json --resume")) == [NO_PRIOR, NO_PRIOR]


def test_resume_unknown_plan_is_absorbed_by_from_name(ab):
    """名前の分からない 2 つのうち 1 つは、名前だけ分かる `--from x` に当てて足さない。"""
    conv = ab._Conversation()
    got = conv.targets(call(ab, "ndf plan queue /w/a.json /w/b.json --from x --resume"))
    assert got == [NO_PRIOR, NO_PRIOR]


def test_resume_unknown_plan_is_absorbed_by_unmatched_item_name(ab):
    """パスと結べなかった items の名前 c に、名前の分からないプランの 1 つを当てる。c は自分の行で寄せる。"""
    conv = ab._Conversation()
    row = {"kind": "plan", "name": "c", "status": "stopped", "cause": {"kind": "base_conflict", "detail": "衝突"}}
    conv.saw_result(call(ab, "ndf plan queue /w/a.json /w/b.json"), result(row, summary="c: 衝突"))
    got = conv.targets(call(ab, "ndf plan queue /w/a.json /w/b.json --resume"))
    assert got == [NO_PRIOR, ("R10", "base_conflict")]


def test_resume_empty_without_from_is_not_filled(ab):
    """`--resume` で空なら `--from` が無くても no_prior_result を足さない（`--resume` の無い形との違い）。"""
    conv = ab._Conversation()
    conv.saw_result(call(ab, "ndf plan queue /w/a.json"), result(done_row("a")))
    assert conv.targets(call(ab, "ndf plan queue /w/a.json --resume")) == []
    assert conv.targets(call(ab, "ndf plan queue /w/a.json")) == [NO_PRIOR]
