"""計画を進める耐久ワークフロー（#3097）: 計画とレーンの進行表を読み、止まっているレーンを起こし、計画を終える。

- オーガナイザーの `ndf lanes start` が `ndf lanes run <計画>` を切り離して起こし、計画が終わると抜ける。
  常駐を新しく置かない。同じ計画の `run` は実行の鍵のロックで 1 本に限る
- 1 周ごと（`parallel-run.poll`。`NDF_PARALLEL_POLL` で秒を変えられる）に、計画とレーンの進行表を読む先にする。
  計画の台帳と `plan.json` の状態は読まない
- 判断は `run_decide.decide`（純粋な関数）が持ち、読み・起動・完了はそれぞれ耐久ステップで打つ。落ちた後の回復で
  済んだ起動を打ち直さない
- レーンを起こす前に、同じ名前の元のラッパー（前の行を終えた conductor など）を止めてから起こす
- 答えが載った後に conductor が next を打っていない実行中の行（3346-5）は、そのレーンの conductor を答えの後の
  `ndf progress next` を打つ入力で起こす。契機は答えの事象（`row_answered`）がレーンの進行表に載った後の最初の
  読み直しで、猶予（止まりの知らせ）を待たない。同じ答えでは 1 回だけ起こす
- 止めた計画（`ndf lanes stop`）では、実行中の行が残るレーンをスプリントの中止の入力で起こし、実行中の行が
  無くなれば `stopped` で終わる。中止をすべて起こせなくなったら `cancel_failed` で終わる（終わった計画の進行表へは
  依頼を書けない）。`cancel_failed` で終えた耐久ワークフローの ID は再び使わず、`ndf lanes run` を打ち直すと
  次の番号の ID で新しく始めて中止を試し直す
- 区切りで止めた計画（`ndf lanes stop` の既定）では次の行を起こさず、実行中の行が無くなれば `paused` で終わる。
  `ndf lanes start` で再開すると、次の番号の ID で新しく始めて止めた所から続ける
- 起こせなかったレーン（回数が尽きた・開発ループの行・計画と別のリポジトリのレーン）は、計画の進行表へ依頼
  （`request_opened`。中身の `kind` は `launch_failed`）を書いて `ndf lanes wait` へ知らせる。オーガナイザーが
  依頼を閉じたら、打ち直せば起こせる失敗のレーンを起こし直す。起こしたのに期限（`parallel-run.launch` の秒）までに
  行が実行中にならないレーンも、起動の失敗の 1 回として数える
- 依頼は同じトランザクションで計画の状態を読み直してから書く。起動の最中に計画が止められたら書かない
- レーンは計画のリポジトリ（打った場所）で起こす。別のリポジトリを宣言したレーン（`repo`。同じリポジトリかの
  判定は `PlanView.lane_repo`）の置き場を解決する経路はまだ無いので、打った場所で流さずに起こせない失敗にする
"""

from __future__ import annotations

import contextlib
import functools
import math
import os
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ndf import identity
from ndf.contracts import limits
from ndf.core import durable, loop
from ndf.progress import lane, model, store
from ndf.progress import plan as table
from ndf.progress.lane import launch
from ndf.progress.plan import run_decide
from ndf.progress.plan.run_decide import Complete, Done, Launch, Observe, Policy, Report, RunState, Sleep

# 実行の鍵の接頭辞（計画の名前を後ろに付ける）と、耐久ワークフローの ID の接尾辞
RUN_PREFIX = "lanes-"
WORKFLOW_SUFFIX = "-lanes"
# 読み直しの待ちを早めに起こす話題（送り手はまだ無く、期限で起きる）
TOPIC_WAKE = "wake"
POLL_ENV = "NDF_PARALLEL_POLL"
# 起こせなかったレーンの知らせ（計画の進行表の依頼の中身の kind）と、依頼を書く主体
LAUNCH_FAILED = "launch_failed"
REPORT_ACTOR = model.WORKFLOW_ACTOR


@dataclass(frozen=True)
class Where:
    """計画の置き場: チェックアウトの状態の置き場・進行表の DB の URL・リポジトリの識別・打った場所。"""

    root: Path
    url: str
    repo: str
    cwd: Path


def run_key(plan: str) -> str:
    return f"{RUN_PREFIX}{plan}"


def workflow_id(plan: str, attempt: int = 1) -> str:
    """計画の耐久ワークフローの ID。2 本目以降（前の 1 本が `cancel_failed` で終わった後）は番号を付ける。"""
    base = f"{durable.run_name(run_key(plan))}{WORKFLOW_SUFFIX}"
    return base if attempt == 1 else f"{base}-{attempt}"


def policy() -> Policy:
    """読み直しの間隔と起動の回数。間隔は環境変数があればその秒にする。"""
    given = os.environ.get(POLL_ENV, "")
    poll = float(given) if given else limits.value("parallel-run.poll", "interval")
    return Policy(
        poll=poll,
        attempts=int(limits.value("parallel-run.launch", "attempts")),
        up=limits.seconds("parallel-run.launch"),
    )


# --- 耐久ステップ -------------------------------------------------------------------------------


@contextlib.contextmanager
def _conn(spec: dict[str, Any]) -> Iterator[Any]:
    """耐久ステップが進行表を読み書きする接続（置き場を開き、トランザクションを張る）。"""
    with store.open_store(spec["url"], Path(spec["root"])) as st, store.transaction(st) as conn:
        yield conn


def read_snapshot(conn: Any, plan_id: str) -> dict[str, Any]:
    """計画とレーンの進行表から、判断へ渡す写しを作る。"""
    view, rows = lane.read_view(conn, plan_id)
    live = [(name, r) for name in view.lanes for r in rows[name] if r.state == lane.RUNNING and r.sprint_id]
    running = [(name, r.key, r.sprint_id) for name, r in live]
    answered = [(name, r.key, r.sprint_id, r.answered_seq) for name, r in live if r.awaiting_next]
    reported = [r.body["launch"] for r in view.open_requests() if r.body.get("kind") == LAUNCH_FAILED]
    wakeable = lane.wakeable_lanes(view, rows)
    return run_decide.snapshot(view.state, lane.plan_complete(view, rows), wakeable, running, reported, answered)


@durable.step
def observe(spec: dict[str, Any]) -> dict[str, Any]:
    """計画とレーンの進行表を読む耐久ステップ。"""
    with _conn(spec) as conn:
        return read_snapshot(conn, spec["plan_id"])


def first_input(conn: Any, plan_id: str, act: Launch) -> str:
    """起こすレーンへ渡す最初の入力。計画のリポジトリと別のリポジトリのレーンや、起こす入力を組めない
    行なら `launch.Unsupported`。"""
    view, _ = lane.read_plan(conn, plan_id)
    declared = view.lane_repo(act.lane)
    if declared != view.repo:
        raise launch.Unsupported(f"レーン {act.lane} はリポジトリ {declared} を宣言しているが、置き場を解決できない（計画は {view.repo}）")
    if act.cancel is not None:
        return launch.prompt(view.name, act.lane, launch.cancel_commands(act.cancel))
    if act.resume is not None:
        return launch.prompt(view.name, act.lane, launch.resume_commands(act.resume))
    rows = lane.fold_lane(store.read_events(conn, view.lanes[act.lane].table_id))
    row = next(r for r in rows if r.key == act.row)
    return launch.prompt(view.name, act.lane, launch.start_commands(view.name, act.lane, view.lanes[act.lane].table_id, row))


@durable.step
def launch_lane(spec: dict[str, Any], lane_name: str, row: str, cancel: str | None, resume: str | None = None) -> dict[str, Any]:
    """レーンの conductor を起こす耐久ステップ。済んだ起動は回復のときに打ち直さない。"""
    act = Launch(lane_name, row, cancel, resume)
    try:
        with _conn(spec) as conn:
            first = first_input(conn, spec["plan_id"], act)
    except launch.Unsupported as exc:
        return {"ok": False, "final": True, "detail": str(exc)}
    # 前の行を終えた conductor のラッパーも同じ名前で残るので、行を始めるときも中止のときも先に止める
    stopped = launch.stop(spec["repo"], spec["plan"], lane_name)
    if not stopped["ok"]:
        return {"ok": False, "detail": f"元のラッパーを止められない: {stopped['detail']}"}
    return launch.spawn(spec["repo"], spec["plan"], lane_name, Path(spec["cwd"]), first)


def write_report(conn: Any, plan_id: str, lane_name: str, key: str, detail: str) -> str | None:
    """起こせなかったレーンを計画の進行表の依頼として書き、開いた依頼の鍵を返す。

    判断が使った写しは起動の前のもので、その間に計画が止められていることがある。同じトランザクションで計画の状態を
    読み直し、動いていなければ（終わった計画の進行表へは事象を書けない）書かずに None を返す。
    """
    view, _ = lane.read_plan(conn, plan_id)
    if view.state != lane.RUNNING:
        return None
    body = {"lane": lane_name, "kind": LAUNCH_FAILED, "launch": key, "detail": detail}
    return table.open_request(conn, plan_id, body, f"レーン {lane_name} を起こせない", REPORT_ACTOR)


@durable.step
def report(spec: dict[str, Any], lane_name: str, key: str, detail: str) -> str | None:
    """起こせなかったレーンを計画の進行表の依頼として書く耐久ステップ（`write_report`）。"""
    with _conn(spec) as conn:
        return write_report(conn, spec["plan_id"], lane_name, key, detail)


@durable.step
def complete(spec: dict[str, Any]) -> bool:
    """計画を完了にする耐久ステップ（書けたら真）。"""
    with _conn(spec) as conn:
        return table.complete_plan(conn, spec["plan_id"])


def _perform(spec: dict[str, Any], act: run_decide.Act) -> Done:
    if isinstance(act, Observe):
        return Done(act, observe(spec))
    if isinstance(act, Launch):
        return Done(act, launch_lane(spec, act.lane, act.row, act.cancel, act.resume))
    if isinstance(act, Complete):
        return Done(act, complete(spec))
    if isinstance(act, Report):
        return Done(act, report(spec, act.lane, act.key, act.detail))
    if isinstance(act, Sleep):
        durable.recv(TOPIC_WAKE, act.seconds)
        return Done(act)
    raise TypeError(f"知らない手: {act!r}")


@durable.workflow
def lanes_workflow(spec: dict[str, Any]) -> dict[str, Any]:
    """計画が終わるまで、判断のループで読み直し・起動・完了を回す。"""
    pol = Policy(poll=float(spec["poll"]), attempts=int(spec["attempts"]), up=float(spec.get("up") or math.inf))
    final = loop.run(functools.partial(run_decide.decide, pol), functools.partial(_perform, spec), RunState())
    return final.as_result()


# --- 起動 ---------------------------------------------------------------------------------------


def spec_of(where: Where, plan_id: str, plan: str, pol: Policy) -> dict[str, Any]:
    return {
        "url": where.url,
        "root": str(where.root),
        "plan_id": plan_id,
        "plan": plan,
        "repo": where.repo,
        "cwd": str(where.cwd),
        "poll": pol.poll,
        "attempts": pol.attempts,
        # 耐久の記録へ書くので、期限の無い（無限の）秒は None にする
        "up": pol.up if math.isfinite(pol.up) else None,
    }


def advance(where: Where, plan: str, pol: Policy) -> dict[str, Any]:
    """計画の耐久ワークフローを始めるか（途中なら回復して）続け、終わるまで流して結果を返す。"""
    plan_id = table.plan_id(where.repo, plan)
    with store.open_store(where.url, where.root) as st, store.transaction(st) as conn:
        view, _ = lane.read_plan(conn, plan_id)
    if view.state == run_decide.COMPLETED:
        return RunState(outcome=run_decide.OUTCOME_COMPLETED).as_result()
    with durable.session(where.root, run_key(plan)):
        attempt = 1
        while True:
            wid = workflow_id(plan, attempt)
            if durable.status(wid) is None:
                return durable.start(lanes_workflow, wid, spec_of(where, plan_id, plan, pol))
            done = durable.result(wid)
            # 中止を起こせずに終えた 1 本と区切りで止めて終えた 1 本は記録した結果を返すだけになるので、打ち直したら
            # 次の ID で中止を試し直す・再開した計画を続ける
            if done["outcome"] not in run_decide.OUTCOMES_RESTARTABLE:
                return done
            attempt += 1


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
