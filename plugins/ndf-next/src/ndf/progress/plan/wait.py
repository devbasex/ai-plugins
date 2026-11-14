"""オーガナイザーが計画の知らせを待つ `ndf lanes wait`（#3097・SF9）。判断は `wait_decide` が持つ。

計画とレーンの進行表を `parallel-wait.poll` の間隔で読み直し、知らせ・計画の終わり・まだ知らせていない止まりの
どれかが出たら返す。上限の秒を過ぎたら、知らせが無いまま同じ番号で返す（結果の `next` で打ち直す）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from ndf.contracts import limits
from ndf.progress import lane, model
from ndf.progress import plan as table
from ndf.progress.plan import wait_decide
from ndf.progress.plan.wait_decide import Seen

POLL = "parallel-wait.poll"


def read_seen(conn: sa.Connection, plan_id: str, after: int, now: datetime) -> Seen:
    """計画の進行表の番号より後の知らせと、止まりの鍵を読む。"""
    view, events = lane.read_plan(conn, plan_id)
    found = wait_decide.notices([e.as_dict() for e in events], after)
    out = table.status(conn, plan_id, now)
    stalls = [f"{i['lane']}/{r['row']}" for i in out["items"] for r in i["rows"] if "awaiting_next" in r and r["awaiting_next"]["stalled"]]
    last = max([after, *(e["seq"] for e in found)])
    return Seen(state=view.state, ended=model.is_terminal(view.state), notices=tuple(found), last_seq=last, stalls=tuple(stalls))


def wait(
    read: Callable[[], Seen],
    known: list[str],
    seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Seen:
    """知らせが出るか上限の秒を過ぎるまで読み直す。"""
    interval = limits.value(POLL, "interval")
    deadline = clock() + seconds
    seen = read()
    while not wait_decide.done(seen, known) and clock() < deadline:
        sleep(interval)
        seen = read()
    return seen


def report(plan: str, seen: Seen, known: list[str]) -> dict[str, Any]:
    """結果の中身（要約・知らせ・次に打つ wait）。"""
    new_stalls = sorted(set(seen.stalls) - set(known))
    ended = seen.ended
    summary = f"計画 {plan}: 知らせ {len(seen.notices)}・新しい止まり {len(new_stalls)}" + ("・計画は終わった" if ended else "")
    return {
        "summary": summary,
        "items": list(seen.notices),
        "metrics": {"state": seen.state, "last_seq": seen.last_seq, "stalls": list(seen.stalls), "new_stalls": new_stalls},
        "next": None if ended else wait_decide.next_wait(plan, seen),
    }


def now() -> datetime:
    return datetime.now(UTC)
