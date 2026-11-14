"""順位の算出と plan の照合（#2232・#1595）。"""

from __future__ import annotations

from ndf.domains.backlog import model, plan_decide, rank_decide
from ndf.domains.backlog.model import APPROVAL, BACKWARD, FORWARD, Config, Estimate

NEAR, NEXT = "01 直近", "02 次"


def _est(n: int, ubv: int, size: int = 1, waiting: bool = False) -> Estimate:
    return Estimate(n, ubv, 1, 1, size, digest=f"d{n}", waiting_decision=waiting)


def _board(ests: list[Estimate], place: dict[int, str | None], capacity: int | None = 3, **kw) -> rank_decide.Board:
    issues = {e.number: {"milestone": place[e.number], "labels": [], "created_at": "2026-10-01"} for e in ests}
    return rank_decide.Board(issues, {e.number: e for e in ests}, Config(capacity=capacity), [NEAR, NEXT], **kw)


def test_boundary_skips_rows_waiting_for_a_decision():
    # #2232: 順位 1〜4 が判断待ちで容量の宣言があるとき、境界は 5 位以降から取る
    ests = [_est(n, 20, waiting=True) for n in (1, 2, 3, 4)] + [_est(5, 8), _est(6, 5), _est(7, 3)]
    board = _board(ests, dict.fromkeys(range(1, 8), NEAR))
    released = rank_decide.released_counts(board.edges)
    rows = [model.score(e, [], released.get(e.number, 0), board.cfg) for e in ests]
    ranked = sorted(rows, key=model.row_key)
    _, _, bnd = rank_decide.slots(board, ranked)
    assert bnd["number"] == 7 and bnd["size_sum"] == 3


def test_boundary_is_none_when_every_row_waits():
    ests = [_est(n, 20, waiting=True) for n in (1, 2)]
    board = _board(ests, dict.fromkeys((1, 2), NEAR))
    rows = sorted((model.score(e, [], 0, board.cfg) for e in ests), key=model.row_key)
    assert rank_decide.slots(board, rows)[2] is None


def _forward_board(rejected=()) -> rank_decide.Board:
    # 直近に CoD の低い 2 件（境界は #2）、次のマイルストーンに CoD の高い #9
    ests = [_est(1, 2), _est(2, 1), _est(9, 20)]
    return _board(ests, {1: NEAR, 2: NEAR, 9: NEXT}, capacity=2, rejected=tuple(rejected))


def test_forward_and_backward_candidates():
    got = rank_decide.compute(_forward_board())
    assert [f["number"] for f in got["forward"]] == [9]
    assert got["forward"][0]["decision"] == APPROVAL
    assert [b["number"] for b in got["backward"]] == [2]
    assert got["rejected_stale"] == []


def test_backward_rejection_is_not_stale_when_no_backward_was_built():
    # #1595 の関連 2: 前倒しを全部退け、後ろ倒しを組まなかった回は、後ろ倒しの却下を古いと見なさない
    first = rank_decide.compute(_forward_board())
    fwd, back = first["forward"][0], first["backward"][0]
    rejected = [{"number": 9, "direction": FORWARD, "move_digest": fwd["move_digest"]},
                {"number": 2, "direction": BACKWARD, "move_digest": back["move_digest"]}]  # fmt: skip
    got = rank_decide.compute(_forward_board(rejected))
    assert got["forward"] == [] and got["backward"] == []
    assert got["rejected_stale"] == []
    assert [d["number"] for d in got["dropped"]] == [9]


def test_rejection_of_a_direction_that_was_built_but_not_matched_is_stale():
    rejected = [{"number": 9, "direction": FORWARD, "move_digest": "old"}]
    got = rank_decide.compute(_forward_board(rejected))
    assert got["rejected_stale"] == [{"number": 9, "direction": FORWARD, "move_digest": "old"}]


def _metrics_1595() -> dict:
    """#1595 の最小の形: #23（group に 21・33・36）・#33・#36・#21 がこの順に並ぶ。"""

    def cand(n, group=()):
        return {"number": n, "to": NEAR, "group": list(group), "decision": APPROVAL, "move_digest": f"A{n}", "cod": 17}

    return {"forward": [cand(23, (21, 33, 36)), cand(33), cand(36), cand(21)], "backward": [], "rejected_stale": []}


def test_rejected_moves_are_recorded_with_their_own_candidate_digest():
    # #1595: 4 件がそれぞれ本人の move_digest で残る（依存先として一致した #23 の値で記録しない）
    plan = {"rejected": [{"number": n, "direction": FORWARD} for n in (21, 23, 33, 36)]}
    metrics = _metrics_1595()
    assert plan_decide.check(plan, metrics).errors == []
    got = plan_decide.rejected_after([], plan, metrics)
    assert {(r["number"], r["move_digest"]) for r in got} == {(21, "A21"), (23, "A23"), (33, "A33"), (36, "A36")}


def test_rejecting_a_number_that_is_only_a_dependency_is_an_error():
    metrics = {"forward": [{"number": 23, "to": NEAR, "group": [21], "decision": APPROVAL, "move_digest": "A23"}]}
    errors = plan_decide.check({"rejected": [{"number": 21, "direction": FORWARD}]}, metrics).errors
    assert len(errors) == 1 and "#21 は #23 の依存先" in errors[0]


def test_a_dependency_moves_only_together_with_its_lead():
    metrics = {"forward": [{"number": 23, "to": NEAR, "group": [21], "decision": APPROVAL, "move_digest": "A23"}]}
    both = {
        "reschedule": [{"number": 23, "direction": FORWARD, "milestone": NEAR}, {"number": 21, "direction": FORWARD, "milestone": NEAR}]
    }
    checked = plan_decide.check(both, metrics)
    assert checked.errors == [] and checked.holds == {21, 23}
    alone = {"reschedule": [{"number": 21, "direction": FORWARD, "milestone": NEAR}]}
    assert plan_decide.check(alone, metrics).errors
    lead_only = {"reschedule": [{"number": 23, "direction": FORWARD, "milestone": NEAR}]}
    assert "依存先 #21" in plan_decide.check(lead_only, metrics).errors[0]


def test_stale_rejections_are_removed_by_apply():
    metrics = {**_metrics_1595(), "rejected_stale": [{"number": 5, "direction": FORWARD, "move_digest": "S"}]}
    got = plan_decide.rejected_after([{"number": 5, "direction": FORWARD, "move_digest": "S"}], {"rejected": []}, metrics)
    assert got == []
