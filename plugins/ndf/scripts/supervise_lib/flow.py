"""プランの実行とキューを耐久ワークフローで流す（#1142 の決定 26〜30・35）。

プラン 1 本の実行は耐久ワークフロー `plan_workflow`、プランのステップ 1 回は耐久ステップ `run_step` である。
落ちた後に同じ `supervise.py run` / `queue` を打ち直すと、`lib/durable.py` が記録から耐久ワークフローを続け、
記録のある耐久ステップは流し直さずに出力だけを返す。`plan_workflow` は遷移の規則を持たない。次のステップは
`run_step` の出力（`Engine` の判断のメソッドが決めたもの）で決まり、`plan_workflow` が持つのはステップの数の
上限・知らないステップの判定・出力を `Engine.replay` へ渡すことだけである。

    engine = Engine(plan, state_dir, slow, plan_path)
    flow.launch_run(engine, start)   # 実行の鍵の排他を取って DBOS を起動する（cmd_run）
    text = engine.run(start)         # Engine.run が flow.run_engine を呼ぶ

    prefix = flow.launch_queue(done, max_, resources, fresh)   # cmd_queue
    durable.start(durable.resolve(prefix), flow.queue_workflow, args)

- 登録簿: `Engine` はプロセスの中の登録簿（`plan_workflow` の接頭辞 → `Engine`）で引く。落ちた後の起動では
  `DBOS.launch()` の直後に回復した `plan_workflow` が流れ始めるため、`run` は launch の前に登録する。登録簿に
  無ければ、耐久ワークフローの入力（プランのパス・状態ディレクトリ・`--slow`・開始のステップ・同時に流れる
  他のプランのファイル）から作り直す
- 資源の枠: 資源のタグのあるステップ（`admission.tags`）は、耐久キュー `res-<タグ>`（同時の本数 = 枠の本数）へ
  入れた耐久ワークフロー `slot_workflow` の中で流す（I21）
- queue: `queue_workflow` がステージを順に流す。同じステージのプランは耐久キュー `plans`（同時の本数 = `--max`）へ
  入れた順に流れる。重なりの組（`admission.groups`）は、耐久ワークフロー `group_workflow` 1 つとして入れ、その中で
  1 本ずつ流す（I20）。DBOS 3.1.0 の耐久キューの分割は、枠が足りないとき流す分割を乱数の順に選び、入れた順に
  流す今の約束を保てないため使わない
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import durable
from supervise_lib import admission, paths
from supervise_lib import queue as queue_files
from supervise_lib.decl import QUEUE_RESOURCES, SUPERVISE_DECL, DeclError, decl_roots, queue_decl, read_decl
from supervise_lib.engine import Engine
from supervise_lib.worker_steps import CONCURRENT_FILES

FINISHED = ("完了", "関門")  # 打ち直しでも流し直さない結果（止まったは次の実行の回で頭から流す）
PLANS = "plans"  # queue が流すプランの耐久キュー
PARENT_WAIT = 120.0  # 回復した slot_workflow が、自分を入れた plan_workflow の回復を待つ上限（秒）
_ENGINES: dict[str, Engine] = {}
_TURN: dict[str, int] = {}  # plan_workflow の接頭辞 → 資源の枠を待っているステップの番号
_READY = threading.Condition()  # 登録簿と _TURN の排他と、slot_workflow への合図
_OPENED: dict[str, Any] = {}  # このモジュールが開いた耐久の記録と、耐久キューを登録した資源の枠


def _finished(out: Any) -> bool:
    return isinstance(out, dict) and out.get("result") in FINISHED


def res_queue(tag: str) -> str:
    """資源のタグの耐久キューの名前。"""
    return f"res-{tag}"


def _queues(resources: dict) -> dict:
    return {res_queue(tag): {"concurrency": int(res["limit"])} for tag, res in resources.items()}


def _registered() -> dict:
    """今開いている耐久の記録に登録した資源の枠（このモジュールが開いた記録でなければ空）。"""
    return _OPENED.get("resources") or {} if _OPENED.get("launched") is durable.launched() else {}


def identity_of(engine: Engine) -> str:
    """実行の鍵の元。プランの絶対パス（無ければ状態ディレクトリの絶対パス）。"""
    return engine.ctx.plan_path or str(Path(engine.state.dir).resolve())


def prefix_of(identity: str, body: str, start: str | None) -> str:
    """`plan-<パス鍵>-<中身鍵>-<開始>`。プランを直すか開始を変えれば、別の耐久ワークフローになる。"""
    return f"plan-{durable.key_hash(identity)}-{durable.key_hash(body, 8)}-{start or '-'}"


def plan_prefix(engine: Engine, start: str | None) -> str:
    path = engine.ctx.plan_path
    if path and Path(path).is_file():
        body = Path(path).read_text(encoding="utf-8")
    else:
        body = json.dumps(engine.plan, ensure_ascii=False, sort_keys=True, default=str)
    return prefix_of(identity_of(engine), body, start)


def resources_of(engine: Engine) -> dict:
    """`run` のプランの資源の枠（作業場所の宣言。読めなければ知らせて既定で流す）。"""
    try:
        roots = decl_roots(str(engine.plan["作業場所"]), engine.plan.get("リポジトリ"))
        return queue_decl(read_decl(roots, SUPERVISE_DECL))["resources"]
    except DeclError as e:
        print(f"[ndf supervise] 資源の枠の宣言を読めないため既定で流す: {e}", file=sys.stderr)
        return {tag: dict(res) for tag, res in QUEUE_RESOURCES.items()}


def launch_run(engine: Engine, start: str | None) -> str:
    """`Engine` を登録してから、`run` の耐久の記録を開く（続けない接頭辞の途中の記録は launch の前に止める）。接頭辞を返す。"""
    prefix = plan_prefix(engine, start)
    resources = resources_of(engine)
    _ENGINES[prefix] = engine
    try:
        launched = durable.launch("run", identity_of(engine), queues=_queues(resources), keep=prefix)
    except BaseException:
        _ENGINES.pop(prefix, None)
        raise
    _OPENED.update(launched=launched, resources=resources)
    return prefix


def launch_queue(done: Path, max_: int, resources: dict, fresh: bool) -> str:
    """`queue` の耐久の記録を開く（実行の鍵は done の絶対パスから決める）。接頭辞 `queue-<done の鍵>` を返す。

    `fresh` なら、途中の耐久ワークフローを launch の前に子ごと止める（流すプランの並びを変えて打ち直したとき）。"""
    queues = {PLANS: {"worker_concurrency": max_}, **_queues(resources)}
    launched = durable.launch("queue", str(Path(done).resolve()), queues=queues, keep=() if fresh else None)
    _OPENED.update(launched=launched, resources=resources)
    return launched.key


def run_engine(engine: Engine, start: str | None = None) -> str:
    """`Engine.run` の本体。耐久ワークフローを始めるか続けるか記録を返し、`## フェーズの報告` を返す。

    耐久の記録が開いていなければ（`Engine` を直に流すとき）、ここで開いて終わりに閉じる。"""
    own = durable.launched() is None
    prefix = launch_run(engine, start) if own else plan_prefix(engine, start)
    _ENGINES[prefix] = engine
    try:
        ref = durable.resolve(prefix, finished=_finished)
        if ref.action == "done":
            if ref.output.get("result") == "関門":
                engine.state.retell_gates(ref.output.get("gates") or [])
            return ref.output["report"]
        if ref.action == "continue":
            print(f"[ndf supervise] 落ちた前の起動の {ref.id} を続ける（済んだステップは流し直さない）", file=sys.stderr)
        args = {
            "prefix": prefix,
            "plan_path": engine.ctx.plan_path,
            "state_dir": str(engine.state.dir),
            "slow": list(engine.slow.args),
            "start": start,
            "resources": _registered(),
        }
        wid = durable.start(ref, plan_workflow, args)
        got = durable.wait(wid)
        if got.kind != "done":
            durable.output_of(wid)  # 耐久ワークフローの中の例外をそのまま上げる
            raise durable.DurableError(f"耐久ワークフロー {wid} が終わらなかった（{got.kind}: {got.value}）")
        return got.value["report"]
    finally:
        _forget(prefix)
        if own:
            durable.close()


def _engine(prefix: str, args: dict | None = None) -> Engine:
    with _READY:
        eng = _ENGINES.get(prefix)
        if eng is None:
            if not args or not args.get("plan_path"):
                raise durable.DurableError(f"耐久ワークフロー {prefix} の Engine が無く、プランのパスも無い")
            plan = json.loads(Path(args["plan_path"]).read_text(encoding="utf-8"))
            eng = _ENGINES[prefix] = Engine(plan, Path(args["state_dir"]), args.get("slow") or [], args["plan_path"])
            if args.get("others"):
                eng.plan[CONCURRENT_FILES] = list(args["others"])
        return eng


def _forget(prefix: str) -> None:
    with _READY:
        _ENGINES.pop(prefix, None)
        _TURN.pop(prefix, None)


@durable.step(name="ndf.supervise.prepare")
def prepare_step(prefix: str, start: str | None) -> Any:
    """実行の条件と worktree の用意（ファイルとプロセスに触る）。流さないなら [結果, 理由]。"""
    return _engine(prefix).prepare(start)


@durable.step(name="ndf.supervise.run_step")
def run_step(prefix: str, n: int, sid: str) -> dict:
    """プランのステップ 1 回。判断と記録の書き込み（進捗ログ・`.out`・`state.json`）はこの中で行う。"""
    return _engine(prefix).execute(n, sid)


@durable.step(name="ndf.supervise.report")
def report_step(prefix: str, result: str, reason: str) -> str:
    return _engine(prefix).report(result, reason)


@durable.workflow(name="ndf.supervise.slot_workflow")
def slot_workflow(prefix: str, n: int, sid: str, tags: list[str]) -> dict:
    """資源の枠を 1 本取って、プランのステップ 1 回を流す（I21。設計の `tagged_step`）。タグが複数なら、名前の順に枠を取ってから流す。

    落ちた後の起動では、自分を入れた `plan_workflow` より先に流れ始めうる。`plan_workflow` が記録を組み直して
    同じステップまで進むのを待つ。"""
    with _READY:
        if not _READY.wait_for(lambda: _TURN.get(prefix) == n and prefix in _ENGINES, timeout=PARENT_WAIT):
            raise durable.DurableError(f"ステップ {sid} を入れた耐久ワークフロー {prefix} が {PARENT_WAIT:g} 秒待っても続かない")
    if len(tags) > 1:
        return _in_slot(prefix, n, sid, tags[1:])
    return run_step(prefix, n, sid)


def _in_slot(prefix: str, n: int, sid: str, tags: list[str]) -> dict:
    """資源の枠の耐久キューへ `slot_workflow` を入れ、結果を待つ（耐久ワークフローの中で呼ぶ）。"""
    with _READY:
        _TURN[prefix] = n
        _READY.notify_all()
    return durable.output_of(durable.submit(res_queue(tags[0]), slot_workflow, prefix, n, sid, tags, id=f"{durable.current_id()}-t{n}"))


@durable.workflow(name="ndf.supervise.plan_workflow")
def plan_workflow(args: dict) -> dict:
    """プラン 1 本の実行。ステップを `nxt` の順に流し、ステップの数が上限を超えるか知らないステップに当たれば止まる。"""
    began = time.time()
    prefix, start = args["prefix"], args.get("start")
    resources = args.get("resources") or {}
    eng = _engine(prefix, args)
    stopped = eng.setup(start) or prepare_step(prefix, start)
    if stopped:
        result, reason = stopped
    else:
        eng.after_prepare()
        result, reason = "完了", "無し"
        limit = eng.plan.get("上限", 30)
        sid, n = start or eng.order[0], 0
        while sid:
            n += 1
            if n > limit:
                result, reason = "止まった", f"ステップの数が上限 {limit} を超えた"
                break
            if sid not in eng.steps:
                result, reason = "止まった", f"知らないステップ: {sid}"
                break
            tags = admission.tags(eng.steps[sid], resources)
            out = _in_slot(prefix, n, sid, tags) if tags else run_step(prefix, n, sid)
            eng.replay(out)
            if out["result"] is not None:
                result, reason = out["result"], out["reason"]
            sid = out["nxt"]
        result, reason = eng._final_result(result, reason)
    gates = [a for a in eng.state.attention_log if a.get("reason") == "関門"]
    report = report_step(prefix, result, reason)
    return {"result": result, "reason": reason, "report": report, "gates": gates, "seconds": round(time.time() - began, 1)}


# ---- queue ----


def make_worktree(plan: dict) -> str | None:
    """queue がプランを流す前に worktree を作る。誤りの文を返す（無ければ None）。"""
    return paths.ensure_worktree(plan)


@durable.step(name="ndf.supervise.queue.listing")
def listing_step(done: str, plans: list[str]) -> None:
    """前の queue の終わりを消し、流すプランの一覧を done の隣へ書く（wait が読む）。"""
    queue_files.start_listing(Path(done), plans)


@durable.step(name="ndf.supervise.queue.fill")
def fill_step(first: bool, stage: list[str], items: list[dict]) -> dict:
    """ステージを流す前に、プランの `{queue_pr:<名>}` と QUEUE_PRS を埋める（プランのファイルを書く）。"""
    skipped, runnable = queue_files.fill_stage(first, stage, items)
    return {"skipped": skipped, "runnable": runnable}


@durable.step(name="ndf.supervise.queue.admit")
def admit_step(plans: list[str], shared: list[dict]) -> dict:
    """流すプランの実行の回を選び、重なりの組を数えて標準エラーへ知らせる（耐久ステップの中なので、打ち直しで 2 度出ない）。

    完了か関門の記録があるプランは流し直さず、並行中にも数えない。キューの外のプランは候補に入らない（C-4）。"""
    refs: dict[str, dict] = {}
    files: dict[str, list[str]] = {}
    for p in plans:
        body = Path(p).read_text(encoding="utf-8")
        prefix = prefix_of(str(Path(p).resolve()), body, None)
        ref = durable.resolve(prefix, finished=_finished)
        refs[p] = {"prefix": prefix, "id": ref.id, "action": ref.action, "output": ref.output}
        if ref.action != "done":
            files[p] = queue_files.touched_files(body)
    grouped = admission.groups(files, shared)
    pairs = admission.overlaps(files, shared)
    for o in pairs:
        a, b = o["paths"]
        how = a if a == b else f"{a} ⊃ {b}" if admission.contains(a, b) else f"{b} ⊃ {a}"
        print(f"supervise-queue: 重なる組 {o['a']} と {o['b']}（{how}）を同時には流さない", file=sys.stderr, flush=True)
    return {"groups": grouped, "overlaps": pairs, "others": admission.others(files, grouped), "refs": refs}


@durable.step(name="ndf.supervise.queue.worktrees")
def worktrees_step(plans: list[str]) -> None:
    """すぐ流れるプランの worktree を、入れた順に 1 本ずつ作る（同時の git worktree add は .git/config の lock で落ちる）。

    実行の条件のあるプランは、プランが条件を打ってから作る。作れなかったときの報告は、プランが同じ誤りで書く。"""
    for p in plans:
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            if not data.get("実行の条件"):
                make_worktree(data)
        except (OSError, ValueError, KeyError, AttributeError):
            pass


@durable.step(name="ndf.supervise.queue.done")
def done_step(done: str, items: list[dict], max_: int, pairs: list[dict], resources: dict) -> dict:
    """結果の JSON を組み立てて done へ書く。"""
    return queue_files.write_done(Path(done), items, max_, pairs, {tag: res["limit"] for tag, res in resources.items()})


def _plan_output(wid: str) -> dict:
    """プランの耐久ワークフローの出力。例外で終わったプランは `{"error"}`（ほかのプランを止めない）。"""
    try:
        return durable.output_of(wid)
    except Exception as e:  # noqa: BLE001  プランの中の誤りは、そのプランの結果として返す
        return {"error": f"{type(e).__name__}: {e}"}


@durable.workflow(name="ndf.supervise.group_workflow")
def group_workflow(members: list[dict]) -> list[dict]:
    """重なりの組を、入れた順に 1 本ずつ流す（I20）。組は耐久キュー `plans` の 1 本として数える。"""
    outs = []
    for m in members:
        outs.append(_plan_output(durable.start(durable.WorkflowRef(m["id"], 0, "start"), plan_workflow, m["args"])))
        _forget(m["args"]["prefix"])
    return outs


def run_stage(plans: list[str], adm: dict, args: dict) -> list[dict]:
    """同じステージのプランを耐久キュー `plans` へ入れ、終わりを待って items を返す（`queue_workflow` の中で呼ぶ）。

    同時に流れるのは `--max` 本までで、入れた順に流れる。重なりの組は `group_workflow` 1 つとして入れる。"""
    refs = adm["refs"]
    worktrees_step([g[0] for g in adm["groups"]][: args["max"]])
    waits: dict[str, tuple[str, int | None]] = {}
    for i, group in enumerate(adm["groups"]):
        members = [{"id": refs[p]["id"], "args": _plan_args(p, refs[p]["prefix"], adm["others"].get(p), args)} for p in group]
        if len(members) == 1:
            wid = durable.submit(PLANS, plan_workflow, members[0]["args"], id=members[0]["id"], priority=i + 1)
            waits[group[0]] = (wid, None)
        else:
            wid = durable.submit(PLANS, group_workflow, members, id=f"group-{members[0]['id']}", priority=i + 1)
            waits.update({p: (wid, k) for k, p in enumerate(group)})
    outs: dict[str, Any] = {}
    items = []
    for p in plans:
        if p not in waits:
            items.append(queue_files.plan_item(p, refs[p]["output"]))  # 完了か関門の記録があるプランは、記録した結果を返す
            continue
        wid, k = waits[p]
        if wid not in outs:
            outs[wid] = _plan_output(wid)
        out = outs[wid][k] if k is not None and isinstance(outs[wid], list) else outs[wid]
        _forget(refs[p]["prefix"])
        items.append(queue_files.plan_item(p, out))
    return items


def _plan_args(plan: str, prefix: str, others: list[str] | None, args: dict) -> dict:
    return {
        "prefix": prefix,
        "plan_path": plan,
        "state_dir": str(paths.state_dir_of(plan)),
        "slow": [],
        "start": None,
        "resources": args["resources"],
        "others": others or [],
    }


@durable.workflow(name="ndf.supervise.queue_workflow")
def queue_workflow(args: dict) -> dict:
    """`queue` 1 回。ステージを順に流し、前のステージが 1 本でも完了でなければ後続を流さない。"""
    stages, done = args["stages"], args["done"]
    listing_step(done, [p for stage in stages for p in stage])
    items: list[dict] = []
    pairs: list[dict] = []
    for idx, stage in enumerate(stages):
        filled = fill_step(idx == 0, stage, items)
        items += filled["skipped"]
        if filled["runnable"]:
            adm = admit_step(filled["runnable"], args["shared"])
            pairs += adm["overlaps"]
            items += run_stage(filled["runnable"], adm, args)
    return done_step(done, items, args["max"], pairs, args["resources"])
