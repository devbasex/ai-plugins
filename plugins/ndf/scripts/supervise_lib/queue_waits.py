"""queue が流したプランごとに、キューで待った秒を理由ごとに数える（#2998 の決定 5〜7）。

時刻は DBOS の耐久の記録（`durable.times` の作った時刻・最後に耐久キューから出した時刻）から導き、待ちの始まりと
終わりをワークフローの中で書かない。導く処理は耐久ステップ `waits_step` 1 つにまとめ、queue を打ち直したときは
記録を返す（I5）。

| 理由 | 何の秒か |
| --- | --- |
| `overlap` | 重なりの組（`group_workflow`）の中で、前の員が終わるのを待った秒 |
| `plans` | 耐久キュー `plans` の空き（`--max`）を待った秒。見回りの遅れ（`PICKUP_SECONDS` 以下）は 0 |
| `resource` | 資源の枠の耐久キュー `res-<タグ>` の空きを待った秒の、ステップすべての和。各ステップの見回りの遅れは 0 |
| `deps` | 同じステージの前提のプランの終わりを `wait_workflow` で待った秒 |
| `plan_limit` | 計画の上限を待った秒。#2999 が導き方を足すまでは 0 |

流したプランは 5 つの理由をすべて持ち、待たなかった理由は 0 にする（決定 6）。この queue で流さなかったプラン
（完了か承認ゲートの記録があるもの・前提のプランで止めたもの）は `waits` を持たない（I4）。
"""

from __future__ import annotations

import durable
from supervise_lib.queue import NOT_RUN

REASONS = ("overlap", "plans", "resource", "deps", "plan_limit")
WAIT_PREFIX, GROUP_PREFIX = "wait-", "group-"  # flow の wait_workflow と group_workflow の ID の頭（`flow._group_id`）
RESOURCE_PREFIX = "res-"  # 資源の枠の耐久キューの名前の頭（`flow.res_queue`）
# 耐久キューの見回りで出るまでの遅れ。DBOS は耐久キューを既定 1 秒（揺らぎ ±5%、混み合うと延ばす）ごとに見るため、
# 枠が空いていても入れてから出るまで 1 秒ほどかかる。耐久キューで待った秒（`plans`・`resource`）がこれ以下なら待たなかったとみなす
PICKUP_SECONDS = 2.0


def _span(start: float | None, end: float | None) -> float:
    """`end - start` の秒（小数 1 桁）。どちらかが無い・負（時計の誤差）なら 0。"""
    if start is None or end is None:
        return 0.0
    return round(max(0.0, end - start), 1)


def _queued(created: float, dequeued: float | None) -> float:
    """耐久キューへ入れてから出るまでの秒。見回りの遅れ（`PICKUP_SECONDS` 以下）は 0。"""
    got = _span(created, dequeued)
    return got if got > PICKUP_SECONDS else 0.0


def _one(prefix: str) -> tuple[float, float | None, str | None] | None:
    return durable.times(prefix).get(prefix)


def plan_waits(pid: str, via: str | None) -> dict | None:
    """プランの耐久ワークフロー `pid` の待ちの秒。`via` は入れた先（直なら None・組の ID・待ちの ID）。

    プランの耐久ワークフローの記録が無ければ（流さなかった）None。"""
    rows = durable.times(pid)
    me = rows.get(pid)
    if me is None:
        return None
    got = dict.fromkeys(REASONS, 0.0)
    got["resource"] = round(
        sum(_queued(c, d) for wid, (c, d, q) in rows.items() if wid.startswith(f"{pid}-t") and (q or "").startswith(RESOURCE_PREFIX)), 1
    )
    if via is None:
        got["plans"] = _queued(me[0], me[1])
        return got
    wait = _one(via) if via.startswith(WAIT_PREFIX) else None
    group = _one(GROUP_PREFIX + via[len(WAIT_PREFIX) :] if wait is not None else via)
    if group is not None:
        got["plans"] = _queued(group[0], group[1])
        got["overlap"] = _span(group[1], me[0])
    if wait is not None and group is not None:
        got["deps"] = _span(wait[0], group[0])
    return got


@durable.step(name="ndf.supervise.queue_waits")
def waits_step(entries: dict[str, list]) -> dict[str, dict]:
    """プラン → [プランの耐久ワークフローの ID, 入れた先の ID か None] から、プラン → 待ちの秒を返す。"""
    out = {}
    for plan, (pid, via) in entries.items():
        got = plan_waits(pid, via)
        if got is not None:
            out[plan] = got
    return out


def attach(items: list[dict], refs: dict, waits: dict[str, tuple[str, int | None]]) -> list[dict]:
    """`flow.run_stage` の items のうち、この queue で流したプランへ `waits` を足して返す。

    `waits` はプラン → (入れた耐久ワークフローの ID, 組の中の員の位置か None)（`flow._submit_groups` の答え）。"""
    got = waits_step({p: [refs[p]["id"], wid if k is not None else None] for p, (wid, k) in waits.items()})
    for item in items:
        if item["plan"] in got and item.get("result") != NOT_RUN:
            item["waits"] = got[item["plan"]]
    return items
