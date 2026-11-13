"""アクティビティの関数（#3092 の構成要素「アクティビティの関数」）。定義の `call` の名前 → 関数。

- `ndf.plan`: 工程のプランを作って `supervise.py run` で流す。`with.kind` があれば `supervise.py new <種類>` の雛形、
  無ければ `with.skill` の Skill を起動する 1 ステップのプランにする。承認と判断のゲート（終了コード 10〜19）は
  待ちとして返し、答えの後は止まったステップの次から `run --from` で続ける
- `ndf.listen`: 承認ゲートの答えとリリースの結果の待ち。DBOS の受信はワークフローの中でしか打てないため、
  進行表のワークフロー（`progress_run`）が持つ。ここでは答えを出力の形へ写すだけ
- `ndf.record`: 進捗記録の生成（`progress_projection`）。失敗しても工程を止めない
- `ndf.reshuffle`: 組み換え（`reshuffle`）

アクティビティの出力は統一 JSON レポートの形で、工程の文書の `output.schema` で検査する（呼ぶ側）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
SUPERVISE = HERE.parent / "supervise.py"
GATE_CODES = range(10, 20)


def _report(tool: str, status: str, summary: str, **metrics) -> dict:
    return {"tool": tool, "status": status, "summary": summary, "items": [], "metrics": metrics}


def _plan_doc(inp: dict, args: dict, work: Path) -> Path:
    stage, issues = inp["stage"], inp.get("issues") or []
    target = [inp["issue"]] if inp.get("issue") else issues
    where = work / stage.replace("/", "_")
    where.mkdir(parents=True, exist_ok=True)
    path = where / "plan.json"
    if args.get("kind"):
        extra = [str(x) for x in (inp.get("with") or {}).get("args", [])]
        cmd = [sys.executable, str(SUPERVISE), "new", args["kind"], "--out", str(path), *extra]
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=inp.get("cwd"))
        if p.returncode != 0:
            raise RuntimeError(f"supervise.py new {args['kind']} が {p.returncode} で終わった: {p.stdout[-300:]}")
        return path
    refs = " ".join(f"#{n}" for n in target)
    doc = {
        "フェーズ": stage, "課題": target, "モード": inp.get("mode"), "作業場所": inp.get("cwd") or str(Path.cwd()),
        "steps": [{"id": "work", "type": "work", "kind": stage, "stage": stage,
                   "prompt": f"/ndf:{args.get('skill')} {refs}（工程: {stage}。進行表 {inp['table']}）", "next": "end"}],
    }
    (where / "plan.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def act_plan(inp: dict, args: dict) -> dict:
    """工程のプランを作り、`supervise.py run` で流す。承認と判断のゲートは `gate` で返す。"""
    work = Path(inp.get("work") or Path.cwd())
    work.mkdir(parents=True, exist_ok=True)
    resume = (inp.get("answered") or {}).get("from")
    try:
        plan = Path(resume["plan"]) if resume else _plan_doc(inp, args, work)
    except RuntimeError as e:
        return _report("ndf.plan", "stopped", str(e))
    cmd = [sys.executable, str(SUPERVISE), "run", str(plan)] + (["--from", resume["step"]] if resume else [])
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=inp.get("cwd"))
    last = (p.stdout.strip().splitlines() or ["{}"])[-1]
    try:
        out = json.loads(last)
    except json.JSONDecodeError:
        out = {}
    metrics = {"plan": str(plan), "code": p.returncode, **(out.get("metrics") or {})}
    if p.returncode == 0:
        return _report("ndf.plan", "ok", out.get("summary") or "プランが終わった", **metrics)
    if p.returncode in GATE_CODES:
        nxt = (out.get("metrics") or {}).get("next_step") or (out.get("metrics") or {}).get("step")
        return _report("ndf.plan", "gate", out.get("summary") or "プランが承認ゲートで止まった", **metrics, gate_step=nxt)
    return _report("ndf.plan", "stopped", out.get("summary") or f"プランが {p.returncode} で終わった", **metrics)


def act_listen(answer: Optional[dict]) -> dict:
    """承認ゲートの答えかリリースの結果を出力の形へ写す。答えが無い（待ちの期限を過ぎた）なら `stopped`。"""
    if not answer:
        return {**_report("ndf.listen", "stopped", "待ちの期限を過ぎた"), "answer": "rejected"}
    value = answer.get("answer")
    status = "ok" if value in ("approved", "released") else "stopped"
    out = {**_report("ndf.listen", status, f"答え: {value}"), "answer": value}
    if answer.get("release") is not None:
        out["release"] = answer["release"]
    return out


def act_record(store, table_id: str, row: str, phase: str) -> dict:
    """進捗記録の生成を流す。書けなかった先は `items` に載せ、工程は止めない。"""
    import progress_projection

    try:
        got = progress_projection.project_stage(store, table_id, row, phase)
    except Exception as e:  # noqa: BLE001  進捗記録の失敗は工程を失敗にしない（詳細設計の act_record）
        got = {"projection": f"failed: {type(e).__name__}"}
    failed = [{"kind": "progress-record", "target": k, "result": v} for k, v in got.items() if str(v).startswith("failed")]
    rep = _report("ndf.record", "ok", f"進捗記録 {len(got) - len(failed)} 件・書けなかった {len(failed)} 件", targets=got)
    rep["items"] = failed
    return rep


def act_reshuffle(store, table_id: str, inp: dict, propose: Optional[Callable] = None) -> dict:
    """組み換えの判定・変更案・反映を流す。変更の要らないときは Claude を起動しない（受け入れ条件 19）。"""
    import progress_db as pdb
    import reshuffle

    plan, lanes, lane = None, {}, None
    with pdb.transaction(store) as conn:
        link = pdb.sprint_link(conn, table_id)
        if link:
            lane_row = pdb.table_row(conn, link[0])
            lane = pdb.lane_name(lane_row)
            if lane_row.parent_id:
                plan, lanes, _ = pdb.plan_and_lanes(conn, lane_row.parent_id)
    data = {**(inp.get("reshuffle") or {}), "lane": lane}
    if not reshuffle.needs_reshuffle(data, plan, lanes):
        return reshuffle.unchanged()
    try:
        proposal = (propose or reshuffle.propose)(data, lanes.get(lane) or [])
    except RuntimeError as e:
        return {**reshuffle.unchanged(), "status": "stopped", "summary": f"変更案を作れない: {e}"}
    return reshuffle.apply_changes(store, table_id, proposal)


ACTIVITIES: dict[str, str] = {"ndf.plan": "act_plan", "ndf.listen": "act_listen", "ndf.record": "act_record", "ndf.reshuffle": "act_reshuffle"}


def run_activity(store, table_id: str, rev: int, act, inp: dict, answer: Optional[dict] = None) -> dict:
    """`call` の名前から関数を引いて流し、出力を返す。知らない名前は `stopped`。"""
    name = ACTIVITIES.get(act.call)
    if name is None:
        return _report(act.call or "unknown", "stopped", f"知らないアクティビティ {act.call}")
    if name == "act_plan":
        return act_plan(inp, act.with_)
    if name == "act_listen":
        return act_listen(answer)
    if name == "act_record":
        return act_record(store, table_id, inp["stage"], "finish")
    return act_reshuffle(store, table_id, inp)

