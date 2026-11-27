"""プランの実行の回を、1 つの実行のキーで DBOS の殻を通して流す（B1・B2・B4）。

- 実行の回の記録 `run.json`（状態の保存先）がキーを持つ。同じプラン・開始点で打ち直すと、回が終わるまで同じキーで続く。
  終わった回は、head が回の初めか終わりの head と同じなら同じキーで、結果を返して流さない（B4）。head が違えば
  （変更要求の後に直した）次の回のキーで流す（#3176）
- 耐久ワークフロー `plan_workflow` は遷移の判断（`transition_decide.decide`）と手の実行を判断のループで回す。
  ステップは耐久ステップで打ち、打ち直しの待ちと承認の回答の待ちは耐久の `recv` で待つ
- `next` は状態の保存先のロック（`plan.lock`）を持って回を選び、流す。同じプランの別の回を同時に打つと、後の `next` は
  先の回が終わるか承認ゲートで抜けるまで上限まで待つ。ステップの打ち直しが、別の回の流れている子を前の回の残りとして
  止めない
- 遷移の判断がルートを選ぶたびに、選択の記録（`ndf.contracts.chosen`）を状態の保存先の `progress.jsonl` へ追記する。
  書くのは耐久ステップで、回復で判断を流し直しても同じ選択を 2 回書かない
- ステップの結果 JSON の `outputs` を回をまたいで `outputs.json` に重ね（後のステップの値が勝つ）、プランの結果の `outputs` に載せる。
  `outputs.gaps` が空でなければ、最後まで ok で流れてもプランの結果は incomplete でその欠けを持つ（レビューの欠けを
  CI の後まで引き継ぐ）
- 承認の回答を待つ所に来たら、待ちの目印 `waiting-<キーの名前>.json` を書く。呼び手は目印を見て承認ゲートの結果を出し、
  DBOS を閉じずにプロセスを抜ける（待っている耐久ワークフローはキーのファイルに残り、次の起動が回復する。B2）
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import cli
from ndf.adapters import git
from ndf.contracts import chosen, limits, result, routing, worker_kinds, worker_paths
from ndf.contracts.exit_codes import GATE, OK, STOPPED
from ndf.core import actors, durable, durable_locks, loop
from ndf.core.plan import assemble_decide, model, queue_decide, steps, transition_decide
from ndf.core.plan.files import read_json, write_json
from ndf.core.plan.records import (
    PROGRESS_FILE,
    _add_outputs,
    _add_prs,
    _keep_step_gaps,
    read_result,
    recorded_outputs,
    recorded_prs,
    recorded_step_gaps,
)
from ndf.core.plan.transition_decide import ANSWERED, SLEPT, AwaitAnswer, Outcome, RunStep, Sleep

# 結果 JSON の tool の名前はサブコマンドの名前（サブコマンドの表から引く）
TOOL = cli.tool_of(f"{__package__}.cli")
# 承認した HEAD・マージ先が変わったときの停止理由の語
APPROVAL_STALE = "approval_stale"
CLI_NAME = result.ENTRY
RUN_FILE = "run.json"
# 状態の保存先を 1 つの `next` に限るロック（回を選ぶ・ステップの子を打つ間）
PLAN_LOCK = "plan.lock"
RESULTS_DIR = "results"
# 差し込み `{work_base}` の保存先（状態の保存先の隣。git の共通ディレクトリの中で、リポジトリと同じボリュームにある）
WORK_DIR = "work"
WAITING_PREFIX = "waiting-"
# 承認待ちへの回答の記録と、承認待ちで止まったステップの提示の識別子の写しの保存先（状態の保存先の下）
ANSWERS_DIR = "answer-records"
PRESENTED_DIR = "presented"
# 回答コマンドが固定する待ちと提示の識別子の引数（`ndf plan next` が受ける）
WAITING_FLAG = "--waiting"
# 承認待ちのステップの提示の写しの欄（提示の識別子・承認資料のパス・本番へ反映され得るか）
SHOWN_KEYS = ("presented_id", "presentation_path", "production")
PRESENTED_FLAG = "--presented"
# 旧い形の目印で、同じ回の提示を確かめられなかったことを示すキー（`read_marker` が足す。目印のファイルには書かない）
UNCONFIRMED = "unconfirmed"
TOPIC_ANSWER = "gate-answer"
TOPIC_RETRY = "retry-wait"
# 承認の回答を 1 回に待つ秒の方針（contracts/limits/）。期限で回答が無ければ待ち直す（回答の来ない承認ゲートを打ち切らない）
ANSWER_WAIT = "supervise.answer-wait"
POLL = durable.POLL


@dataclass(frozen=True)
class RunOptions:
    """`prepare` の選択肢。`sets` は呼び手が足す差し込みの値、`fresh` は前の回に戻らず新しい回を選ぶ（#1592）。"""

    sets: Mapping[str, str] = field(default_factory=dict)
    fresh: bool = False


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
        """回のキーの名前（`durable.run_name`）。"""
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
    """回 `run`（キーの名前）の承認の回答を待つ目印。"""
    return state_dir / f"{WAITING_PREFIX}{run}.json"


def _pick_shown(source: dict[str, Any]) -> dict[str, Any]:
    """`source` から提示の写しの欄（`SHOWN_KEYS`）を抜き出す。無い欄は None。"""
    return {k: source.get(k) for k in SHOWN_KEYS}


def read_marker(state_dir: Path, run: str) -> Optional[dict[str, Any]]:
    """回 `run` の承認の回答を待つ目印。無ければ None。

    回の id か提示の写しを持たない旧い形の目印（この形より前のバージョンが書いた目印）は、目印のファイルの回 `run` で補う。
    提示の写しは、状態の保存先に残った待っているステップの写しのうち、同じ回 `run` が書いたと確かめられるものだけで補う。
    同じ保存先で `--fresh` から流した別の回が同じステップの写しを上書きし得るため、確かめられなければ提示を空にし、
    `UNCONFIRMED` を真にする（`advance` はこの待ちへの回答を記録も送信もせずに止め、待ちを残す）。
    """
    marker = read_json(waiting_path(state_dir, run))
    if marker is None:
        return None
    if "run" not in marker:
        marker = {**marker, "run": run}
    if not isinstance(marker.get("shown"), dict):
        saved = read_json(step_presented_path(state_dir, str(marker["step"])))
        if isinstance(saved, dict) and saved.get("run") == run:
            marker = {**marker, "shown": _pick_shown(saved)}
        else:
            marker = {**marker, "shown": {}, UNCONFIRMED: True}
    return marker


def is_unconfirmed(marker: dict[str, Any]) -> bool:
    """旧い形の目印で、待ちと同じ回が書いた提示を確かめられなかったか（`read_marker`）。"""
    return marker.get(UNCONFIRMED) is True


def result_path(state_dir: Path, run: str) -> Path:
    """回 `run`（キーの名前）の結果のバックアップ。"""
    return state_dir / RESULTS_DIR / f"{run}.json"


def answer_path(state_dir: Path, step: str) -> Path:
    """承認待ちのステップ `step` への回答の記録（`ndf ci answer` が読む。#4409 の決定 7）。"""
    return state_dir / ANSWERS_DIR / f"{step}.json"


def step_presented_path(state_dir: Path, step: str) -> Path:
    """承認待ちで止まったステップ `step` の提示の識別子（ステップの結果の `metrics.presented_id`）の写し。"""
    return state_dir / PRESENTED_DIR / f"{step}.json"


def stored_result(state_dir: Path, run: str) -> Optional[dict[str, Any]]:
    """回 `run`（キーの名前）の結果のバックアップ（今の形）。終わっていなければ None、新しいバージョンなら `InternalError`。"""
    return read_result(result_path(state_dir, run))


# --- 耐久ワークフロー ---------------------------------------------------------------------


@durable.step
def run_step(spec: dict[str, Any], step_id: str, attempt: int) -> dict[str, Any]:
    """ステップを 1 回打つ耐久ステップ。済んだ呼び出しは回復のときに打ち直さない。

    承認待ちで止まれば提示の写しを、書いた回の id と組で残す（旧い形の目印を補うとき、同じ回の写しかを確かめる）。
    """
    plan = model.parse(spec["plan"])
    ctx = steps.StepContext(plan, Path(spec["state_dir"]), dict(spec["values"]))
    out = steps.run(ctx, plan.step(step_id))
    out["attempt"] = attempt
    if out["status"] == GATE:
        shown = _pick_shown(out)
        write_json(step_presented_path(ctx.state_dir, step_id), {"step": step_id, "run": spec.get("run_id"), **shown})
    if out["prs"]:
        _add_prs(ctx.state_dir, out["prs"])
    if out["outputs"]:
        _add_outputs(ctx.state_dir, out["outputs"])
    _keep_step_gaps(ctx.state_dir, step_id, out["gaps"])
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
def mark_waiting(path: str, step_id: str, seq: int, routes: dict[str, dict[str, Any]], run_id: str, state_dir: str) -> None:
    """承認の回答を待つ目印を書く（呼び手と `wait` が読む）。

    回の id と、待っているステップの提示の写し（提示の識別子・承認資料のパス・本番へ反映され得るか）を同じ目印に入れる。
    結果の資料・回答コマンドと、回答を照らす値をこの一組から作り、別々に読んだ写しを混ぜない（#4409）。
    """
    shown = read_json(step_presented_path(Path(state_dir), step_id))
    held = _pick_shown(shown) if isinstance(shown, dict) else {}
    write_json(Path(path), {"step": step_id, "seq": seq, "run": run_id, "shown": held, "routes": routes})


@durable.step
def clear_waiting(path: str) -> None:
    """回答を受けたので待ちの目印を消す。"""
    Path(path).unlink(missing_ok=True)


def _perform(spec: dict[str, Any], act: Any) -> Outcome:
    """手を実行する。ステップは耐久ステップ、待ちは耐久の recv で行う。"""
    if isinstance(act, RunStep):
        out = run_step(spec, act.step, act.attempt)
        return Outcome(out["status"], out["kind"], out["retry_after"], summary=out["summary"])
    if isinstance(act, Sleep):
        durable.recv(TOPIC_RETRY, act.seconds)
        return Outcome(SLEPT)
    if isinstance(act, AwaitAnswer):
        mark_waiting(spec["waiting"], act.step, act.seq, spec["routes"][act.step], spec["run_id"], spec["state_dir"])
        answer = durable.recv(TOPIC_ANSWER, limits.seconds(ANSWER_WAIT))
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
    except git.GitError:  # noqa: NDF401 外部コマンド: head の無い保存先は head をキーに入れない
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
    """打ち直しの引数の開始点を `start` に置き換える（状態の保存先と差し込みの値は保つ）。"""
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
    """状態の保存先。`given`（`--state-dir`）が無ければプランの `state_dir` を使う（`plan` はそのときだけ要る）。"""
    found = given if given is not None else plan.state_dir
    return Path(assemble_decide.state_dir_for(str(plan_path), found, str(cwd)))


def state_dir_of(plan_path: Path, state_dir: Optional[str], cwd: Path) -> Path:
    """プランの状態の保存先（`prepare` と同じ決め方）。プランを読めなければ `model.PlanError`。"""
    plan = model.load(plan_path) if state_dir is None else None
    return resolve_state_dir(plan_path, state_dir, plan, cwd)


@contextmanager
def plan_lock(state_dir: Path) -> Iterator[Path]:
    """状態の保存先のロック。上限まで待ち、取れなければオーナー（ホスト・pid・時刻）を添えて `durable.StoreInUse`（B11）。"""
    with durable_locks.exclusive_lock(state_dir / PLAN_LOCK) as path:
        yield path


def _base_values(plan: model.Plan, state_dir: Path, absolute_plan: Path, work_base: Path) -> dict[str, str]:
    """決まった名前の差し込みの値（呼び手の `sets` で上書きできない）。"""
    return {
        "state_dir": str(state_dir),
        "repo": plan.repo if plan.repo else "",  # noqa: NDF501 ファイル: 計画の repo が無ければ空の差し込み
        "worktree": plan.worktree if plan.worktree else "",  # noqa: NDF501 ファイル: 計画の worktree が無ければ空の差し込み
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
    root: Path,
    plan_path: Path,
    start: Optional[str],
    state_dir: Optional[str],
    cwd: Path,
    options: Optional[RunOptions] = None,
) -> Invocation:
    """プランを読み、状態の保存先・キー・差し込みの値を決める。誤りは `model.PlanError`（開始点の誤りを含む）。

    `options.sets` は呼び手が足す差し込みの値（キューの `{queue_prs}` など）。決まった名前（`state_dir` など）は上書きできない。
    `{work_base}` はステップが作業ディレクトリを作る保存先。`/tmp` でなくリポジトリと同じボリューム（git の共通ディレクトリの
    中）に置き、Docker のデーモンがホストにある環境（DooD）でも compose の bind mount から見える（#2933）
    `options.fresh` は前の回に戻らず、耐久の記録の保存先に無い番号の回を選ぶ（#1592）。打ち直しの引数（`again`）には入れず、
    承認の回答はその回へ届く
    """
    opts = options if options is not None else RunOptions()
    sets = dict(opts.sets)
    plan = model.load(plan_path)
    digest = assemble_decide.run_digest(plan.digest(), sets)
    if start is not None and start not in plan.order and start not in routing.PLAN_TARGETS:
        raise model.PlanError(f"{plan.name} に {start} は無い")
    sdir = resolve_state_dir(plan_path, state_dir, plan, cwd)
    head = _head(plan)
    record = read_json(sdir / RUN_FILE)
    finished = record is not None and _finished(root, record["key"])
    choice = assemble_decide.choose_run(
        record,
        assemble_decide.RunPlan(plan.name, digest, start),
        head,
        finished,
        fresh=opts.fresh,
        taken=lambda key: _recorded_key(root, key),
    )
    abs_plan = (cwd / plan_path) if not plan_path.is_absolute() else plan_path
    values = _merge_values(_base_values(plan, sdir, abs_plan, root.parent / WORK_DIR), sets)
    again = _again(abs_plan, start, state_dir, sets)
    return Invocation(abs_plan, plan, start, _prelude(plan, start), sdir, choice.key, choice.round_no, head, values, again, digest)


# --- 実行の回 -----------------------------------------------------------------------------


def _finished(root: Path, key: str) -> bool:
    """キーの回が終わって退避されたか（retired/ にキーのファイルがある）。"""
    name = durable.run_name(key)
    return any((root / durable.DBOS_DIR / durable.RETIRED_DIR).glob(f"{name}*{durable.DB_SUFFIX}"))


def _recorded_key(root: Path, key: str) -> bool:
    """キーの記録（DB・ロックのファイル）が耐久の記録の保存先か retired/ にあるか。"""
    name = durable.run_name(key)
    dbos = root / durable.DBOS_DIR
    return any(dbos.glob(f"{name}*")) or any((dbos / durable.RETIRED_DIR).glob(f"{name}*"))


def _routes(inv: Invocation) -> dict[str, dict[str, dict[str, Any]]]:
    """承認の回答を待つステップごとの、回答 → 次に打つコマンド（結果 JSON の command の形）。"""
    return {
        s.id: {a: result.command(TOOL, "next", *inv.again, "--answer", a, "--by", actors.HUMAN) for a in s.on_answer}
        for s in inv.plan.steps
        if s.on_answer
    }


def waiting_token(marker: dict[str, Any]) -> str:
    """待ちの目印の識別子（`<回の id>:<ステップ>:<待ちの番号>`）。回答コマンドの `--waiting` に載せ、回答のときに今の待ちと照らす。

    回の id を含めるので、同じ保存先で `--fresh` から流した別の回の待ちと、ステップと番号が同じでも取り違えない。
    """
    return f"{marker.get('run')}:{marker['step']}:{marker['seq']}"


def shown_of(marker: dict[str, Any]) -> dict[str, Any]:
    """待ちの目印に入れた提示の写し（提示の識別子・承認資料のパス・本番へ反映され得るか）。無ければ空。"""
    shown = marker.get("shown")
    return shown if isinstance(shown, dict) else {}


def presented_of(marker: dict[str, Any]) -> Optional[str]:
    """待ちの目印の提示の識別子。提示が無ければ None。"""
    presented = shown_of(marker).get("presented_id")
    return presented if isinstance(presented, str) and presented else None


def presented_routes(marker: dict[str, Any]) -> dict[str, Any]:
    """待ちの目印の routes に、今の待ち（`--waiting`）と提示の識別子（`--presented`。提示があれば）を足す。

    利用者に示した回答コマンドを、示した待ちと資料に固定する（前の承認コマンドを再送しても次の承認待ちへ届かない。#4409）。
    """
    pin = [WAITING_FLAG, waiting_token(marker)]
    presented = presented_of(marker)
    if presented is not None:
        pin += [PRESENTED_FLAG, presented]
    routes: dict[str, Any] = {}
    for answer, cmd in dict(marker["routes"]).items():
        routes[answer] = None if cmd is None else {**cmd, "argv": [*cmd["argv"], *pin]}
    return routes


def presentation_of(marker: dict[str, Any]) -> tuple[Optional[str], Optional[bool]]:
    """待ちの目印の承認資料のパスと「本番へ反映され得るか」。示していなければ None。"""
    shown = shown_of(marker)
    material = shown.get("presentation_path")
    production = shown.get("production")
    return (material if isinstance(material, str) and material else None), (production if isinstance(production, bool) else None)


def presentation_fields(marker: dict[str, Any]) -> dict[str, Any]:
    """`presentation_of` の値を、結果の行へそのまま足す形（示していない値は持たない）。"""
    material, production = presentation_of(marker)
    fields: dict[str, Any] = {"presentation_path": material} if material is not None else {}
    return fields if production is None else {**fields, "production": production}


class Reply(NamedTuple):
    """承認待ちへの回答。回答した者（`by`）と、回答コマンドが固定した待ち（`--waiting`）と提示の識別子（`--presented`）を持つ。"""

    answer: str
    by: Optional[str] = None
    waiting: Optional[str] = None
    presented: Optional[str] = None


def _pin_mismatch(baseline: dict[str, Any], reply: Reply) -> Optional[str]:
    """回答コマンドの固定が今の待ちと提示に合わなければ理由。利用者の回答は、提示があれば提示の識別子を必ず照らす。"""
    if reply.waiting is not None and reply.waiting != waiting_token(baseline):
        return f"回答した待ち {reply.waiting} は今の待ち {waiting_token(baseline)} でない"
    presented = presented_of(baseline)
    if presented is None:
        return None
    human = reply.by == actors.HUMAN
    if reply.presented is None and not human:
        return None
    if reply.presented != presented:
        return f"回答した提示 {reply.presented or '（無い）'} は今の提示でない"
    return "回答した待ち（--waiting）が無い" if reply.waiting is None and human else None


def _unconfirmed_stop(inv: Invocation, baseline: dict[str, Any]) -> dict[str, Any]:
    """同じ回の提示を確かめられない旧い形の待ち（`unconfirmed`）への回答を、記録も送信もせずに止める結果。

    待ちは解かず、ステップも打ち直さない。プランのステップは任意のコマンドで、資料だけを作り直す一般の経路が無く、
    打ち直すとマージのステップが資料を出す前にマージまで進み得る。そのため打ち直しのコマンドは示さず、停止理由だけを返す。
    待ちの確かめ（`--waiting`）は先に済ませてある。
    """
    step = str(baseline["step"])
    detail = (
        f"{inv.plan.name} のステップ {step} の待ちは更新前のバージョンが残したもので、示した資料がこの待ちのものか確かめられないため、"
        "回答を記録しない。資料だけを作り直す経路が無く、ステップを打ち直すとマージなどの操作が資料を出す前に進み得るので、"
        "打ち直しは案内しない。承認の対象を人が確かめ、プランをどう進めるかを決める"
    )
    return result.stopped(TOOL, "precondition", detail)


def _deliver(inv: Invocation, baseline: Optional[dict[str, Any]], reply: Reply) -> Optional[dict[str, Any]]:
    """待っている回へ回答を送る。送る前に照らして回答の記録を書き、送れないときは止まる結果を返す（送れば None）。"""
    if baseline is None:
        return result.stopped(TOOL, "invalid", f"{inv.plan.name} は承認の回答を待っていない")
    if reply.by is None:
        detail = f"{inv.plan.name} への回答に回答した者（--by）が無い。返された経路（--answer <回答> --by human）で打ち直す"
        return result.stopped(TOOL, "invalid", detail)
    mismatch = _pin_mismatch(baseline, reply)
    if mismatch is not None:
        detail = f"{inv.plan.name} への回答を記録しない: {mismatch}（ndf plan wait で今の承認待ちと資料を読み直して回答する）"
        return result.stopped(TOOL, APPROVAL_STALE, detail)
    if is_unconfirmed(baseline):
        return _unconfirmed_stop(inv, baseline)
    try:
        _record_answer(inv, baseline, reply.answer, reply.by)
    except OSError as exc:
        return result.stopped(TOOL, "precondition", f"{inv.plan.name} の回答の記録を書けない: {exc}")
    durable.send(inv.workflow_id, reply.answer, TOPIC_ANSWER)
    return None


def _record_answer(inv: Invocation, baseline: dict[str, Any], answer: str, by: str) -> None:
    """待っていたステップへの回答の記録を書く（ステップ・回答・回答した者・時刻・回の id・提示の識別子）。

    提示の識別子は、待ちの目印に入れた提示の写し（回答を照らしたのと同じ一組）から写す。無ければ null（`ndf ci answer` が断る）。
    一時ファイルから置き換えて書く。書けなければ `OSError`。
    """
    step = str(baseline["step"])
    record = {
        "step": step,
        "answer": answer,
        "by": by,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run": inv.run_id,
        "presented_id": presented_of(baseline),
    }
    write_json(answer_path(inv.state_dir, step), record)


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
        marker = read_marker(inv.state_dir, inv.run_id)
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
    """承認の回答を待つ結果。`routes` は回答ごとに打つコマンド。

    待っているステップが承認資料（`presentation_path`）と「本番へ反映され得るか」（`metrics.production`）を出していれば、
    そのまま載せる（承認する人が資料を開き、本番へ反映されるマージかを知ってから回答する。#4409）。
    """
    summary = f"{inv.plan.name} のステップ {marker['step']} が承認の回答を待っている"
    items = [{"kind": "plan", "name": inv.plan.name, "step": marker["step"], "gate": True}, *_pr_items(inv.state_dir)]
    material, production = presentation_of(marker)
    metrics: dict[str, Any] = {"round": inv.round_no}
    if production is not None:
        metrics["production"] = production
    return result.result(
        TOOL,
        GATE,
        summary,
        items,
        metrics,
        routes=presented_routes(marker),
        next=result.command(TOOL, "wait", str(inv.plan_path), "--state-dir", str(inv.state_dir)),
        presentation_path=material,
    )


@dataclass(frozen=True)
class _FinalRecords:
    """終わった回の結果に共通の値。`outputs` はステップの outputs（gaps の判断に使う）、`shared` は結果へそのまま載せる値。

    `step_gaps` は止まったステップごとの欠け（ステップの結果 JSON の gaps）で、incomplete で止まった回の詳細に載せる。
    """

    items: list[dict[str, Any]]
    metrics: dict[str, Any]
    outputs: Optional[dict[str, Any]]
    shared: dict[str, Any]
    step_gaps: Mapping[str, list[dict[str, Any]]] = field(default_factory=dict)


def _stop_gaps(inv: Invocation, final: dict[str, Any], rec: _FinalRecords) -> Optional[list[dict[str, Any]]]:
    """止まった回の欠け。incomplete でなければ None（結果 JSON は incomplete のときだけ gaps を持つ）。

    最後のステップが残した欠けを引き継ぎ、欠けの詳細を持たない incomplete（JSON を出さないステップ・欠けを書かなかった
    ステップ）には、止まったステップと理由の欠けを 1 件補う（gaps の無い incomplete は結果 JSON の形に合わない）。
    """
    if final["kind"] != "incomplete":
        return None
    last = final["last"]
    if last in rec.step_gaps:
        return list(rec.step_gaps[last])
    return [{"kind": "step", "name": last or inv.plan.name, "reason": final["detail"] or "欠けの詳細が無い"}]


def _finished_result(inv: Invocation, rec: _FinalRecords) -> dict[str, Any]:
    """ok で終わった回の結果。ステップが残した欠け（outputs の gaps）があれば incomplete で止まる。"""
    gaps = rec.outputs.get("gaps") if rec.outputs else None
    if gaps:
        detail = f"{inv.plan.name} は終わったが、ステップが残した欠け {len(gaps)} 件がある"
        return result.stopped(TOOL, "incomplete", detail, items=rec.items, metrics=rec.metrics, gaps=gaps, **rec.shared)
    return result.result(TOOL, OK, f"{inv.plan.name} が終わった", rec.items, rec.metrics, **rec.shared)


def _result_of(inv: Invocation, final: Optional[dict[str, Any]], status: Optional[str], rec: _FinalRecords) -> dict[str, Any]:
    """終わった回の結果を組む（状態の保存先を読まない）。耐久ワークフローが例外で終わったときは unknown で止まる。"""
    items, metrics, shared = rec.items, rec.metrics, rec.shared
    if final is None:
        detail = f"{inv.plan.name} の耐久ワークフローが {status} で終わった"
        return result.stopped(TOOL, "unknown", detail, items=items, metrics=metrics, **shared)
    if final["status"] == OK:
        return _finished_result(inv, rec)
    if final["status"] == STOPPED:
        items = [{**items[0], "step": final["last"]}, *items[1:]]
        again = result.command(TOOL, "next", *_with_start(inv.again, final["last"])) if final["last"] else None
        gaps = _stop_gaps(inv, final, rec)
        detail = final["detail"]
        if final["last"] in rec.step_gaps:
            # 工程の failed の理由はこの説明をそのまま載せるので、欠けの名前を末尾に添える（#3908）
            detail = f"{detail}（欠け: {', '.join(sorted({g['name'] for g in rec.step_gaps[final['last']]}))}）"
        return result.stopped(TOOL, final["kind"], detail, items=items, metrics=metrics, next=again, gaps=gaps, **shared)
    after = inv.plan.following(final["last"])
    cont = result.command(TOOL, "next", *_with_start(inv.again, after)) if after else None
    return result.result(TOOL, GATE, final["detail"], items, metrics, routes={"approved": cont, "rejected": None}, **shared)


def final_result(inv: Invocation, final: Optional[dict[str, Any]], status: Optional[str]) -> dict[str, Any]:
    """終わった回の結果。状態の保存先の記録（載った PR・ステップの outputs）を読み、`_result_of` で組む。"""
    items = [_run_item(inv), *_pr_items(inv.state_dir)]
    metrics = {"round": inv.round_no, "steps": final["count"] if final else 0}
    outputs, warnings = recorded_outputs(inv.state_dir)
    shared = {"outputs": outputs, "warnings": warnings, "run_id": inv.run_id}
    return _result_of(inv, final, status, _FinalRecords(items, metrics, outputs, shared, recorded_step_gaps(inv.state_dir)))


def _stored(inv: Invocation) -> dict[str, Any]:
    found = stored_result(inv.state_dir, inv.run_id)
    if found is not None:
        return found
    items = [_run_item(inv)]
    return result.result(TOOL, OK, f"{inv.plan.name} の回は終わっている（結果のバックアップが無い）", items, {"round": inv.round_no})


def _finish(inv: Invocation) -> dict[str, Any]:
    status = durable.status(inv.workflow_id)
    final = durable.result(inv.workflow_id) if status == "SUCCESS" else None
    out = final_result(inv, final, status)
    write_json(inv.result_path, out)
    _record_run(inv, _head(inv.plan))
    return out


def advance(
    root: Path,
    inv: Invocation,
    reply: Optional[Reply],
    on_gate: Callable[[dict[str, Any]], None],
    url: Optional[str] = None,
) -> dict[str, Any]:
    """回を始めるか続け、終わるか承認の回答を待つ所まで進める。

    待つ所に来たら `on_gate(結果)` を DBOS を開いたまま呼ぶ。`on_gate` は結果を出してプロセスを抜ける（戻らない）。
    終わった回（retired/）なら流さずにバックアップの結果を返す（B4）。プランが worker の入力（`worker_kinds.PLAN_KEY`）を
    持てば、流す前にこの回の保存先の `values.json` へ書く（書けなければ流さずに precondition）。
    承認の回答（`reply`）は回答した者と組で受け、送る前に回答の記録（`answer_path`）を書く。回答した者が無ければ
    回答を送らずに invalid で止まる（承認待ちのステップの証跡は、回答した者の分かる回答の記録からだけ書く。#4409）。
    回答コマンドが固定した待ちと提示が今の待ちと提示に合わなければ、回答を記録せずに approval_stale で止まる。
    同じ回の提示を確かめられない旧い形の目印（`unconfirmed`）への回答は、待ちを照らした後、記録も送信もせずに precondition で止まる。
    """
    if _finished(root, inv.key):
        return _stored(inv)
    try:
        worker_kinds.hand_plan_values(inv.state_dir, inv.plan.raw)
    except (OSError, worker_paths.EntryError) as exc:
        detail = f"{inv.plan.name} の worker の入力を保存先 {inv.state_dir} の {worker_kinds.VALUES_FILE} へ書けない: {exc}"
        return result.stopped(TOOL, "precondition", detail)
    _record_run(inv)
    with durable.session(root, inv.key, url):
        status = durable.status(inv.workflow_id)
        baseline = read_marker(inv.state_dir, inv.run_id)
        if status is None:
            durable.spawn(plan_workflow, inv.workflow_id, _spec(inv))
        elif reply is not None:
            refused = _deliver(inv, baseline, reply)
            if refused is not None:
                return refused
        else:
            baseline = None
        reached, marker = _watch(inv, baseline)
        if reached == GATE:
            on_gate(gate_result(inv, marker))
        out = _finish(inv)
    return out
