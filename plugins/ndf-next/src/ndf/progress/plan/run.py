"""計画を進める耐久ワークフロー（#3097）: 計画とレーンの進行表を読み、止まっているレーンを起こし、計画を終える。

- オーガナイザーの `ndf lanes start` が `ndf lanes run <計画>` を切り離して起こし、計画が終わると抜ける。
  常駐を新しく置かない。同じ計画の `run` は実行の鍵のロックで 1 本に限る
- 1 周ごと（`parallel-run.poll`。`NDF_PARALLEL_POLL` で秒を変えられる）に、計画とレーンの進行表を読む先にする。
  計画の台帳と `plan.json` の状態は読まない
- 判断は `run_decide.decide`（純粋な関数）が持ち、読み・起動・完了はそれぞれ耐久ステップで打つ。落ちた後の回復で
  済んだ起動を打ち直さない
- 止めた計画（`ndf lanes stop`）では、実行中の行が残るレーンをスプリントの中止の入力で起こし、実行中の行が
  無くなれば `stopped` で終わる
"""

from __future__ import annotations

import functools
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ndf import identity
from ndf.contracts import limits
from ndf.core import durable, loop
from ndf.progress import lane, store
from ndf.progress import plan as table
from ndf.progress.lane import launch
from ndf.progress.plan import run_decide
from ndf.progress.plan.run_decide import Complete, Done, Launch, Observe, Policy, RunState, Sleep

# 実行の鍵の接頭辞（計画の名前を後ろに付ける）と、耐久ワークフローの ID の接尾辞
RUN_PREFIX = "lanes-"
WORKFLOW_SUFFIX = "-lanes"
# 読み直しの待ちを早めに起こす話題（送り手はまだ無く、期限で起きる）
TOPIC_WAKE = "wake"
POLL_ENV = "NDF_PARALLEL_POLL"


@dataclass(frozen=True)
class Where:
    """計画の置き場: チェックアウトの状態の置き場・進行表の DB の URL・リポジトリの識別・打った場所。"""

    root: Path
    url: str
    repo: str
    cwd: Path


def run_key(plan: str) -> str:
    return f"{RUN_PREFIX}{plan}"


def workflow_id(plan: str) -> str:
    return f"{durable.run_name(run_key(plan))}{WORKFLOW_SUFFIX}"


def policy() -> Policy:
    """読み直しの間隔と起動の回数。間隔は環境変数があればその秒にする。"""
    given = os.environ.get(POLL_ENV, "")
    poll = float(given) if given else limits.value("parallel-run.poll", "interval")
    return Policy(poll=poll, attempts=int(limits.value("parallel-run.launch", "attempts")))


# --- 耐久ステップ -------------------------------------------------------------------------------


def _spec_where(spec: dict[str, Any]) -> tuple[str, Path]:
    return spec["url"], Path(spec["root"])


def read_snapshot(conn: Any, plan_id: str) -> dict[str, Any]:
    """計画とレーンの進行表から、判断へ渡す写しを作る。"""
    view, rows = lane.read_view(conn, plan_id)
    running = [(name, r.key, r.sprint_id) for name in view.lanes for r in rows[name] if r.state == lane.RUNNING and r.sprint_id]
    return run_decide.snapshot(view.state, lane.plan_complete(view, rows), lane.wakeable_lanes(view, rows), running)


@durable.step
def observe(spec: dict[str, Any]) -> dict[str, Any]:
    """計画とレーンの進行表を読む耐久ステップ。"""
    url, root = _spec_where(spec)
    with store.open_store(url, root) as st, store.transaction(st) as conn:
        return read_snapshot(conn, spec["plan_id"])


def first_input(conn: Any, plan_id: str, act: Launch) -> str:
    """起こすレーンへ渡す最初の入力。"""
    view, _ = lane.read_plan(conn, plan_id)
    if act.cancel is not None:
        return launch.prompt(view.name, act.lane, launch.cancel_commands(act.cancel))
    rows = lane.fold_lane(store.read_events(conn, view.lanes[act.lane].table_id))
    row = next(r for r in rows if r.key == act.row)
    return launch.prompt(view.name, act.lane, launch.start_commands(view.name, act.lane, view.lanes[act.lane].table_id, row))


@durable.step
def launch_lane(spec: dict[str, Any], lane_name: str, row: str, cancel: str | None) -> dict[str, Any]:
    """レーンの conductor を起こす耐久ステップ。済んだ起動は回復のときに打ち直さない。"""
    url, root = _spec_where(spec)
    act = Launch(lane_name, row, cancel)
    with store.open_store(url, root) as st, store.transaction(st) as conn:
        first = first_input(conn, spec["plan_id"], act)
    return launch.spawn(spec["plan"], lane_name, Path(spec["cwd"]), first)


@durable.step
def complete(spec: dict[str, Any]) -> bool:
    """計画を完了にする耐久ステップ（書けたら真）。"""
    url, root = _spec_where(spec)
    with store.open_store(url, root) as st, store.transaction(st) as conn:
        return table.complete_plan(conn, spec["plan_id"])


def _perform(spec: dict[str, Any], act: run_decide.Act) -> Done:
    if isinstance(act, Observe):
        return Done(act, observe(spec))
    if isinstance(act, Launch):
        return Done(act, launch_lane(spec, act.lane, act.row, act.cancel))
    if isinstance(act, Complete):
        return Done(act, complete(spec))
    if isinstance(act, Sleep):
        durable.recv(TOPIC_WAKE, act.seconds)
        return Done(act)
    raise TypeError(f"知らない手: {act!r}")


@durable.workflow
def lanes_workflow(spec: dict[str, Any]) -> dict[str, Any]:
    """計画が終わるまで、判断のループで読み直し・起動・完了を回す。"""
    pol = Policy(poll=float(spec["poll"]), attempts=int(spec["attempts"]))
    final = loop.run(functools.partial(run_decide.decide, pol), functools.partial(_perform, spec), RunState())
    return final.as_result()


# --- 起動 ---------------------------------------------------------------------------------------


def spec_of(where: Where, plan_id: str, plan: str, pol: Policy) -> dict[str, Any]:
    return {
        "url": where.url,
        "root": str(where.root),
        "plan_id": plan_id,
        "plan": plan,
        "cwd": str(where.cwd),
        "poll": pol.poll,
        "attempts": pol.attempts,
    }


def advance(where: Where, plan: str, pol: Policy) -> dict[str, Any]:
    """計画の耐久ワークフローを始めるか（途中なら回復して）続け、終わるまで流して結果を返す。"""
    plan_id = table.plan_id(where.repo, plan)
    with store.open_store(where.url, where.root) as st, store.transaction(st) as conn:
        view, _ = lane.read_plan(conn, plan_id)
    if view.state == run_decide.COMPLETED:
        return {"outcome": run_decide.OUTCOME_COMPLETED, "launched": [], "failed": [], "errors": []}
    wid = workflow_id(plan)
    with durable.session(where.root, run_key(plan)):
        if durable.status(wid) is None:
            return durable.start(lanes_workflow, wid, spec_of(where, plan_id, plan, pol))
        return durable.result(wid)


def detach(plan: str, cwd: Path) -> int:
    """`ndf lanes run <計画>` を切り離して起こし、pid を返す。結果は進行表と耐久の記録に残る。"""
    proc = subprocess.Popen(
        [str(identity.entrypoint(identity.ROOT)), "lanes", "run", plan],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(cwd),
        env=dict(os.environ),
    )
    return proc.pid
