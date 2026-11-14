"""プランの実行の回を、1 つの実行の鍵で DBOS の殻を通して流す（B1・B2・B4）。

- 実行の回の記録 `run.json`（状態の置き場）が鍵を持つ。同じプラン・開始点で打ち直すと、回が終わるまで同じ鍵で続く。
  終わった回は、head が回の初めか終わりの head と同じなら同じ鍵で、結果を返して流さない（B4）。head が違えば
  （差し戻しの後に直した）次の回の鍵で流す（#3176）
- 耐久ワークフロー `plan_workflow` は遷移の判断（`transition_decide.decide`）と手の実行を判断のループで回す。
  ステップは耐久ステップで打ち、打ち直しの待ちと承認の答えの待ちは耐久の `recv` で待つ
- 承認の答えを待つ所に来たら、待ちの目印 `waiting-<鍵の名前>.json` を書く。呼び手は目印を見て承認ゲートの結果を出し、
  DBOS を閉じずにプロセスを抜ける（待っている耐久ワークフローは鍵のファイルに残り、次の起動が回復する。B2）
"""

from __future__ import annotations

import functools
import json
import os
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ndf import cli
from ndf.adapters import git
from ndf.contracts import limits, result, routing
from ndf.contracts.exit_codes import GATE, OK, STOPPED
from ndf.core import durable, loop
from ndf.core.plan import assemble_decide, model, queue_decide, steps, transition_decide
from ndf.core.plan.transition_decide import ANSWERED, SLEPT, AwaitAnswer, Outcome, RunStep, Sleep

# 結果 JSON の tool の名前は副命令の名前（副命令の表から引く）
TOOL = next(name for name, module in cli.COMMANDS.items() if module == f"{__package__}.cli")
CLI_NAME = "ndf"
RUN_FILE = "run.json"
RESULTS_DIR = "results"
WAITING_PREFIX = "waiting-"
TOPIC_ANSWER = "answer"
TOPIC_RETRY = "retry-wait"
# 承認の答えを 1 回に待つ秒。期限で答えが無ければ待ち直す（答えの来ない承認ゲートを打ち切らない）
ANSWER_WAIT_SECONDS = 86400.0
POLL = "durable.poll"


@dataclass(frozen=True)
class Invocation:
    """1 回の呼び出しの組み立て。`again` はこの呼び出しを打ち直す引数（`ndf plan next` の後ろ）。"""

    plan_path: Path
    plan: model.Plan
    start: Optional[str]
    prelude: tuple[str, ...]
    state_dir: Path
    key: str
    round_no: int
    head: Optional[str]
    values: dict[str, str] = field(default_factory=dict)
    again: tuple[str, ...] = ()

    @property
    def workflow_id(self) -> str:
        return f"{durable.run_name(self.key)}-plan"

    @property
    def waiting_path(self) -> Path:
        return self.state_dir / f"{WAITING_PREFIX}{durable.run_name(self.key)}.json"

    @property
    def result_path(self) -> Path:
        return self.state_dir / RESULTS_DIR / f"{durable.run_name(self.key)}.json"


# --- 耐久ワークフロー ---------------------------------------------------------------------


@durable.step
def run_step(spec: dict[str, Any], step_id: str, attempt: int) -> dict[str, Any]:
    """ステップを 1 回打つ耐久ステップ。済んだ呼び出しは回復のときに打ち直さない。"""
    plan = model.parse(spec["plan"])
    ctx = steps.StepContext(plan, Path(spec["state_dir"]), dict(spec["values"]))
    out = steps.run(ctx, plan.step(step_id))
    out["attempt"] = attempt
    return out


@durable.step
def mark_waiting(path: str, step_id: str, seq: int, routes: dict[str, str]) -> None:
    """承認の答えを待つ目印を書く（呼び手と `wait` が読む）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps({"step": step_id, "seq": seq, "routes": routes}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, target)


@durable.step
def clear_waiting(path: str) -> None:
    """答えを受けたので待ちの目印を消す。"""
    Path(path).unlink(missing_ok=True)


def _perform(spec: dict[str, Any], act: Any) -> Outcome:
    """手を実行する。ステップは耐久ステップ、待ちは耐久の recv で行う。"""
    if isinstance(act, RunStep):
        out = run_step(spec, act.step, act.attempt)
        return Outcome(out["status"], out["kind"], out["retry_after"])
    if isinstance(act, Sleep):
        durable.recv(TOPIC_RETRY, act.seconds)
        return Outcome(SLEPT)
    if isinstance(act, AwaitAnswer):
        mark_waiting(spec["waiting"], act.step, act.seq, spec["routes"][act.step])
        answer = durable.recv(TOPIC_ANSWER, ANSWER_WAIT_SECONDS)
        if answer is not None:
            clear_waiting(spec["waiting"])
        return Outcome(ANSWERED, answer=answer)
    raise TypeError(f"知らない手: {act!r}")


@durable.workflow
def plan_workflow(spec: dict[str, Any]) -> dict[str, Any]:
    """プランの遷移を判断のループで回し、終わりの状態を返す。"""
    plan = model.parse(spec["plan"])
    state = transition_decide.start_state(plan, spec["start"], spec["prelude"])
    final = loop.run(functools.partial(transition_decide.decide, plan), functools.partial(_perform, spec), state)
    return {"status": final.status, "kind": final.kind, "detail": final.detail, "last": final.last, "count": final.count}


# --- 組み立て -----------------------------------------------------------------------------


def _head(plan: model.Plan) -> Optional[str]:
    where = plan.worktree if steps.usable_worktree(plan.worktree) else plan.repo
    if where is None:
        return None
    try:
        return git.Git(Path(where)).rev_parse("HEAD")
    except git.GitError:  # noqa: NDF401 外部コマンド: head の無い置き場は head を鍵に入れない
        return None


def read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):  # noqa: NDF401 ファイル: 無い・書きかけの記録は無いものとして扱う
        return None
    return found if isinstance(found, dict) else None


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _again(plan_path: Path, start: Optional[str], state_dir: Optional[str]) -> tuple[str, ...]:
    args = [str(plan_path)]
    if start is not None:
        args += ["--from", start]
    if state_dir is not None:
        args += ["--state-dir", state_dir]
    return tuple(args)


def _prelude(plan: model.Plan, start: Optional[str]) -> tuple[str, ...]:
    if start is None or start in routing.PLAN_TARGETS:
        return ()
    return queue_decide.resume_prelude(plan.order, start)


def prepare(root: Path, plan_path: Path, start: Optional[str], state_dir: Optional[str], cwd: Path) -> Invocation:
    """プランを読み、状態の置き場・鍵・差し込みの値を決める。誤りは `model.PlanError`（開始点の誤りを含む）。"""
    plan = model.load(plan_path)
    if start is not None and start not in plan.order and start not in routing.PLAN_TARGETS:
        raise model.PlanError(f"{plan.name} に {start} は無い")
    given = state_dir if state_dir is not None else plan.state_dir
    sdir = Path(assemble_decide.state_dir_for(str(plan_path), given, str(cwd)))
    head = _head(plan)
    record = read_json(sdir / RUN_FILE)
    finished = record is not None and _finished(root, record["key"])
    choice = assemble_decide.choose_run(record, plan.name, plan.digest(), start, head, finished)
    abs_plan = (cwd / plan_path) if not plan_path.is_absolute() else plan_path
    values = {
        "state_dir": str(sdir),
        "repo": plan.repo if plan.repo else "",
        "worktree": plan.worktree if plan.worktree else "",
        "plan": str(abs_plan),
    }
    return Invocation(
        abs_plan, plan, start, _prelude(plan, start), sdir, choice.key, choice.round_no, head, values, _again(abs_plan, start, state_dir)
    )


# --- 実行の回 -----------------------------------------------------------------------------


def _finished(root: Path, key: str) -> bool:
    """鍵の回が終わって退避されたか（retired/ に鍵のファイルがある）。"""
    name = durable.run_name(key)
    return any((root / durable.DBOS_DIR / durable.RETIRED_DIR).glob(f"{name}*{durable.DB_SUFFIX}"))


def _routes(inv: Invocation) -> dict[str, dict[str, str]]:
    """承認の答えを待つステップごとの、答え → 次に打つコマンド。"""
    base = [CLI_NAME, TOOL, "next", *inv.again]
    return {s.id: {a: shlex.join([*base, "--answer", a]) for a in s.on_answer} for s in inv.plan.steps if s.on_answer}


def _spec(inv: Invocation) -> dict[str, Any]:
    return {
        "plan": dict(inv.plan.raw),
        "start": inv.start,
        "prelude": list(inv.prelude),
        "state_dir": str(inv.state_dir),
        "values": inv.values,
        "waiting": str(inv.waiting_path),
        "routes": _routes(inv),
    }


def _record_run(inv: Invocation, end_head: Optional[str] = None) -> None:
    path = inv.state_dir / RUN_FILE
    record = read_json(path)
    heads = list(record["heads"]) if record is not None and record["key"] == inv.key else []
    heads += [h for h in (inv.head, end_head) if h is not None and h not in heads]
    _write_json(
        path,
        {"key": inv.key, "name": inv.plan.name, "digest": inv.plan.digest(), "start": inv.start, "round": inv.round_no, "heads": heads},
    )


def _watch(inv: Invocation, baseline: Optional[dict[str, Any]]) -> tuple[str, Optional[dict[str, Any]]]:
    """耐久ワークフローが終わるか、`baseline` と違う待ちの目印が現れるまで見る。"""
    interval = limits.value(POLL, "interval")
    while True:
        if durable.status(inv.workflow_id) not in durable.WAITING:
            return "done", None
        marker = read_json(inv.waiting_path)
        if marker is not None and marker != baseline:
            return GATE, marker
        time.sleep(interval)


def gate_result(inv: Invocation, marker: dict[str, Any]) -> dict[str, Any]:
    """承認の答えを待つ結果。`routes` は答えごとに打つコマンド。"""
    summary = f"{inv.plan.name} のステップ {marker['step']} が承認の答えを待っている"
    items = [{"kind": "plan", "name": inv.plan.name, "step": marker["step"], "gate": True}]
    return result.result(
        TOOL,
        GATE,
        summary,
        items,
        {"round": inv.round_no},
        routes=marker["routes"],
        next=shlex.join([CLI_NAME, TOOL, "wait", str(inv.plan_path)]),
    )


def final_result(inv: Invocation, final: Optional[dict[str, Any]], status: Optional[str]) -> dict[str, Any]:
    """終わった回の結果。耐久ワークフローが例外で終わったときは unknown で止まる。"""
    items = [{"kind": "plan", "name": inv.plan.name, "run": durable.run_name(inv.key)}]
    metrics = {"round": inv.round_no, "steps": final["count"] if final else 0}
    if final is None:
        return result.stopped(TOOL, "unknown", f"{inv.plan.name} の耐久ワークフローが {status} で終わった", items=items, metrics=metrics)
    if final["status"] == OK:
        return result.result(TOOL, OK, f"{inv.plan.name} が終わった", items, metrics)
    if final["status"] == STOPPED:
        return result.stopped(TOOL, final["kind"], final["detail"], items=items, metrics=metrics)
    after = inv.plan.following(final["last"])
    cont = shlex.join([CLI_NAME, TOOL, "next", str(inv.plan_path), "--from", after]) if after else None
    return result.result(TOOL, GATE, final["detail"], items, metrics, routes={"approved": cont, "rejected": None})


def _stored(inv: Invocation) -> dict[str, Any]:
    found = read_json(inv.result_path)
    if found is not None:
        return found
    items = [{"kind": "plan", "name": inv.plan.name, "run": durable.run_name(inv.key)}]
    return result.result(TOOL, OK, f"{inv.plan.name} の回は終わっている（結果の控えが無い）", items, {"round": inv.round_no})


def _finish(inv: Invocation) -> dict[str, Any]:
    status = durable.status(inv.workflow_id)
    final = durable.result(inv.workflow_id) if status == "SUCCESS" else None
    out = final_result(inv, final, status)
    _write_json(inv.result_path, out)
    _record_run(inv, _head(inv.plan))
    return out


def advance(
    root: Path, inv: Invocation, answer: Optional[str], on_gate: Callable[[dict[str, Any]], None], url: Optional[str] = None
) -> dict[str, Any]:
    """回を始めるか続け、終わるか承認の答えを待つ所まで進める。

    待つ所に来たら `on_gate(結果)` を DBOS を開いたまま呼ぶ。`on_gate` は結果を出してプロセスを抜ける（戻らない）。
    終わった回（retired/）なら流さずに控えの結果を返す（B4）。
    """
    if _finished(root, inv.key):
        return _stored(inv)
    _record_run(inv)
    with durable.session(root, inv.key, url):
        status = durable.status(inv.workflow_id)
        baseline = read_json(inv.waiting_path)
        if status is None:
            durable.spawn(plan_workflow, inv.workflow_id, _spec(inv))
        elif answer is not None:
            if baseline is None:
                return result.stopped(TOOL, "invalid", f"{inv.plan.name} は承認の答えを待っていない")
            durable.send(inv.workflow_id, answer, TOPIC_ANSWER)
        else:
            baseline = None
        reached, marker = _watch(inv, baseline)
        if reached == GATE:
            on_gate(gate_result(inv, marker))
        out = _finish(inv)
    return out
