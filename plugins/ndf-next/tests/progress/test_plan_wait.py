"""オーガナイザーの `ndf lanes wait`（#3097・SF9 の待ちの結果の行き先、3346-5 の止まりの知らせ）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ndf.progress import lane, plan, store
from ndf.progress.plan import wait, wait_decide
from ndf.progress.plan.wait_decide import Seen

REPO = "https://example.invalid/o/r"


def _doc():
    lanes = [{"name": "a", "plans": 1, "touches": ["src/a"], "after": [], "rows": [{"kind": "sprint", "issues": [1]}]}]
    return {"version": 1, "name": "p6", "rules": [], "lanes": lanes}


def _seen(notices=(), state="running", stalls=(), last=0):
    return Seen(state=state, ended=state in ("completed", "cancelled"), notices=tuple(notices), last_seq=last, stalls=tuple(stalls))


def test_notices_return_every_event_after_the_number_not_only_the_latest():
    events = [
        {"seq": 1, "kind": "created"},
        {"seq": 2, "kind": "request_opened"},
        {"seq": 3, "kind": "lane_added"},
        {"seq": 4, "kind": "request_opened"},
    ]
    assert [e["seq"] for e in wait_decide.notices(events, 1)] == [2, 4]
    assert wait_decide.notices(events, 4) == []


def test_wait_ends_on_a_notice_an_ended_plan_or_a_new_stall():
    assert not wait_decide.done(_seen(), [])
    assert wait_decide.done(_seen(notices=[{"seq": 2}]), [])
    assert wait_decide.done(_seen(state="cancelled"), [])
    assert wait_decide.done(_seen(stalls=["a/r1"]), [])
    assert not wait_decide.done(_seen(stalls=["a/r1"]), ["a/r1"])


def test_next_wait_carries_the_last_number_and_the_known_stalls():
    got = wait_decide.next_wait("p6", _seen(stalls=["a/r1"], last=7))
    assert got["argv"][1:] == ["lanes", "wait", "p6", "--after", "7", "--known-stalls", "a/r1"]


def test_wait_reads_again_until_something_happens():
    reads = iter([_seen(), _seen(), _seen(notices=[{"seq": 3}], last=3)])
    slept = []
    clock = iter(range(100))
    seen = wait.wait(lambda: next(reads), [], 50, sleep=slept.append, clock=lambda: next(clock))
    assert seen.last_seq == 3 and len(slept) == 2


def test_wait_returns_at_the_deadline_with_the_same_number():
    clock = iter([0, 0, 1, 2, 3])
    seen = wait.wait(lambda: _seen(last=5), [], 2, sleep=lambda s: None, clock=lambda: next(clock))
    out = wait.report("p6", seen, [])
    assert out["items"] == [] and out["next"]["argv"][-2:] == ["--after", "5"]


def test_read_seen_finds_the_request_and_the_answer_without_next(opened):
    with store.transaction(opened) as conn:
        pid = plan.create_plan(conn, REPO, _doc(), True)
        view, events = lane.read_plan(conn, pid)
        after = events[-1].seq
        sid = store.create_table(conn, ("sprint", REPO, "a-r1"), view.lanes["a"].table_id, None, [])
        lane.start_row(conn, pid, "a", "r1", sid)
        lane.record_answer(conn, sid, {"choice": "approved"})
        key = plan.open_request(conn, pid, {"lane": "a", "kind": "lane_add"}, "レーンを足したい", "organizer")
    later = datetime.now(UTC) + timedelta(seconds=plan.ANSWER_NEXT_GRACE_SECONDS + 60)
    with store.transaction(opened) as conn:
        seen = wait.read_seen(conn, pid, after, later)
    assert [n["row_key"] for n in seen.notices] == [key] and seen.stalls == ("a/r1",)
    out = wait.report("p6", seen, [])
    assert out["metrics"]["new_stalls"] == ["a/r1"] and out["next"]["argv"][-1] == "a/r1"
