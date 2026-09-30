"""プランの実行を耐久ワークフローで流す（#1142 の決定 26〜28・35）。

プラン 1 本の実行は耐久ワークフロー `plan_workflow`、プランのステップ 1 回は耐久ステップ `run_step` である。
落ちた後に同じ `supervise.py run` を打ち直すと、`lib/durable.py` が記録から `plan_workflow` を続け、記録の
ある `run_step` は流し直さずに出力だけを返す。`plan_workflow` は遷移の規則を持たない。次のステップは
`run_step` の出力（`Engine` の判断のメソッドが決めたもの）で決まり、`plan_workflow` が持つのはステップの数の
上限・知らないステップの判定・出力を `Engine.replay` へ渡すことだけである。

    engine = Engine(plan, state_dir, slow, plan_path)
    flow.launch_run(engine, start)   # 実行の鍵の排他を取って DBOS を起動する（cmd_run）
    text = engine.run(start)         # Engine.run が flow.run_engine を呼ぶ

`Engine` はプロセスの中の登録簿（接頭辞 → `Engine`）で引く。落ちた後の起動では `DBOS.launch()` の直後に
回復した `plan_workflow` が流れ始めるため、登録は launch の前に行う。登録簿に無ければ、耐久ワークフローの
入力（プランのパス・状態ディレクトリ・`--slow`・開始のステップ）から作り直す。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import durable
from supervise_lib.engine import Engine

FINISHED = ("完了", "関門")  # 打ち直しでも流し直さない結果（止まったは次の実行の回で頭から流す）
_ENGINES: dict[str, Engine] = {}


def identity_of(engine: Engine) -> str:
    """実行の鍵の元。プランの絶対パス（無ければ状態ディレクトリの絶対パス）。"""
    return engine.ctx.plan_path or str(Path(engine.state.dir).resolve())


def plan_prefix(engine: Engine, start: str | None) -> str:
    """`plan-<パス鍵>-<中身鍵>-<開始>`。プランを直すか開始を変えれば、別の耐久ワークフローになる。"""
    path = engine.ctx.plan_path
    if path and Path(path).is_file():
        body = Path(path).read_text(encoding="utf-8")
    else:
        body = json.dumps(engine.plan, ensure_ascii=False, sort_keys=True, default=str)
    return f"plan-{durable.key_hash(identity_of(engine))}-{durable.key_hash(body, 8)}-{start or '-'}"


def launch_run(engine: Engine, start: str | None) -> str:
    """`Engine` を登録してから、`run` の耐久の記録を開く（続けない接頭辞の途中の記録は launch の前に止める）。接頭辞を返す。"""
    prefix = plan_prefix(engine, start)
    _ENGINES[prefix] = engine
    try:
        durable.launch("run", identity_of(engine), keep=prefix)
    except BaseException:
        _ENGINES.pop(prefix, None)
        raise
    return prefix


def run_engine(engine: Engine, start: str | None = None) -> str:
    """`Engine.run` の本体。耐久ワークフローを始めるか続けるか記録を返し、`## フェーズの報告` を返す。

    耐久の記録が開いていなければ（`Engine` を直に流すとき）、ここで開いて終わりに閉じる。"""
    own = durable.launched() is None
    prefix = launch_run(engine, start) if own else plan_prefix(engine, start)
    _ENGINES[prefix] = engine
    try:
        ref = durable.resolve(prefix, finished=lambda out: isinstance(out, dict) and out.get("result") in FINISHED)
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
        }
        wid = durable.start(ref, plan_workflow, args)
        got = durable.wait(wid)
        if got.kind != "done":
            durable.output_of(wid)  # 耐久ワークフローの中の例外をそのまま上げる
            raise durable.DurableError(f"耐久ワークフロー {wid} が終わらなかった（{got.kind}: {got.value}）")
        return got.value["report"]
    finally:
        _ENGINES.pop(prefix, None)
        if own:
            durable.close()


def _engine(prefix: str, args: dict | None = None) -> Engine:
    eng = _ENGINES.get(prefix)
    if eng is None:
        if not args or not args.get("plan_path"):
            raise durable.DurableError(f"耐久ワークフロー {prefix} の Engine が無く、プランのパスも無い")
        plan = json.loads(Path(args["plan_path"]).read_text(encoding="utf-8"))
        eng = _ENGINES[prefix] = Engine(plan, Path(args["state_dir"]), args.get("slow") or [], args["plan_path"])
    return eng


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


@durable.workflow(name="ndf.supervise.plan_workflow")
def plan_workflow(args: dict) -> dict:
    """プラン 1 本の実行。ステップを `nxt` の順に流し、ステップの数が上限を超えるか知らないステップに当たれば止まる。"""
    prefix, start = args["prefix"], args.get("start")
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
            out = run_step(prefix, n, sid)
            eng.replay(out)
            if out["result"] is not None:
                result, reason = out["result"], out["reason"]
            sid = out["nxt"]
        result, reason = eng._final_result(result, reason)
    gates = [a for a in eng.state.attention_log if a.get("reason") == "関門"]
    return {"result": result, "reason": reason, "report": report_step(prefix, result, reason), "gates": gates}
