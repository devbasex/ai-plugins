"""プランの実行の回を、1 つの実行の鍵で DBOS の殻を通して流す（B1・B2・B4）。

- 実行の回の記録 `run.json`（状態の置き場）が鍵を持つ。同じプラン・開始点で打ち直すと、回が終わるまで同じ鍵で続く。
  終わった回は、head が回の初めか終わりの head と同じなら同じ鍵で、結果を返して流さない（B4）。head が違えば
  （差し戻しの後に直した）次の回の鍵で流す（#3176）
- 耐久ワークフロー `plan_workflow` は遷移の判断（`transition_decide.decide`）と手の実行を判断のループで回す。
  ステップは耐久ステップで打ち、打ち直しの待ちと承認の答えの待ちは耐久の `recv` で待つ
- `next` は状態の置き場のロック（`plan.lock`）を持って回を選び、流す。同じプランの別の回を同時に打つと、後の `next` は
  先の回が終わるか承認ゲートで抜けるまで上限まで待つ。ステップの打ち直しが、別の回の流れている子を前の回の残りとして
  止めない
- 遷移の判断が行き先を選ぶたびに、選択の記録（`ndf.contracts.chosen`）を状態の置き場の `progress.jsonl` へ追記する。
  書くのは耐久ステップで、回復で判断を流し直しても同じ選択を 2 回書かない
- ステップの結果 JSON の `outputs` を回をまたいで `outputs.json` に重ね（後のステップの値が勝つ）、プランの結果の `outputs` に載せる。
  `outputs.gaps` が空でなければ、最後まで ok で流れてもプランの結果は incomplete でその欠けを持つ（レビューの欠けを
  CI の後まで引き継ぐ）
- 承認の答えを待つ所に来たら、待ちの目印 `waiting-<鍵の名前>.json` を書く。呼び手は目印を見て承認ゲートの結果を出し、
  DBOS を閉じずにプロセスを抜ける（待っている耐久ワークフローは鍵のファイルに残り、次の起動が回復する。B2）
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ndf import cli
from ndf.adapters import git
from ndf.contracts import chosen, limits, result, routing
from ndf.contracts.exit_codes import GATE, OK, STOPPED
from ndf.core import durable, durable_locks, loop
from ndf.core.plan import assemble_decide, model, queue_decide, steps, transition_decide
from ndf.core.plan.files import read_json, write_json
from ndf.core.plan.records import PROGRESS_FILE, _add_outputs, _add_prs, recorded_outputs, recorded_prs
from ndf.core.plan.transition_decide import ANSWERED, SLEPT, AwaitAnswer, Outcome, RunStep, Sleep

# 結果 JSON の tool の名前は副命令の名前（副命令の表から引く）
TOOL = cli.tool_of(f"{__package__}.cli")
CLI_NAME = result.ENTRY
RUN_FILE = "run.json"
# 状態の置き場を 1 つの `next` に限るロック（回を選ぶ・ステップの子を打つ間）
PLAN_LOCK = "plan.lock"
RESULTS_DIR = "results"
# 差し込み `{work_base}` の置き場（状態の置き場の隣。git の共通ディレクトリの中で、リポジトリと同じボリュームにある）
WORK_DIR = "work"
WAITING_PREFIX = "waiting-"
TOPIC_ANSWER = "answer"
TOPIC_RETRY = "retry-wait"
# 承認の答えを 1 回に待つ秒。期限で答えが無ければ待ち直す（答えの来ない承認ゲートを打ち切らない）
ANSWER_WAIT_SECONDS = 86400.0
POLL = durable.POLL


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
    digest: str = ""

    @property
    def run_id(self) -> str:
        """回の鍵の名前（`durable.run_name`）。"""
        return durable.run_name(self.key)

    @property
    def workflow_id(self) -> str:
        return f"{self.run_id}-plan"

    @property
    def waiting_path(self) -> Path:
        return waiting_path(self.state_dir, self.run_id)

    @property
    def result_path(self) -> Path:
        return result_path(self.state_dir, self.run_id)


def waiting_path(state_dir: Path, run: str) -> Path:
    """回 `run`（鍵の名前）の承認の答えを待つ目印。"""
    return state_dir / f"{WAITING_PREFIX}{run}.json"


def result_path(state_dir: Path, run: str) -> Path:
    """回 `run`（鍵の名前）の結果の控え。"""
    return state_dir / RESULTS_DIR / f"{run}.json"


# --- 耐久ワークフロー ---------------------------------------------------------------------


@durable.step
def run_step(spec: dict[str, Any], step_id: str, attempt: int) -> dict[str, Any]:
    """ステップを 1 回打つ耐久ステップ。済んだ呼び出しは回復のときに打ち直さない。"""
    plan = model.parse(spec["plan"])
    ctx = steps.StepContext(plan, Path(spec["state_dir"]), dict(spec["values"]))
    out = steps.run(ctx, plan.step(step_id))
    out["attempt"] = attempt
    if out["prs"]:
        _add_prs(ctx.state_dir, out["prs"])
    if out["outputs"]:
        _add_outputs(ctx.state_dir, out["outputs"])
    return out


@durable.step
def record_choices(path: str, run_id: str, choices: list[list[Any]]) -> int:
    """選択の記録を 1 件ずつ進捗記録へ追記する（耐久ステップ。回復で同じ選択を 2 回書かない）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [result.dumps_line(chosen.record(run_id, *c)) for c in choices]
    with target.open("a", encoding="utf-8") as fh:
        fh.write("".join(f"{line}\n" for line in lines))
    return len(lines)


@durable.step
def mark_waiting(path: str, step_id: str, seq: int, routes: dict[str, dict[str, Any]]) -> None:
    """承認の答えを待つ目印を書く（呼び手と `wait` が読む）。"""
    write_json(Path(path), {"step": step_id, "seq": seq, "routes": routes})


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


def _decide_and_record(spec: dict[str, Any], plan: model.Plan, state: Any, done: Optional[Outcome]) -> loop.Next:
    """遷移の判断を呼び、判断が足した選択を選択の記録として書く。"""
    nxt = transition_decide.decide(plan, state, done)
    added = nxt.state.choices[len(state.choices) :]
    if added:
        record_choices(spec["progress"], spec["run_id"], [list(c) for c in added])
    return nxt


@durable.workflow
def plan_workflow(spec: dict[str, Any]) -> dict[str, Any]:
    """プランの遷移を判断のループで回し、終わりの状態を返す。"""
    plan = model.parse(spec["plan"])
    state = transition_decide.start_state(plan, spec["start"], spec["prelude"])
    final = loop.run(functools.partial(_decide_and_record, spec, plan), functools.partial(_perform, spec), state)
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


def _again(plan_path: Path, start: Optional[str], state_dir: Optional[str], sets: Mapping[str, str]) -> tuple[str, ...]:
    args = [str(plan_path)]
    if start is not None:
        args += ["--from", start]
    if state_dir is not None:
        args += ["--state-dir", state_dir]
    for name in sorted(sets):
        args += ["--set", f"{name}={sets[name]}"]
    return tuple(args)


def _with_start(again: tuple[str, ...], start: str) -> list[str]:
    """打ち直しの引数の開始点を `start` に置き換える（状態の置き場と差し込みの値は保つ）。"""
    args = list(again)
    if "--from" in args:
        at = args.index("--from")
        del args[at : at + 2]
    return [args[0], "--from", start, *args[1:]]


def _prelude(plan: model.Plan, start: Optional[str]) -> tuple[str, ...]:
    if start is None or start in routing.PLAN_TARGETS:
        return ()
    return queue_decide.resume_prelude(plan.order, start)


def resolve_state_dir(plan_path: Path, given: Optional[str], plan: Optional[model.Plan], cwd: Path) -> Path:
    """状態の置き場。`given`（`--state-dir`）が無ければプランの `state_dir` を使う（`plan` はそのときだけ要る）。"""
    found = given if given is not None else plan.state_dir
    return Path(assemble_decide.state_dir_for(str(plan_path), found, str(cwd)))


def state_dir_of(plan_path: Path, state_dir: Optional[str], cwd: Path) -> Path:
    """プランの状態の置き場（`prepare` と同じ決め方）。プランを読めなければ `model.PlanError`。"""
    plan = model.load(plan_path) if state_dir is None else None
    return resolve_state_dir(plan_path, state_dir, plan, cwd)


@contextmanager
def plan_lock(state_dir: Path) -> Iterator[Path]:
    """状態の置き場のロック。上限まで待ち、取れなければ持ち主（ホスト・pid・時刻）を添えて `durable.StoreInUse`（B11）。"""
    with durable_locks.exclusive_lock(state_dir / PLAN_LOCK) as path:
        yield path


def _base_values(plan: model.Plan, state_dir: Path, absolute_plan: Path, work_base: Path) -> dict[str, str]:
    """決まった名前の差し込みの値（呼び手の `sets` で上書きできない）。"""
    return {
        "state_dir": str(state_dir),
        "repo": plan.repo if plan.repo else "",
        "worktree": plan.worktree if plan.worktree else "",
        "plan": str(absolute_plan),
        "owner": assemble_decide.owner_of(plan.name, str(state_dir)),
        "work_base": str(work_base),
    }


def _merge_values(base: dict[str, str], sets: Mapping[str, str]) -> dict[str, str]:
    """決まった名前の値に呼び手の値を足す。決まった名前を上書きする値があれば `model.PlanError`。"""
    clash = sorted(set(sets) & set(base))
    if clash:
        raise model.PlanError(f"差し込みの値 {', '.join(clash)} は上書きできない")
    return {**base, **sets}


def prepare(
    root: Path, plan_path: Path, start: Optional[str], state_dir: Optional[str], cwd: Path, sets: Optional[Mapping[str, str]] = None
) -> Invocation:
    """プランを読み、状態の置き場・鍵・差し込みの値を決める。誤りは `model.PlanError`（開始点の誤りを含む）。

    `sets` は呼び手が足す差し込みの値（キューの `{queue_prs}` など）。決まった名前（`state_dir` など）は上書きできない。
    `{work_base}` はステップが作業ディレクトリを作る置き場。`/tmp` でなくリポジトリと同じボリューム（git の共通ディレクトリの
    中）に置き、Docker のデーモンがホストにある環境（DooD）でも compose の bind mount から見える（#2933）
    """
    sets = dict(sets) if sets is not None else {}
    plan = model.load(plan_path)
    digest = assemble_decide.run_digest(plan.digest(), sets)
    if start is not None and start not in plan.order and start not in routing.PLAN_TARGETS:
        raise model.PlanError(f"{plan.name} に {start} は無い")
    sdir = resolve_state_dir(plan_path, state_dir, plan, cwd)
    head = _head(plan)
    record = read_json(sdir / RUN_FILE)
    finished = record is not None and _finished(root, record["key"])
    choice = assemble_decide.choose_run(record, plan.name, digest, start, head, finished)
    abs_plan = (cwd / plan_path) if not plan_path.is_absolute() else plan_path
    values = _merge_values(_base_values(plan, sdir, abs_plan, root.parent / WORK_DIR), sets)
    again = _again(abs_plan, start, state_dir, sets)
    return Invocation(abs_plan, plan, start, _prelude(plan, start), sdir, choice.key, choice.round_no, head, values, again, digest)


# --- 実行の回 -----------------------------------------------------------------------------


def _finished(root: Path, key: str) -> bool:
    """鍵の回が終わって退避されたか（retired/ に鍵のファイルがある）。"""
    name = durable.run_name(key)
    return any((root / durable.DBOS_DIR / durable.RETIRED_DIR).glob(f"{name}*{durable.DB_SUFFIX}"))


def _routes(inv: Invocation) -> dict[str, dict[str, dict[str, Any]]]:
    """承認の答えを待つステップごとの、答え → 次に打つコマンド（結果 JSON の command の形）。"""
    return {s.id: {a: result.command(TOOL, "next", *inv.again, "--answer", a) for a in s.on_answer} for s in inv.plan.steps if s.on_answer}


def _spec(inv: Invocation) -> dict[str, Any]:
    return {
        "plan": dict(inv.plan.raw),
        "start": inv.start,
        "prelude": list(inv.prelude),
        "state_dir": str(inv.state_dir),
        "values": inv.values,
        "waiting": str(inv.waiting_path),
        "routes": _routes(inv),
        "progress": str(inv.state_dir / PROGRESS_FILE),
        "run_id": inv.run_id,
    }


def _record_run(inv: Invocation, end_head: Optional[str] = None) -> None:
    path = inv.state_dir / RUN_FILE
    record = read_json(path)
    heads = list(record["heads"]) if record is not None and record["key"] == inv.key else []
    heads += [h for h in (inv.head, end_head) if h is not None and h not in heads]
    write_json(
        path,
        {"key": inv.key, "name": inv.plan.name, "digest": inv.digest, "start": inv.start, "round": inv.round_no, "heads": heads},
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


def _pr_items(state_dir: Path) -> list[dict[str, Any]]:
    """プランのステップが載せた PR の、結果の items の行（載った順）。"""
    return [{"kind": "pr", "number": n} for n in recorded_prs(state_dir)]


def _run_item(inv: Invocation) -> dict[str, Any]:
    """終わった回の、結果の items の先頭の行。"""
    return {"kind": "plan", "name": inv.plan.name, "run": inv.run_id}


def gate_result(inv: Invocation, marker: dict[str, Any]) -> dict[str, Any]:
    """承認の答えを待つ結果。`routes` は答えごとに打つコマンド。"""
    summary = f"{inv.plan.name} のステップ {marker['step']} が承認の答えを待っている"
    items = [{"kind": "plan", "name": inv.plan.name, "step": marker["step"], "gate": True}, *_pr_items(inv.state_dir)]
    return result.result(
        TOOL,
        GATE,
        summary,
        items,
        {"round": inv.round_no},
        routes=marker["routes"],
        next=result.command(TOOL, "wait", str(inv.plan_path), "--state-dir", str(inv.state_dir)),
    )


def _finished_result(
    inv: Invocation, items: list[dict[str, Any]], metrics: dict[str, Any], outputs: Optional[dict[str, Any]], shared: dict[str, Any]
) -> dict[str, Any]:
    """ok で終わった回の結果。ステップが残した欠け（outputs の gaps）があれば incomplete で止まる。"""
    gaps = outputs.get("gaps") if outputs else None
    if gaps:
        detail = f"{inv.plan.name} は終わったが、ステップが残した欠け {len(gaps)} 件がある"
        return result.stopped(TOOL, "incomplete", detail, items=items, metrics=metrics, gaps=gaps, **shared)
    return result.result(TOOL, OK, f"{inv.plan.name} が終わった", items, metrics, **shared)


def final_result(inv: Invocation, final: Optional[dict[str, Any]], status: Optional[str]) -> dict[str, Any]:
    """終わった回の結果。耐久ワークフローが例外で終わったときは unknown で止まる。"""
    items = [_run_item(inv), *_pr_items(inv.state_dir)]
    metrics = {"round": inv.round_no, "steps": final["count"] if final else 0}
    outputs, warnings = recorded_outputs(inv.state_dir)
    shared = {"outputs": outputs, "warnings": warnings, "run_id": inv.run_id}
    if final is None:
        detail = f"{inv.plan.name} の耐久ワークフローが {status} で終わった"
        return result.stopped(TOOL, "unknown", detail, items=items, metrics=metrics, **shared)
    if final["status"] == OK:
        return _finished_result(inv, items, metrics, outputs, shared)
    if final["status"] == STOPPED:
        items[0]["step"] = final["last"]
        again = result.command(TOOL, "next", *_with_start(inv.again, final["last"])) if final["last"] else None
        return result.stopped(TOOL, final["kind"], final["detail"], items=items, metrics=metrics, next=again, **shared)
    after = inv.plan.following(final["last"])
    cont = result.command(TOOL, "next", *_with_start(inv.again, after)) if after else None
    return result.result(TOOL, GATE, final["detail"], items, metrics, routes={"approved": cont, "rejected": None}, **shared)


def _stored(inv: Invocation) -> dict[str, Any]:
    found = read_json(inv.result_path)
    if found is not None:
        return found
    items = [_run_item(inv)]
    return result.result(TOOL, OK, f"{inv.plan.name} の回は終わっている（結果の控えが無い）", items, {"round": inv.round_no})


def _finish(inv: Invocation) -> dict[str, Any]:
    status = durable.status(inv.workflow_id)
    final = durable.result(inv.workflow_id) if status == "SUCCESS" else None
    out = final_result(inv, final, status)
    write_json(inv.result_path, out)
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
