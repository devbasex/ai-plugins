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
- 実行中の行を持つレーンの生死を読み直しのたびに判定し（`ndf.progress.lane.liveness`。材料はラッパーの一覧と
  スプリントの待ち）、止まり（`stalled`・`ended`）を計画の進行表の依頼（中身の `kind` は `lane_stalled`）で知らせる。
  承認ゲートや問いの答えを待つ行は正常な待ちで知らせない。同じレーン・行・判定では 1 回だけ知らせる。知らせには
  最後に動いた時刻（レーンの行とスプリントの進行表の最後の事象の時刻）と起動の試みの回数・最後の結果を載せ、
  レーンが動き出したら依頼を閉じる（#2585）
- 最後の行が終わったレーンのラッパーがまだ動いていれば止める（#3173）。止められなければ依頼（中身の `kind` は
  `halt_failed`）で知らせる
- 行を始める起動は、マシン全体のプランの本数の上限（`ndf.progress.lane.machine`。#2999）に収まるときだけ打つ。
  マシンの起動の排他の中で、同じマシンで動いているレーンのプランの本数を数えてから起こし、収まらなければその読み直しでは
  起こさずに待たせる（起動の失敗に数えない）
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
import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ndf.contracts import facts, limits
from ndf.core import durable, loop
from ndf.progress import command, lane, model, store
from ndf.progress import plan as table
from ndf.progress.lane import launch, liveness, machine
from ndf.progress.plan import run_decide
from ndf.progress.plan.run_decide import Complete, Done, Launch, Observe, Policy, Report, RunState, Sleep, Withdraw

# 実行の鍵の接頭辞（計画の名前を後ろに付ける）と、耐久ワークフローの ID の接尾辞
RUN_PREFIX = "lanes-"
WORKFLOW_SUFFIX = "-lanes"
# 読み直しの待ちを早めに起こす話題（送り手はまだ無く、期限で起きる）
TOPIC_WAKE = "wake"
POLL_ENV = "NDF_PARALLEL_POLL"
# 起こせなかったレーン・止まったレーンの知らせ（計画の進行表の依頼の中身の kind）と、依頼を書く主体
LAUNCH_FAILED, LANE_STALLED, HALT_FAILED = run_decide.LAUNCH_FAILED, run_decide.LANE_STALLED, run_decide.HALT_FAILED
# 依頼の中身で、知らせの鍵を持つ項目（知らせの kind → 項目）と、依頼の理由
NOTICE_FIELDS = {LAUNCH_FAILED: "launch", LANE_STALLED: "stall", HALT_FAILED: "halt"}
NOTICE_REASONS = {
    LAUNCH_FAILED: "レーン {} を起こせない",
    LANE_STALLED: "レーン {} が止まった",
    HALT_FAILED: "終わったレーン {} のラッパーを止められない",
}
REPORT_ACTOR = model.WORKFLOW_ACTOR
# 切り離した `ndf lanes run` の出力を足していく、計画の記録の置き場の中のファイル
RUN_LOG = "run.log"


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


def probes(repo: str, plan: str) -> liveness.Probes:
    """生死の判定の材料を、自分の副命令（`ndf relay ls`・`ndf progress wait --timeout 0`）で読む口。

    ラッパーの一覧は 1 回の読み直しの中で 1 度だけ読む（生死の判定と終わったレーンのラッパーの判定が同じ一覧を使う）。
    """
    return liveness.Probes(
        wrapper_name=functools.partial(launch.wrapper_name, repo, plan),
        wrappers=functools.cache(lambda: launch.wrappers()),
        sprint=launch.sprint_wait,
        account_ok=lambda: launch.account_ok(),
    )


@dataclass(frozen=True)
class Judged:
    """読み直し 1 回の生死の判定。`stalls` は止まったレーンの（レーン, 知らせの鍵, 理由, 最後に動いた時刻, 行）、
    `unknown` は判定が unknown のレーン、`judged` は判定したか（計画が動いていない・材料の口が無ければ偽）、
    `relaunch` は `/goal` を入れ直して起こし直せる（アカウントが使える）`stalled` のレーンの（レーン, 行, スプリントの ID）。"""

    stalls: list[tuple[str, str, str, str | None, str]]
    unknown: list[str]
    judged: bool
    relaunch: list[tuple[str, str, str]]


def _judge(rows: dict[str, list[lane.LaneRow]], state: str, found: liveness.Probes | None, moved: dict[str, str]) -> Judged:
    """生死を判定する。計画が動いていなければ判定しない（知らせを書けない・閉じられない）。"""
    if found is None or state != lane.RUNNING:
        return Judged([], [], False, [])
    verdicts = liveness.judge_lanes(rows, found)
    stalls = [
        (name, liveness.notice_key(name, v), f"レーン {name} の行 {v.row}: {v.reason}", moved.get(name), str(v.row))
        for name, v in verdicts.items()
        if v.stall
    ]
    auth = [(name, str(v.row), str(v.sprint_id)) for name, v in verdicts.items() if v.kind == liveness.STALLED]
    relaunch = auth if auth and found.account_ok() else []
    return Judged(stalls, [name for name, v in verdicts.items() if v.kind == liveness.UNKNOWN], True, relaunch)


def last_moved(conn: Any, view: lane.PlanView, rows: dict[str, list[lane.LaneRow]]) -> dict[str, str]:
    """実行中の行を持つレーンの、最後に動いた時刻（その行の事象とスプリントの進行表の事象の最後の時刻。#2585）。"""
    out = {}
    for name in view.lanes:
        row = liveness.running_row(rows[name])
        if row is None or row.sprint_id is None:
            continue
        lane_at = [e.at for e in store.read_events(conn, view.lanes[name].table_id) if e.draft.row_key == row.key]
        times = lane_at + [e.at for e in store.read_events(conn, row.sprint_id)]
        if times:
            out[name] = max(times).isoformat()
    return out


def _halts(view: lane.PlanView, rows: dict[str, list[lane.LaneRow]], found: liveness.Probes | None) -> list[tuple[str, str]]:
    """最後の行が終わった（未着手と実行中の行が無い）のに、ラッパーが動いているレーンの（レーン, 最後の行）（#3173）。

    ラッパーの一覧を読めなければ空（止めるかを決めない）。
    """
    ended = [(name, rows[name][-1].key) for name in view.lanes if rows[name] and not any(r.live for r in rows[name])]
    table = found.wrappers() if found is not None and ended else None
    if table is None:
        return []
    return [(name, row) for name, row in ended if liveness.wrapper_running(table.get(found.wrapper_name(name)))]


def read_snapshot(conn: Any, plan_id: str, found: liveness.Probes | None = None) -> dict[str, Any]:
    """計画とレーンの進行表から、判断へ渡す写しを作る。`found` があればレーンの生死を判定して止まりを載せる。"""
    view, rows = lane.read_view(conn, plan_id)
    return snapshot_of(view, rows, found, last_moved(conn, view, rows))


def snapshot_of(
    view: lane.PlanView,
    rows: dict[str, list[lane.LaneRow]],
    found: liveness.Probes | None = None,
    moved: dict[str, str] | None = None,
) -> dict[str, Any]:
    """読んだ計画とレーンの進行表から写しを作る。`moved` はレーンの最後に動いた時刻（`last_moved`）。"""
    live = [(name, r) for name in view.lanes for r in rows[name] if r.active]
    running = [(name, r.key, r.sprint_id) for name, r in live]
    answered = [(name, r.key, r.sprint_id, r.answered_seq) for name, r in live if r.awaiting_next]
    reported = [r.body[NOTICE_FIELDS[r.body["kind"]]] for r in view.open_requests() if r.body.get("kind") in NOTICE_FIELDS]
    stall_requests = [(r.key, r.lane, r.body["stall"]) for r in view.open_requests() if r.body.get("kind") == LANE_STALLED]
    wakeable = lane.wakeable_lanes(view, rows)
    got = _judge(rows, view.state, found, moved if moved is not None else {})
    snap = run_decide.snapshot(view.state, lane.plan_complete(view, rows), wakeable, running, reported, answered)
    snap = run_decide.with_stalls(snap, got.stalls, got.unknown, got.judged, got.relaunch)
    snap = run_decide.with_stall_requests(snap, stall_requests)
    return run_decide.with_halts(snap, _halts(view, rows, found))


@durable.step
def observe(spec: dict[str, Any]) -> dict[str, Any]:
    """計画とレーンの進行表を読む耐久ステップ。生死の材料は、進行表の接続を閉じてから読む。"""
    with _conn(spec) as conn:
        view, rows = lane.read_view(conn, spec["plan_id"])
        moved = last_moved(conn, view, rows)
    return snapshot_of(view, rows, probes(spec["repo"], spec["plan"]), moved)


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
    if act.goal is not None:
        return launch.goal_prompt(view.name, act.lane, act.goal)
    rows = lane.fold_lane(store.read_events(conn, view.lanes[act.lane].table_id))
    row = next(r for r in rows if r.key == act.row)
    return launch.prompt(view.name, act.lane, launch.start_commands(view.name, act.lane, view.lanes[act.lane].table_id, row))


@durable.step
def launch_lane(spec: dict[str, Any], lane_name: str, row: str, how: dict[str, Any]) -> dict[str, Any]:
    """レーンの conductor を起こす耐久ステップ。済んだ起動は回復のときに打ち直さない。`how` は `Launch` の
    cancel・resume・seq・halt・goal。`halt` なら起こさずに、最後の行が終わったレーンのラッパーを止める（#3173）。"""
    act = Launch(lane_name, row, **how)
    if act.halt:
        return launch.stop(spec["repo"], spec["plan"], lane_name)
    try:
        with _conn(spec) as conn:
            first = first_input(conn, spec["plan_id"], act)
            view, rows = lane.read_view(conn, spec["plan_id"])
            own = machine.lanes_of(view, rows)
            plans = view.lanes[lane_name].plans
    except launch.Unsupported as exc:
        return {"ok": False, "final": True, "detail": str(exc)}

    def go() -> dict[str, Any]:
        # 前の行を終えた conductor のラッパーも同じ名前で残るので、行を始めるときも中止のときも先に止める
        stopped = launch.stop(spec["repo"], spec["plan"], lane_name)
        if not stopped["ok"]:
            return {"ok": False, "detail": f"元のラッパーを止められない: {stopped['detail']}"}
        return launch.spawn(spec["repo"], spec["plan"], lane_name, Path(spec["cwd"]), first)

    if act.continues:
        # 動いているレーンの続き（中止・答えの後の next・/goal の入れ直し）は、上限で待たせない
        return go()
    wrapper = launch.wrapper_name(spec["repo"], spec["plan"], lane_name)
    return machine.admitted(own, wrapper, plans, lambda: launch.wrappers(), go)


def write_report(conn: Any, plan_id: str, notice: Report) -> str | None:
    """起こせなかったレーン・止まったレーン・止められないラッパー（`notice.kind`）を計画の進行表の依頼として書き、
    開いた依頼の鍵を返す。`notice.facts` は依頼の中身へ足す。

    判断が使った写しは起動の前のもので、その間に計画が止められていることがある。同じトランザクションで計画の状態を
    読み直し、動いていなければ（終わった計画の進行表へは事象を書けない）書かずに None を返す。
    """
    view, _ = lane.read_plan(conn, plan_id)
    if view.state != lane.RUNNING:
        return None
    facts = {str(k): v for k, v in notice.facts}
    body = {**facts, "lane": notice.lane, "kind": notice.kind, NOTICE_FIELDS[notice.kind]: notice.key, "detail": notice.detail}
    return table.open_request(conn, plan_id, body, NOTICE_REASONS[notice.kind].format(notice.lane), REPORT_ACTOR)


@durable.step
def report(spec: dict[str, Any], notice: dict[str, Any]) -> str | None:
    """レーンの知らせ（`Report` の項目の辞書）を計画の進行表の依頼として書く耐久ステップ（`write_report`）。"""
    got = Report(**{**notice, "facts": tuple(tuple(f) for f in notice["facts"])})
    with _conn(spec) as conn:
        return write_report(conn, spec["plan_id"], got)


def withdraw_report(conn: Any, plan_id: str, request: str, lane_name: str) -> bool | None:
    """止まりの知らせの依頼を、レーンが動き出したので閉じる（#2585）。計画が動いていない・依頼が既に閉じていれば
    書かずに None を返す。"""
    view, _ = lane.read_plan(conn, plan_id)
    found = view.requests.get(request)
    if view.state != lane.RUNNING or found is None or found.closed is not None:
        return None
    table.close_request(conn, plan_id, request, f"レーン {lane_name} が動き出した", REPORT_ACTOR)
    return True


@durable.step
def withdraw(spec: dict[str, Any], request: str, lane_name: str) -> bool | None:
    """止まりの知らせを閉じる耐久ステップ（`withdraw_report`）。"""
    with _conn(spec) as conn:
        return withdraw_report(conn, spec["plan_id"], request, lane_name)


@durable.step
def complete(spec: dict[str, Any]) -> bool:
    """計画を完了にする耐久ステップ（書けたら真）。"""
    with _conn(spec) as conn:
        return table.complete_plan(conn, spec["plan_id"])


def _perform(spec: dict[str, Any], act: run_decide.Act) -> Done:
    if isinstance(act, Observe):
        return Done(act, observe(spec))
    if isinstance(act, Launch):
        how = {k: v for k, v in dataclasses.asdict(act).items() if k not in ("lane", "row")}
        return Done(act, launch_lane(spec, act.lane, act.row, how))
    if isinstance(act, Complete):
        return Done(act, complete(spec))
    if isinstance(act, Report):
        notice = {**dataclasses.asdict(act), "facts": [list(f) for f in act.facts]}
        return Done(act, report(spec, notice))
    if isinstance(act, Withdraw):
        return Done(act, withdraw(spec, act.request, act.lane))
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


def record_dir(cwd: Path, plan: str) -> Path:
    """計画の記録の置き場（`places` の `ndf-parallel` の下の、`ndf-files` の `parallel` の `<id>` を計画の名前にしたもの）。"""
    base = command.declaration_path(cwd, facts.value("places", "ndf-parallel", "path"))
    return base / facts.value("ndf-files", "parallel", "path").replace("<id>", plan)


def detach(plan: str, cwd: Path) -> int:
    """`ndf lanes run <計画>` を切り離して起こし、pid を返す。結果は進行表と耐久の記録に残る。

    切り離した実行の標準出力と標準エラーは、計画の記録の置き場の `RUN_LOG` へ足していく（落ちた理由を後から読める）。
    """
    where = record_dir(cwd, plan)
    where.mkdir(parents=True, exist_ok=True)
    with (where / RUN_LOG).open("ab") as log:
        proc = subprocess.Popen(
            [str(launch.entry()), "lanes", "run", plan],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=str(cwd),
            env=dict(os.environ),
        )
    return proc.pid
