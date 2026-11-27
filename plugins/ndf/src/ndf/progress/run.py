"""ステートマシンを読んで工程を流す（`ndf progress next`・`wait`・`answer`・`resync` と、`next` が起動する `work`）。

ステートマシンの状態の SSOT は事象で、次に何をするかは事象を畳み込んだ表だけから決める（`decide`。受け入れ条件 4）。
再開に `--from` を組み立てる必要は無い。終わったステップ（事象 `step_recorded`）は流し直さない。

- `next` は排他（`store.run_lock`）を取って次の 1 手を決め、工程を流す作業（`work`）を別のプロセスで起動してすぐ返る。
  長く動く工程を前景で抱えない。待ちは `wait <ステートマシン>` の 1 本で行い、上限で戻れば同じ `wait` を打ち直す（3366-1）
- `work` は排他を持ったまま工程のステップを順に呼ぶ（`invoke`）。打ち直しで直る止まり（停止理由の表の
  ルートが `retry`）はここで方針どおりに打ち直し、conductor へ返すのは人か conductor の判断が要る止まりだけで、
  `routes` を持つ（SF8）。終了コードは停止理由の種類から引く
- 承認ゲート（`ndf.listen`）とプランの中の承認ゲートは、行を実行中のまま `work` を抜け、`next` が `gate` と `routes` を返す。
  回答は `answer`（と `ndf answer`。`ndf.progress.gate`）が事象 `answered` で残す。変更要求には指摘ファイルが要る（3365-4）。
  直前の MVV 判定が省けると決めた承認ゲートは、回答した者 `service:mvv` の承認を書いて人へ質問せずに通す（#3745）
- スプリントの完了と同じトランザクションで `Env.ended` を打つ（レーンの行を終える口。決定 17。`ndf.progress.gate.lane_env`）
- 工程の出力は工程の出力の規約で照らしてから終了のマーカーを付ける。合わなければマーカーを付けず失敗にする（受け入れ条件 8）
- 課題の工程表とスプリント PR の「通した工程」は終了のマーカーから作る（受け入れ条件 6）。書けなくても工程は進め、`resync` で揃え直せる（3377-9）
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Optional

from ndf import process_group
from ndf.contracts import causes, facts, limits, result
from ndf.contracts.worker_kinds import Recorded
from ndf.core import durable
from ndf.core.plan import cli as plan_cli
from ndf.progress import command, definition, invoke, issue_values, model, stages, store
from ndf.progress.folding import REQUEST_CHANGES, StepProgress, answer_word, step_progress
from ndf.progress.records import Records, github_records, sync_records
from ndf.progress.run_view import (
    ACTOR,
    ANSWERED,
    CONDUCTOR,
    FAILED,
    FINISHED,
    ORGANIZER,
    STARTED,
    STEP_EVENT,
    StageAnswers,
    View,
    migrated_order,
    migration_drafts,
    event_payload,
    read_accepted_head,
    released_answer,
)

TOOL = __package__.rpartition(".")[2]


# 回答の語（承認・変更要求・承認しない）
APPROVED = invoke.APPROVED
REJECTED = answer_word("rejected")
# 外から待つ問い合わせの間隔と、作業が排他を取るまでの待ちの間隔（どちらも設定の値）
WAIT_POLL = plan_cli.WAIT_POLL
LOCK_POLL = durable.POLL
# 作業の保存先（チェックアウトの保存先の下）と、起動した作業が排他を取るまで置く起動のマーカー（作業の保存先の下）
WORK_DIR = "progress-work"
LAUNCH_FILE = "launched.json"


# --- ステートマシンの読み書き ---------------------------------------------------------------------


def view(conn: Any, machine_id: str) -> View:
    rev, raw, snap, events = command.read_current(conn, machine_id)
    return View(machine_id, rev, migrated_order(snap, events), raw, events)


def migrate_definition(st: store.Store, machine_id: str) -> Optional[int]:
    """このリリースの前に始めた状態機械の写しで、クリーンアップをデプロイの直後へ移す事象を 1 回だけ書き、その番号を返す
    （#4409 の決定 6。下書きは `run_view.migration_drafts`）。クリーンアップが開いた PR で止まった（`precondition` の失敗）
    状態機械も移す。移さなければ None。"""
    seqs = append(st, machine_id, migration_drafts)
    return seqs[0] if seqs else None


def read(st: store.Store, machine_id: str) -> View:
    with store.transaction(st) as conn:
        return view(conn, machine_id)


def append(
    st: store.Store, machine_id: str, build: Callable[[View], list[model.EventDraft]], after: Callable[[Any], Any] = lambda _conn: None
) -> list[int]:
    """`build` が今のステートマシンから作った下書きを追記する（ぶつかれば作り直す。空なら書かない）。`after` は同じトランザクションで打つ。"""

    def write(conn: Any) -> list[int]:
        v = view(conn, machine_id)
        drafts = build(v)
        seqs = store.append_events(conn, machine_id, drafts, v.events[-1].seq if v.events else 0) if drafts else []
        after(conn)
        return seqs

    return command.retrying_transaction(st, write)


def _write(st: store.Store, machine_id: str, *drafts: model.EventDraft) -> list[int]:
    return append(st, machine_id, lambda _v: list(drafts))


# --- 次の 1 手（純粋な判断） ----------------------------------------------------------------


class Action(StrEnum):
    """次の 1 手の種類（値は結果 JSON の `metrics.move` に載る）。"""

    DONE = "done"
    COMPLETE = "complete"
    START = "start"
    RESUME = "resume"
    GATE = "gate"
    CONSUME = "consume"
    FAILED = "failed"


@dataclass(frozen=True)
class Move:
    """次の 1 手。"""

    action: Action
    stage: Optional[stages.StageDef] = None
    step: Optional[stages.StepDef] = None
    progress: Optional[StepProgress] = None
    event: Optional[model.Event] = None


def _last(events: Iterable[model.Event], kind: str) -> Optional[model.Event]:
    found = None
    for ev in events:
        if ev.draft.kind == kind:
            found = ev
    return found


def _void_answer(v: View, ev: Optional[model.Event], gate: Optional[str]) -> bool:
    """回答 `ev` が、回答として数えない MVV 判定の回答か（`model.mvv_answer_void`）。"""
    return ev is not None and model.mvv_answer_void(event_payload(ev), gate)


def _reopen(v: View, s: stages.StageDef, prog: StepProgress) -> None:
    """数えない MVV 判定の回答で済んだ承認ゲートのステップを、後ろのステップが済んでいなければ済んでいないものにする（#3959）。

    更新前の NDF が `service:mvv` の回答を消費して済みにした承認ゲートを、再開の後に改めて人へ質問するためである。後ろのステップ（マージなど）
    が済んでいれば、承認を取り直しても戻せないので開き直さない。記録の事象は書き換えない。
    """
    last = next((st for st in reversed(s.steps) if st.id in prog.done), None)
    if last is None or not invoke.is_listen(last):
        return
    report = prog.done[last.id]
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    by_seq = {ev.seq: ev for ev in v.row_events(s.name) if ev.draft.kind == ANSWERED}
    if _void_answer(v, by_seq.get(metrics.get("seq")), last.gate):
        del prog.done[last.id]


def _running_move(v: View, s: stages.StageDef) -> Move:
    prog = step_progress(v, s.name)
    _reopen(v, s, prog)
    step = prog.next_step(s)
    if step is None:
        return Move(Action.RESUME, s, None, prog)
    listen = invoke.is_listen(step)
    answer = None if _void_answer(v, prog.answer, step.gate) else prog.answer
    waiting = listen or (prog.waiting is not None and prog.waiting["step"] == step.id)
    held = prog.waiting is not None or step.id in prog.held
    skip = listen and not held and model.mvv_skip(prog.done, s.mvv_before(step.id), step.gate) is not None
    if waiting and answer is None and not skip:
        return Move(Action.GATE, s, step, prog)
    return Move(Action.CONSUME if listen else Action.RESUME, s, step, prog, answer)


def decide(v: View) -> Move:
    """事象を畳み込んだ表だけから次の 1 手を決める。終わった行（終了・無効・中止）は飛ばす。"""
    if model.is_terminal(v.whole):
        return Move(Action.DONE)
    rows = v.rows
    for s in v.snap.stages:
        state = rows[s.name].state if s.name in rows else None
        if model.is_terminal(state):
            continue
        if state == "failed":
            return Move(Action.FAILED, s, event=_last(v.row_events(s.name), FAILED))
        if state == "running":
            return _running_move(v, s)
        return Move(Action.START, s)
    return Move(Action.COMPLETE)


# --- 結果 JSON ------------------------------------------------------------------------------


def _cmd(*args: str, ask: tuple[str, ...] = ()) -> dict[str, Any]:
    return result.command(TOOL, *args, ask=ask)


def _listen_choices(step: stages.StepDef) -> list[str]:
    """承認ゲートの回答の語（出力の規約の `answer` の enum。`definition.answer_choices`）。"""
    return definition.answer_choices(step.output_ref)


def _route_choices(prog: StepProgress) -> list[str]:
    """プランの承認ゲートの回答の語（待っているステップの結果の routes）。"""
    waiting = prog.waiting if prog.waiting is not None else {}
    routes = waiting["report"]["routes"] if isinstance(waiting["report"].get("routes"), dict) else {}
    return list(routes)


def choices(m: Move) -> list[str]:
    """待っているステップへの回答の選択肢。承認ゲートは出力の規約の回答の語、プランの承認ゲートはプランの routes。"""
    assert m.step is not None and m.progress is not None
    known = facts.ids(result.ANSWERS)
    listed = _listen_choices(m.step) if invoke.is_listen(m.step) else _route_choices(m.progress)
    return [a for a in listed if a in known]


def _gate_routes(machine_id: str, m: Move) -> dict[str, Any]:
    assert m.stage is not None
    out = {}
    for a in choices(m):
        ask = ("notes", "reason") if a == REQUEST_CHANGES else ("reason",)
        out[a] = _cmd("answer", machine_id, m.stage.name, "--answer", a, ask=ask)
    return out


def failed_routes(machine_id: str, stage: str) -> dict[str, Any]:
    """失敗した行で conductor が選ぶ手（回答の語彙の retry・finish・cancel）。"""
    return {
        "retry": _cmd("retry", machine_id, stage, ask=("reason",)),
        "finish": _cmd("finish", machine_id, stage, ask=("output", "reason")),
        "cancel": _cmd("cancel", machine_id, ask=("reason",)),
    }


# 承認の後に承認の対象が変わった・有効な承認が無いマージの停止理由（`ndf ci merge --production-gate stale`。#4409）
APPROVAL_STALE = "approval_stale"


def stale_routes(machine_id: str, stage: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """`approval_stale` で止まった工程の経路。工程を最初から流し直す記録（routes の retry。理由は approval_stale）と、
    記録した工程を流して承認ゲートに今の承認の対象で質問し直させる `ndf progress next`（結果の next）をこの順で返す。

    `ndf ci merge` は状態機械の ID と工程を知らないので、止まったプランの結果からは写さず、ここで組む（#4409 の決定 4・5）。
    """
    routes = {
        "retry": _cmd("retry", machine_id, stage, "--reason", APPROVAL_STALE),
        "cancel": _cmd("cancel", machine_id, ask=("reason",)),
    }
    return routes, _cmd("next", machine_id)


def _metrics(v: View, m: Move) -> dict[str, Any]:
    out: dict[str, Any] = {"machine_id": v.machine_id, "rev": v.rev, "state": v.whole, "move": m.action}
    if m.stage is not None:
        out["stage"] = m.stage.name
    if m.step is not None:
        out["step"] = m.step.id
    return out


def _failed_report(v: View, m: Move) -> dict[str, Any]:
    assert m.stage is not None
    p = event_payload(m.event) if m.event is not None else {}
    cause = p["cause"] if isinstance(p.get("cause"), dict) else {}
    kind = causes.kind_of(cause)
    kind = kind if kind is not None and causes.has(kind) else "unknown"
    detail = cause["detail"] if isinstance(cause.get("detail"), str) and cause["detail"] else f"工程 {m.stage.name} が失敗した"
    summary = f"工程 {m.stage.name} が失敗した: {detail}"
    if kind == APPROVAL_STALE:
        routes, nxt = stale_routes(v.machine_id, m.stage.name)
        summary = f"{summary}。routes の retry で工程を最初から流し直す記録を書き、next で承認ゲートから質問し直す"
        return result.stopped(TOOL, kind, detail, summary=summary, metrics=_metrics(v, m), routes=routes, next=nxt)
    return result.stopped(TOOL, kind, detail, summary=summary, metrics=_metrics(v, m), routes=failed_routes(v.machine_id, m.stage.name))


def report(v: View, m: Move) -> dict[str, Any]:
    """副作用の無い 1 手の結果（`wait`・`show` と、`next` が流せない手）。"""
    if m.action == Action.DONE:
        return result.result(TOOL, result.OK, f"ステートマシン {v.machine_id} は{model.label(v.whole)}", metrics=_metrics(v, m))
    if m.action == Action.FAILED:
        return _failed_report(v, m)
    assert m.stage is not None
    if m.action == Action.GATE:
        assert m.step is not None
        summary = f"工程 {m.stage.name} のステップ {m.step.id} が回答を待っている"
        return result.result(TOOL, result.GATE, summary, metrics=_metrics(v, m), routes=_gate_routes(v.machine_id, m))
    summary = f"次に流すのは工程 {m.stage.name}（{m.action}）"
    return result.result(TOOL, result.OK, summary, metrics=_metrics(v, m), next=_cmd("next", v.machine_id))


# --- 依存（テストが差し替える） ------------------------------------------------------------


Facts = Callable[[tuple[str, ...], Mapping[str, Any], Path], Mapping[str, bool]]
Launch = Callable[[store.Store, str, Path], Optional[int]]
Invoker = Callable[[invoke.StageRun, Path, Path, Optional[str]], invoke.StageResult]
# プランの差し込みを組んで保存先へ残す（呼び出し・保存先・打った場所）。上げた例外は `invoke.plan_failure` が分ける
Prepare = Callable[[invoke.StageRun, Path, Path], object]


def launch_worker(st: store.Store, machine_id: str, cwd: Path) -> Optional[int]:
    """工程を流す作業（`ndf progress work`）を、自分の終わりに巻き込まれない別のプロセスの組で起動し、組の番号（pid）を返す。

    作業の標準出力と標準エラーは作業の保存先の `work.log` へ追記する。"""
    log = work_root(st.root, machine_id) / "work.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as out:
        argv = invoke.ndf_command(TOOL, "work", machine_id)
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
    return proc.pid


def _invoke(run: invoke.StageRun, cwd: Path, work_dir: Path, answer: Optional[str]) -> invoke.StageResult:
    return invoke.invoke(run, cwd, work_dir, answer)


@dataclass(frozen=True)
class Env:
    """`next`・`work` が使う外との接点。`facts` は工程の条件の語の値、`records` は進捗記録の保存先を作る。"""

    facts: Facts
    launch: Launch = launch_worker
    invoker: Invoker = _invoke
    prepare: Prepare = invoke.prepare_plan
    records: Callable[[Path], Records] = github_records
    sleep: Callable[[float], None] = time.sleep
    ended: Callable[[Any, str], Any] = lambda _conn, _machine_id: None  # スプリントの完了と同じトランザクションで打つ
    # スプリントが計画のレーンの行にあるか（読み手役のステップを済ませる判定。レーンは `ndf.progress.gate.lane_env` が読む）
    in_lane: Callable[[store.Store, str], bool] = lambda _st, _machine_id: False


def work_root(root: Path, machine_id: str) -> Path:
    """ステートマシンごとの作業の保存先（プランの実行の保存先と作業の記録）。名前はステートマシンの識別子の sha256 の頭。"""
    return root / WORK_DIR / hashlib.sha256(machine_id.encode()).hexdigest()[: model.REPO_KEY_LENGTH]


def resync(st: store.Store, machine_id: str, cwd: Path, env: Env) -> dict[str, Any]:
    v = read(st, machine_id)
    items, errors = sync_records(v, cwd, env.records)
    if errors:
        return result.stopped(TOOL, "transient", "; ".join(errors), items=items, next=_cmd("resync", machine_id))
    return result.result(TOOL, result.OK, f"ステートマシン {machine_id} の進捗記録を揃えた（{len(items)} 件）", items=items)


# --- 手を進める（next と work） ---------------------------------------------------------------


@dataclass
class Drive:
    """1 回の駆動。`inline` が真なら工程をこのプロセスで流し（`work`）、偽なら作業を起動して返す（`next`）。"""

    st: store.Store
    machine_id: str
    cwd: Path
    env: Env
    inline: bool
    warnings: list[str] = field(default_factory=list)

    def write(self, *drafts: model.EventDraft) -> None:
        _write(self.st, self.machine_id, *drafts)

    def synced(self) -> None:
        """行を終えた後に進捗記録を書き直す。記録の失敗は工程を止めず警告に載せる。"""
        _, errors = sync_records(read(self.st, self.machine_id), self.cwd, self.env.records)
        self.warnings.extend(errors)


def _forced(v: View, stage: str) -> bool:
    """conductor が無効から戻した行は、定義の条件を見ずに流す。"""
    last = None
    for ev in v.row_events(stage):
        if ev.draft.state is not None:
            last = ev.draft.kind
    return last == "enabled"


RETRO_STAGE_ID = "retrospective"


def _retro_enabled(v: View) -> bool:
    """振り返りの工程がステートマシンにあって終わったか。無い・無効・中止なら偽（#3092 の前提 4）。"""
    rows = v.rows
    return any(s.id == RETRO_STAGE_ID and s.name in rows and rows[s.name].state == FINISHED for s in v.snap.stages)


TABLE_FACTS: dict[str, Callable[[View], bool]] = {"retro_enabled": _retro_enabled}  # ステートマシンから求める条件の語


def _when_off(d: Drive, v: View, s: stages.StageDef) -> Optional[str]:
    """定義の条件の最初の偽の語。全て真・conductor が戻した・設定が有効にした行は None。求められなければ `command.FactUnknown`。"""
    if not s.when or _forced(v, s.name) or stages.enabled_by_project(v.doc["enablement"], s.name):
        return None
    outer = tuple(w for w in s.when if w not in TABLE_FACTS)
    values = {**d.env.facts(outer, v.context(), d.cwd), **{w: TABLE_FACTS[w](v) for w in s.when if w in TABLE_FACTS}}
    off = [w for w in s.when if not values[w]]
    return off[0] if off else None


def _start(d: Drive, v: View, s: stages.StageDef) -> Optional[dict[str, Any]]:
    """未着手の行を始める。条件が偽なら無効として飛ばし、入力が欠けるか形が違えば始めずに止まる。"""
    with command.base_refs_once():
        # 条件の判定と差し込みの組み立てが同じ起点を使う。求めるのは 1 回だけにする（#3908 の r2-5）
        return _start_once(d, v, s)


def _start_once(d: Drive, v: View, s: stages.StageDef) -> Optional[dict[str, Any]]:
    """`_start` の本体（起点を 1 回だけ求める範囲の中で打つ）。"""
    m = Move(Action.START, s)
    try:
        off = _when_off(d, v, s)
    except command.FactUnknown as exc:
        detail = f"工程 {s.name} の{exc}（流すなら disable の後に enable、外すなら disable を打つ）"
        return result.stopped(TOOL, "precondition", detail, metrics=_metrics(v, m))
    if off is not None:
        d.write(model.EventDraft("skipped", ACTOR, s.name, reason=f"条件 {off} が偽", payload={"when": off}, rev=v.rev))
        return None
    try:
        inputs = invoke.resolve_inputs(s, v.context())
    except invoke.StageInputError as exc:
        return result.stopped(TOOL, exc.kind, str(exc), items=exc.items, metrics=_metrics(v, m))
    failed = _prepare(d, v, s, inputs)
    if failed is not None:
        cause = failed["cause"]
        wait = cause["retry_after"] if "retry_after" in cause else None
        items = failed["items"] if "items" in failed else []
        detail = f"工程 {s.name} の{cause['detail']}"
        return result.stopped(TOOL, cause["kind"], detail, retry_after=wait, items=items, metrics=_metrics(v, m))
    d.write(model.EventDraft(STARTED, ACTOR, s.name, payload={"inputs": inputs}, rev=v.rev))
    return None if d.inline else _launched(d, v, m)


def _prepare(d: Drive, v: View, s: stages.StageDef, inputs: dict[str, Any]) -> Optional[dict[str, Any]]:
    """試行の始まりに、前の試行の値（`values.json` と課題のコピー）を工程のすべてのステップの保存先から消し（消せなければ
    precondition）、次に流すステップがプランならその差し込みを組んで確かめる（#3908 の決定 2・4）。組めなければ `invoke.plan_failure` の結果、組めれば None。

    次に流すステップは、出力の失敗で初めから流し直す（`redo`）なら最初のステップ、ほかは終わっていない最初のステップである。
    2 つ目以降のプランのステップは前のステップが作った値を差し込みに使うので、ここでは組まず、流すときに `plan_path` が組む。
    """
    try:
        issue_values.clear_values(step_dir(d, v, s, st) for st in s.steps)
    except OSError as exc:
        return result.stopped(invoke.PLAN, "precondition", f"前の試行の値を保存先から消せない: {exc}")
    prog = step_progress(v, s.name)
    step = s.steps[0] if prog.redo else prog.next_step(s)
    if step is None or invoke.kind_of(step) != invoke.PLAN:
        return None
    answers = StageAnswers(v, s.name)
    run = replace(invoke.stage_run(s, step, inputs), approved=approved_seen(answers, d.st.root), released=_released_reader(answers))
    if invoke.template(run.name) is None:
        return None  # テンプレートの無いプランは、流すときの _call_plan が precondition で止める
    try:
        d.env.prepare(run, step_dir(d, v, s, step), d.cwd)
    except invoke.PLAN_FAILURES as exc:
        failed = invoke.plan_failure(run.name, exc)
        if failed is None:
            raise
        return failed
    return None


def launch_path(root: Path, machine_id: str) -> Path:
    """起動のマーカー。`next` が作業を起動した後、作業が排他を取るまでの間だけ置く（中身は作業の組の番号）。"""
    return work_root(root, machine_id) / LAUNCH_FILE


def _launched(d: Drive, v: View, m: Move) -> dict[str, Any]:
    """作業を起動し、排他を持ったまま起動のマーカーを書く。作業が排他を取るまでの間も `is_live` が生きているとみなす。"""
    assert m.stage is not None
    pid = d.env.launch(d.st, d.machine_id, d.cwd)
    if pid is not None:
        mark = launch_path(d.st.root, d.machine_id)
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text(json.dumps({"pid": pid}), encoding="utf-8")
    summary = f"工程 {m.stage.name} から流す作業を起動した。承認ゲート・失敗・完了までは wait で待つ"
    return result.result(TOOL, result.OK, summary, metrics=_metrics(v, m), next=_cmd("wait", d.machine_id))


def gate_report(step: stages.StepDef, answer: model.Event) -> dict[str, Any]:
    """承認ゲートのステップの出力（回答を持つ結果 JSON）。"""
    p = event_payload(answer)
    summary = f"承認ゲート {step.gate} の回答: {p['answer']}（{answer.draft.reason}）"
    return result.result(TOOL, result.OK, summary, metrics={"answer": p["answer"], "seq": answer.seq})


def _consume(d: Drive, v: View, m: Move) -> Optional[dict[str, Any]]:
    """承認ゲートの回答を読む。回答が無ければ直前の MVV 判定が省けると決めた承認ゲートで、`service:mvv` の承認を書く。

    省いた回答を書いたら、同じ駆動では読まずに `next` を返して止まる。回答を読む前の検証（`gate.recheck`）はサブコマンドの
    エントリポイントでだけ打つため、続けて読むと判定の後に進んだ PR の head でも承認ゲートを省いてしまう。次の `next` が検証を通してから読む。
    数えない MVV 判定の回答（`model.mvv_answer_void`）と、省けると決めた判定の無い手は、事象を書かずに承認ゲートの待ちを返す（#3959）。
    """
    assert m.stage is not None and m.step is not None and m.progress is not None
    waiting = replace(m, action=Action.GATE, event=None)
    if m.event is not None and model.mvv_answer_void(event_payload(m.event), m.step.gate):
        return report(v, waiting)
    if m.event is None:
        held = model.mvv_skip(m.progress.done, m.stage.mvv_before(m.step.id), m.step.gate)
        if held is None:
            return report(v, waiting)
        qid = model.question_id(v.machine_id, v.rev, m.stage.name, m.step.id, m.progress.anchor)
        payload, reason = model.mvv_answer(m.step.id, invoke.LISTEN, APPROVED, held, qid)
        d.write(model.EventDraft(ANSWERED, CONDUCTOR, m.stage.name, reason=reason, payload=payload, rev=v.rev))
        summary = f"{reason}。回答は検証を通してから読むので next を打つ"
        return result.result(TOOL, result.OK, summary, metrics=_metrics(v, m), next=_cmd("next", d.machine_id))
    if event_payload(m.event)["answer"] == APPROVED:
        rep = gate_report(m.step, m.event)
        payload = {"step": m.step.id, "status": FINISHED, "report": rep, "generation": m.progress.generation}
        d.write(model.EventDraft(STEP_EVENT, ACTOR, m.stage.name, payload=payload, rev=v.rev))
        return None
    detail = f"承認ゲート {m.step.gate} で承認されなかった（{m.event.draft.reason}）"
    d.write(_failed(m.stage, m.step.id, v.rev, "refused", detail))
    d.synced()
    return None


def _complete(d: Drive, v: View) -> dict[str, Any]:
    done = model.EventDraft("completed", ACTOR, None, rev=v.rev)
    append(d.st, d.machine_id, lambda _v: [done], after=lambda conn: d.env.ended(conn, d.machine_id))
    d.synced()
    done = read(d.st, d.machine_id)
    return result.result(TOOL, result.OK, f"ステートマシン {d.machine_id} を完了にした", metrics=_metrics(done, Move(Action.DONE)))


def _resume(d: Drive, v: View, m: Move) -> Optional[dict[str, Any]]:
    """実行中の行の続き。`next` なら作業を起動し、`work` なら次のステップか工程の終わりを流す。"""
    assert m.stage is not None
    if not d.inline:
        return _launched(d, v, m)
    if m.step is None:
        _finish_stage(d, v, m.stage)
    else:
        d.write(*_run_step(d, v, m.stage, m.step))
    if decide(read(d.st, d.machine_id)).stage != m.stage:
        d.synced()
    return None


Hand = Callable[[Drive, View, Move], Optional[dict[str, Any]]]
# 副作用を持つ手 → 打つ関数（表に無い手は副作用の無い結果 `report` を返す）
_HANDS: dict[Action, Hand] = {
    Action.COMPLETE: lambda d, v, _m: _complete(d, v),
    Action.CONSUME: _consume,
    Action.START: lambda d, v, m: _start(d, v, m.stage) if m.stage is not None else None,
    Action.RESUME: _resume,
}


def _step(d: Drive, v: View, m: Move) -> Optional[dict[str, Any]]:
    """1 手を打つ。続けて次の手を決めるなら None。"""
    play = _HANDS.get(m.action)
    return play(d, v, m) if play is not None else report(v, m)


def _drive(d: Drive) -> dict[str, Any]:
    first = read(d.st, d.machine_id)
    # 1 手ごとに行かステップが 1 つ進む（行ごとに始めと終わり、ステップごとに流すと回答の読み取り）に、最後の完了の 1 手を
    # 足す。手の数はこの数を超えない。工程の無いステートマシン（開発ループの行。#3558）も完了の 1 手で終わる
    for _ in range(3 * (len(first.snap.stages) + len(first.snap.steps())) + 1):
        v = read(d.st, d.machine_id)
        out = _step(d, v, decide(v))
        if out is not None:
            return command.with_warnings(out, before=d.warnings)
    raise AssertionError("手の数が行とステップの数を超えた")


def advance(st: store.Store, machine_id: str, cwd: Path, env: Env) -> dict[str, Any]:
    """`next`: 排他を取り、終わるか待つか作業を起動するまで手を進める。作業が動いていれば `wait` を返す。"""
    if is_live(st, machine_id):
        summary = f"ステートマシン {machine_id} を流す作業が動いている。終わりは wait で待つ"
        return result.result(TOOL, result.OK, summary, metrics={"machine_id": machine_id}, next=_cmd("wait", machine_id))
    with store.run_lock(st, machine_id):
        migrate_definition(st, machine_id)
        return _drive(Drive(st, machine_id, cwd, env, inline=False))


def _launching(st: store.Store, machine_id: str) -> bool:
    """起動した作業がまだ排他を取っていないか（起動のマーカーがあり、マーカーの組が生きている）。"""
    try:
        raw = json.loads(launch_path(st.root, machine_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: マーカーが無い・書きかけなら起動した作業は無い
        return False
    pid = raw.get("pid") if isinstance(raw, dict) else None
    return isinstance(pid, int) and pid > 0 and process_group.group_alive(pid)


def is_live(st: store.Store, machine_id: str) -> bool:
    """同じステートマシンを流す作業（か `next`）が動いているか。排他を取れないか、起動した作業がまだ排他を取っていなければ動いている。"""
    try:
        with store.run_lock(st, machine_id):
            held = _launching(st, machine_id)
    except model.Refused:
        held = True
    return held


def hand(st: store.Store, v: View) -> Optional[dict[str, Any]]:
    """次に打つ 1 手（`show` が返す。プロセスが落ちた後も同じ）。作業が動いていれば `wait`、終わっていれば無し、ほかは `next`。"""
    if model.is_terminal(v.whole):
        return None
    return _cmd("wait" if is_live(st, v.machine_id) else "next", v.machine_id)


# --- wait ----------------------------------------------------------------------------------


def wait(
    st: store.Store,
    machine_id: str,
    timeout: float,
    sleep: Callable[[float], None] = time.sleep,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """作業が動いている間だけ待ち、止まったら今の 1 手を返す。上限に当たれば同じ `wait` を打ち直す手を返す。

    `clock` は期限を測る単調な時計（テストが `sleep` と組で差し替える）。
    """
    interval = limits.value(WAIT_POLL, "interval")
    deadline = clock() + timeout
    while is_live(st, machine_id):
        if clock() >= deadline:
            detail = f"ステートマシン {machine_id} を流す作業がまだ動いている（{timeout:g} 秒待った）"
            return result.stopped(TOOL, "wait_timeout", detail, next=_cmd("wait", machine_id))
        sleep(interval)
    v = read(st, machine_id)
    return report(v, decide(v))


# --- answer --------------------------------------------------------------------------------


def notes_text(value: str, path: Optional[Path]) -> Optional[str]:
    """変更要求の指摘の本文。変更要求には空でない指摘ファイルが要る（3365-4）。"""
    if value != REQUEST_CHANGES:
        return None
    if path is None:
        raise model.Refused("変更要求（request_changes）には指摘ファイル（--notes）が要る")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise model.Refused(f"指摘ファイル {path} を読めない: {exc}") from exc
    if not text.strip():
        raise model.Refused(f"指摘ファイル {path} が空")
    return text


def answer_draft(v: View, stage: str, value: str, reason: str, text: Optional[str], extra: Mapping[str, Any]) -> model.EventDraft:
    """待っているステップへの回答の事象。待っていない行・選択肢に無い回答・承認ゲートへの質問の id（`gate_id`）を持たない
    回答（3364-2）は断る。`extra` は payload へ足す（`by` は回答した者で、payload に残す）。

    事象の書き手は回答した者と分ける: 回答した者（`human:<名前>`・`service:<主体>` など）は事象 `answered` の書き手の表に無く、
    回答を書くのは `ndf progress` / `ndf answer` を打つ conductor だからである（organizer が回答したときだけ organizer）。"""
    m = decide(v)
    if m.action != Action.GATE or m.stage is None or m.stage.name != stage or m.step is None:
        raise model.Refused(f"工程 {stage} は回答を待っていない")
    if invoke.is_listen(m.step) and "gate_id" not in extra:
        raise model.Refused(f"工程 {stage} は承認ゲートで、回答は ndf answer で質問の id と見た対象を添える（next の routes）")
    allowed = choices(m)
    if value not in allowed:
        raise model.Refused(f"工程 {stage} の回答は {', '.join(allowed)} のどれか（渡された値 {value}）")
    payload = {"answer": value, "step": m.step.id, "kind": invoke.kind_of(m.step), "notes": text, **extra}
    writer = ORGANIZER if "by" in extra and extra["by"] == ORGANIZER else CONDUCTOR
    return model.EventDraft(ANSWERED, writer, stage, reason=reason, payload=payload, rev=v.rev)


def answer(st: store.Store, machine_id: str, stage: str, value: str, reason: str, notes: Optional[Path]) -> dict[str, Any]:
    """待っているステップへの回答を事象 `answered` で残す（`answer_draft`）。"""
    text, extra = notes_text(value, notes), ({"notes_path": str(notes)} if notes is not None else {})
    seqs = append(st, machine_id, lambda v: [answer_draft(v, stage, value, reason, text, extra)])
    summary = f"工程 {stage} へ回答 {value} を書いた。続きは next で流す"
    return result.result(TOOL, result.OK, summary, metrics={"machine_id": machine_id, "seqs": seqs}, next=_cmd("next", machine_id))


# --- work（next が起動する作業） ----------------------------------------------------------------


def _retrying(call: Callable[[bool], invoke.StageResult], sleep: Callable[[float], None]) -> invoke.StageResult:
    """打ち直しで直る止まりを方針どおりに打ち直す。`call(again)` の `again` は打ち直しか（プランは止まったステップから
    新しい回を始める。`invoke.plan_start`・`invoke.plan_attempt`）。

    打ち直しても同じプランの回の結果が返った（回が変わらず、バックアップの結果を返した）なら、それ以上は打ち直さない。
    """
    used = 0
    res = call(False)
    while res.status == result.STOPPED and (delay := causes.retry_delay(res.cause, used)) is not None:
        sleep(delay)
        used += 1
        again = call(True)
        if invoke.same_run(res, again):
            return again
        res = again
    return res


def _failed_with(s: stages.StageDef, step: Optional[str], rev: int, cause: dict[str, Any]) -> model.EventDraft:
    """行を失敗にする事象の下書き（停止理由と、止まったステップ）。"""
    return model.EventDraft(FAILED, ACTOR, s.name, payload={"cause": cause, "step": step}, rev=rev)


def _failed(s: stages.StageDef, step: Optional[str], rev: int, kind: str, detail: str) -> model.EventDraft:
    return _failed_with(s, step, rev, causes.cause(kind, detail))


def step_event(v: View, stage: str, payload: dict[str, Any]) -> model.EventDraft:
    """工程 `stage` のステップ記録の事象の下書き。"""
    return model.EventDraft(STEP_EVENT, ACTOR, stage, payload=payload, rev=v.rev)


def _step_draft(v: View, s: stages.StageDef, step: stages.StepDef, prog: StepProgress, res: invoke.StageResult) -> list[model.EventDraft]:
    """ステップの結果を事象にする。終われば出力の規約で照らし、承認ゲートで止まれば待ちを残し、止まれば失敗にする。

    出力の規約は終わった結果（ok）だけに当てる。承認ゲートの待ちの結果は出力を持たないためである。読み手役のステップが
    文書を直して `outputs.review_again` を返したら、続けて設計のレビューから流し直す事象を足す（`review_again_drafts`。#4446）。
    """
    if res.status == result.STOPPED:
        cause = res.cause if res.cause is not None else causes.cause("unknown", "作業が理由なく止まった")
        return [_failed_with(s, step.id, v.rev, cause)]
    errs = definition.validate_io(step.output_ref, res.report) if step.output_ref and res.status == result.OK else []
    if errs:
        return [_failed(s, step.id, v.rev, "bad_report", _step_gap_detail(step, res.report, errs))]
    status = FINISHED if res.status == result.OK else "waiting"
    payload = {"step": step.id, "status": status, "report": res.report, "generation": prog.generation, "warnings": list(res.warnings)}
    done = step_event(v, s.name, payload)
    again = review_again_drafts(v, s, step.gate, res.report) if status == FINISHED and step.kind == stages.READING_KIND else []
    return [done, *again]


def _step_gap_detail(step: stages.StepDef, report: Mapping[str, Any], errs: list[str]) -> str:
    """ステップの出力が規約に合わない失敗の理由。承認の回答を経た結果（`metrics.waited`）なら、規約が必須とするキーのうち
    結果に無い名前と、判断を求めたステップの要約と、次の操作を示す（#4513）。ほかは今の文。"""
    ref = step.output_ref

    def missing() -> list[str]:
        outputs = result.outputs_of(report)
        return [n for n in definition.required_outputs(ref) if n not in outputs] if ref else []

    return definition.gap_failure_detail([report], missing, f"ステップ {step.id} の出力が規約に合わない: {'; '.join(errs)}")


def review_again_drafts(v: View, s: stages.StageDef, gate: Optional[str], report: Mapping[str, Any]) -> list[model.EventDraft]:
    """`outputs.review_again` を受けて、設計のレビューから承認ゲート `gate` の前までのステップを未了に戻す事象（#4446）。
    最初の事象が戻したことと `review_again` のコミットを持ち、工程の記録に残る（`reading_args` が読む）。"""
    again = stages.review_again_of(report)
    if again is None:
        return []
    gen, why = step_progress(v, s.name).generation, f"読み手役の直しで文書を push した（{again['commit']}）ので、設計のレビューから流し直す"
    drafts = [
        {"step": i, "status": stages.RESET, "report": None, "generation": gen, "reason": why} for i in stages.review_again_steps(s, gate)
    ]
    drafts[0].update({stages.REVIEW_AGAIN: again, "gate": gate})
    return [step_event(v, s.name, p) for p in drafts]


def reading_args(v: View, s: stages.StageDef, gate: Optional[str]) -> list[str]:
    """読み手役のステップへ足す引数を、工程の記録とゲートの名前だけから作る（`stages.reading_flags`。#4446 の計画の (7)）。"""
    prog = step_progress(v, s.name)
    return stages.reading_flags(s, [a for a in prog.again if a["gate"] == gate], prog.finished_at)


def _reading_run(v: View, s: stages.StageDef, step: stages.StepDef, run: invoke.StageRun, work_dir: Path) -> invoke.StageRun:
    """読み手役のステップの argv に、保存先・結果ファイルと工程の記録から作る引数（`reading_args`）を足す。"""
    out = ["--state-dir", str(work_dir), "--out", str(work_dir / stages.READING_OUT)]
    args = {**run.args, "argv": [*run.args["argv"], *out, *reading_args(v, s, step.gate)]}
    return replace(run, inputs=_reading_inputs(v, s, step.gate, run.inputs), args=args)


def _reading_inputs(v: View, s: stages.StageDef, gate: Optional[str], inputs: Mapping[str, Any]) -> dict[str, Any]:
    """読み手役のステップの入力の MVV 判定を、同じ工程の判定のステップの記録に差し替える。文脈は工程が終わるまで
    書かれず、前の工程の判定が残るためである。判定が無ければ渡さない。"""
    got = {k: value for k, value in inputs.items() if k != model.MVV_KEY}
    step = model.MVV_GATE_STEPS.get(gate) if gate else None
    done = step_progress(v, s.name).done
    outputs = done[step].get("outputs") if step in done else None
    held = outputs.get(model.MVV_KEY) if isinstance(outputs, dict) else None
    if isinstance(held, dict) and model.MVV_KEY in (*s.inputs, *s.optional_inputs):
        got[model.MVV_KEY] = held
    return got


def _reading_skipped(d: Drive, v: View, s: stages.StageDef, step: stages.StepDef, prog: StepProgress) -> Optional[list[model.EventDraft]]:
    """読み手役のステップを打たずに済ませるなら、済ませた記録の事象。打つなら None（`model.reading_skip`）。"""
    gate = next((st for st in s.steps if st.kind == stages.GATE_KIND and st.gate == step.gate), None)
    waiting_gate = gate is not None and gate.id in prog.held
    in_lane = step.gate == model.release_gate() and d.env.in_lane(d.st, v.machine_id)
    if not model.reading_skip(prog.done, s.before(step.id), step.gate, waiting_gate, in_lane):
        return None
    why = "計画のレーンの行にあるスプリントなので" if in_lane else "直前の MVV 判定が承認ゲートを省くので"
    rep = result.result(TOOL, result.OK, f"{why}、読み手役のステップ {step.id} を打たずに済ませた")
    payload = {"step": step.id, "status": FINISHED, "report": rep, "generation": prog.generation, "warnings": []}
    return [step_event(v, s.name, payload)]


def step_location(step: str, prog: StepProgress) -> str:
    """ステップのプランの実行の保存先の名前。変更要求（世代）と未了に戻した記録（#4446）で新しい保存先にする（打ち直しは同じ保存先の新しい回）。"""
    reset = prog.resets[step] if step in prog.resets else 0
    return f"{step}-g{prog.generation}" + (f"-r{reset}" if reset else "")


def step_dir(d: Drive, v: View, s: stages.StageDef, step: stages.StepDef) -> Path:
    """ステップのプランの実行の保存先。`_start` と `_run_step` が同じ世代の同じ保存先を得る。"""
    return work_root(d.st.root, v.machine_id) / s.id / step_location(step.id, step_progress(v, s.name))


def approved_seen(answers: StageAnswers, root: Path) -> Optional[dict[str, Any]]:
    """工程の承認ゲートの最新の承認の回答が見た対象（事象の `seen`）。無ければ None（#4409 の決定 5）。

    利用者の回答でも、MVV 判定が通した回答（`service:mvv`）でも同じ形で読む。マージが本番へ反映されない（`production` が偽）
    承認は、出し直さずに繰り上げた head（`gate.recheck`）を重ねる。分からない・本番へ反映される承認には重ねない。
    `root` は状態の保存先。最新の承認の回答は `answers` の走査から得る（#4513）。
    """
    ev = answers.approved()
    if ev is None:
        return None
    p = event_payload(ev)
    seen = p.get("seen")
    if not isinstance(seen, dict):
        return None
    gate_id = p.get("gate_id")
    carried = None
    if seen.get("production") is False and gate_id:
        carried = read_accepted_head(work_root(root, answers.machine_id), str(gate_id))
    return {**seen, "head": carried} if carried else dict(seen)


def _released_reader(answers: StageAnswers) -> Callable[[], Optional[Recorded]]:
    """記録済みの値を初回の呼び出しで 1 回だけ求める関数（リリースの識別子を使うプランだけが呼ぶ。呼ばれるまで履歴をたどらない）。"""
    return functools.cache(lambda: released_answer(answers))


def _run_step(d: Drive, v: View, s: stages.StageDef, step: stages.StepDef) -> list[model.EventDraft]:
    """ステップを 1 つ呼ぶ。打ち直しで直る止まりは方針どおりに打ち直す（SF8）。読み手役のステップは済ませるかを先に決める（#4446）。"""
    prog = step_progress(v, s.name)
    skipped = _reading_skipped(d, v, s, step, prog) if step.kind == stages.READING_KIND else None
    if skipped is not None:
        return skipped
    try:
        inputs = invoke.resolve_inputs(s, v.context())
    except invoke.StageInputError as exc:
        return [_failed(s, step.id, v.rev, exc.kind, str(exc))]
    answers = StageAnswers(v, s.name)
    approved = approved_seen(answers, d.st.root)
    run = replace(invoke.stage_run(s, step, inputs), notes=prog.notes, retries=prog.retries, approved=approved)
    if invoke.kind_of(step) == invoke.PLAN:
        run = replace(run, released=_released_reader(answers))
    waited = prog.waiting is not None and prog.waiting["step"] == step.id
    ans = prog.answer_value() if waited else None
    work_dir = step_dir(d, v, s, step)
    if step.kind == stages.READING_KIND:
        work_dir.mkdir(parents=True, exist_ok=True)
        run = _reading_run(v, s, step, run, work_dir)
    res = _retrying(lambda again: d.env.invoker(replace(run, again=again), d.cwd, work_dir, ans), d.env.sleep)
    return _step_draft(v, s, step, prog, res)


def _stage_output(s: stages.StageDef, prog: StepProgress) -> tuple[dict[str, Any], dict[str, Any]]:
    """工程の出力（ステップの結果の並び・要約・定義したキーの出力）と、ステップが出した出力のすべて。"""
    reports = [prog.done[st.id] for st in s.steps]
    merged = stages.step_outputs(s, prog.done)
    out: dict[str, Any] = {"steps": reports, "summary": f"{s.name} の {len(reports)} ステップが終わった"}
    kept, _ = stages.keep_declared(s.outputs, merged)
    if kept:
        out["outputs"] = kept
    return out, merged


def _finish_stage(d: Drive, v: View, s: stages.StageDef) -> None:
    """全ステップが終わった工程の出力を工程の出力の規約で照らし、合えば出力を文脈へ書いて終了のマーカーを付ける。"""
    out, merged = _stage_output(s, step_progress(v, s.name))
    errs, gaps = definition.check_stage_output(s, out)
    if errs:
        why = f"工程 {s.name} の出力が規約に合わない: {'; '.join(errs)}"
        d.write(_failed(s, None, v.rev, "bad_report", definition.gap_failure_detail(out["steps"], lambda: [k for k, _ in gaps], why)))
        return
    draft, warnings = stages.outputs_draft(s, v.rev, merged, ACTOR)
    done = model.EventDraft(FINISHED, ACTOR, s.name, reason=out["summary"], payload={"steps": len(out["steps"])}, rev=v.rev)
    d.write(*([draft] if draft is not None else []), done)
    d.warnings.extend(warnings)


def _work_lock(stack: ExitStack, st: store.Store, machine_id: str, sleep: Callable[[float], None]) -> bool:
    """作業の排他を取る。起動した `next` が放すまで待ち、待ちの上限までに取れなければ偽（ほかの作業が流している）。"""
    interval = limits.value(LOCK_POLL, "interval")
    for _ in range(max(1, int(limits.value(WAIT_POLL, "interval") / interval))):
        try:
            stack.enter_context(store.run_lock(st, machine_id))
        except model.Refused:
            sleep(interval)
        else:
            return True
    return False


def work(st: store.Store, machine_id: str, cwd: Path, env: Env) -> dict[str, Any]:
    """工程を順に流す。回答を待つステップ・失敗・ステートマシンの終わりで抜ける。"""
    with ExitStack() as stack:
        locked = _work_lock(stack, st, machine_id, env.sleep)
        launch_path(st.root, machine_id).unlink(missing_ok=True)  # 排他を取った（か、ほかの作業が持つ）ので起動のマーカーは要らない
        if not locked:
            return result.result(
                TOOL, result.OK, f"ステートマシン {machine_id} はほかの作業が流している", metrics={"machine_id": machine_id}
            )
        return _drive(Drive(st, machine_id, cwd, env, inline=True))
    raise AssertionError("到達しない")


# --- サブコマンド ndf progress のエントリポイント ----------------------------------------------------------------


def _cmd_next(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    return advance(st, a.machine_id, cwd, a.env)


def _cmd_wait(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    return wait(st, a.machine_id, a.timeout)


def _cmd_answer(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    return answer(st, a.machine_id, a.stage, a.answer, a.reason, Path(a.notes) if a.notes else None)


def _cmd_resync(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    return resync(st, a.machine_id, cwd, a.env)


def _cmd_work(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    return work(st, a.machine_id, cwd, a.env)


def _cmd_show(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    """ステートマシンの表示に、次に打つ 1 手（`next`）を足す。プロセスが落ちた後もこの 1 手で続けられる（3366-1）。"""
    out = command.COMMANDS["show"](a, cwd, st)
    held = hand(st, read(st, a.machine_id))
    if held is not None:
        out["next"] = held
    return out


# サブコマンドの名前 → 受ける関数（名前は関数の `_cmd_<サブコマンド>` から引く）
COMMANDS: dict[str, command.Command] = {
    fn.__name__.removeprefix("_cmd_"): fn for fn in (_cmd_next, _cmd_wait, _cmd_answer, _cmd_resync, _cmd_work, _cmd_show)
}


def parser() -> argparse.ArgumentParser:
    """工程を流すサブコマンド（next・wait・answer・resync・work）と、次の 1 手を足した show の引数。"""
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL}", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)
    for name, text in (
        ("next", "次の工程を流す"),
        ("resync", "進捗記録を揃え直す"),
        ("work", "工程を流す作業（next が起動する）"),
        ("show", "ステートマシンを表示する"),
    ):
        sub.add_parser(name, help=text).add_argument("machine_id")
    w = sub.add_parser("wait", help="工程を流す作業の終わりを待つ")
    w.add_argument("machine_id")
    w.add_argument("--timeout", type=float, default=plan_cli.wait_default_seconds())
    ans = sub.add_parser("answer", help="回答を待つ工程へ回答を書く")
    command.add_stage_target(ans)
    ans.add_argument("--answer", required=True, choices=facts.ids(result.ANSWERS))
    ans.add_argument("--notes", help="変更要求（request_changes）の指摘ファイル")
    command.add_cwd(sub.choices.values())
    return ap


def main(argv: list[str]) -> int:
    """サブコマンド `ndf progress`。工程を流すサブコマンドはここで受け、ほか（new・disable など）は `command` へ渡す。"""
    if not argv or argv[0] not in COMMANDS:
        return command.main(argv)
    env = Env(facts=command.facts_of)
    return command.run_main(parser(), lambda a, cwd: command.with_store(cwd, COMMANDS[a.action], with_env(a, env)), argv)


def with_env(a: argparse.Namespace, env: Env) -> argparse.Namespace:
    """サブコマンドの引数に外との接点 `env` を足す（エントリポイントの `ndf.progress.gate` は包んだ `Env` を渡す）。"""
    return argparse.Namespace(**vars(a), env=env)
