"""アクティビティの関数（#3092 の構成要素「アクティビティの関数」）。定義の `call` の名前 → 関数。

- `ndf.plan`: 工程のプランを作って `supervise.py run` で流す。`with.kind` があれば `supervise.py new <種類>` の雛形、
  無ければ `with.skill` の Skill を起動する 1 ステップのプランにする。承認と判断のゲート（終了コード 10〜19）は
  待ちとして返し、答えの後は止まったステップの次から `run --from` で続ける
- `ndf.request`: 並列の計画のレーンのスプリントなら、オーガナイザーへリリースの依頼を送る（`parallel.py request`）。
  答えは `parallel.py await` が読み、同じ工程の `ndf.listen` の行へ `answered` で届ける。レーンの外のスプリントでは何もしない
- `ndf.listen`: 承認ゲートの答えとリリースの結果の待ち。DBOS の受信はワークフローの中でしか打てないため、
  進行表のワークフロー（`progress_run`）が持つ。ここでは答えを出力の形へ写すだけ
  pace: fast / auto では、待ちに入る前に `act_mvv` が MVV 判定を打ち、従えば `by: mvv` の答えを書いて止まらずに続ける
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
PARALLEL = HERE.parent / "parallel.py"
MVV_GATE = HERE.parent / "mvv-gate.py"
GATE_CODES = range(10, 20)


def _act_report(tool: str, status: str, summary: str, **metrics) -> dict:
    return {"tool": tool, "status": status, "summary": summary, "items": [], "metrics": metrics}


def _notes_of(inp: dict, target: list) -> str:
    """出し直しの指摘（承認ゲート 1 で送られた指摘の入力ファイル）を、設計の工程の起動指示へ添える（#2089 の決定 1）。"""
    notes = inp.get("notes") or {}
    got = [f"#{n}: {notes[str(n)]}" for n in target if str(n) in notes]
    if inp.get("stage") != "設計" or not got:
        return ""
    return f"。出し直しの指摘の入力（全回を読み、指摘への対応の表を設計文書へ書く）: {'、'.join(got)}"


def _context_text(inp: dict) -> str:
    """まとめの工程（振り返り・棚卸し）の起動指示へ添える、開発ループの文脈（今の `new close --loop --lap --milestone --carry`）。
    文脈の無いスプリントでは空。"""
    ctx = inp.get("context") or {}
    stage = inp.get("stage")
    if not ctx.get("loop") or stage not in ("振り返り", "棚卸し"):
        return ""
    text = f"。開発ループ {ctx['loop']} の {ctx.get('lap')} 回目"
    if stage == "振り返り" and ctx.get("milestone"):
        text += f"。マイルストーン {ctx['milestone']} を振り返りの材料にする"
    if stage == "棚卸し":
        text += "。差分の棚卸し（--depth diff）として無人で通す"
        if carry := [str(n) for n in ctx.get("carry") or []]:
            text += f"。ループの前の 1 回の持ち越し {' '.join('#' + n for n in carry)} を `upkeep.py` の `--add` で候補へ渡す（{' '.join(carry)}）"
    return text


def _release_args(inp: dict, args: dict) -> list[str]:
    """リリースのプランの経路（`with.channel`）と、開発ループが明示した版（文脈の `versions`）。版が無ければ流す時点に決める（#2164）。"""
    if args.get("kind") != "release" or not args.get("channel"):
        return []
    version = (((inp.get("context") or {}).get("versions")) or {}).get(args["channel"])
    where = ["--worktree", inp["cwd"]] if inp.get("cwd") else []
    return ["--channel", args["channel"], *where, *(["--version", version] if version else [])]


def _plan_doc(inp: dict, args: dict, work: Path) -> Path:
    stage, issues = inp["stage"], inp.get("issues") or []
    target = [inp["issue"]] if inp.get("issue") else issues
    where = work / stage.replace("/", "_")
    where.mkdir(parents=True, exist_ok=True)
    path = where / "plan.json"
    if args.get("kind"):
        extra = [str(x) for x in (inp.get("with") or {}).get("args", [])] + _release_args(inp, args)
        cmd = [sys.executable, str(SUPERVISE), "new", args["kind"], "--out", str(path), *extra]
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=inp.get("cwd"))
        if p.returncode != 0:
            raise RuntimeError(f"supervise.py new {args['kind']} が {p.returncode} で終わった: {p.stdout[-300:]}")
        return path
    refs = " ".join(f"#{n}" for n in target)
    doc = {
        "フェーズ": stage,
        "課題": target,
        "モード": inp.get("mode"),
        "作業場所": inp.get("cwd") or str(Path.cwd()),
        "steps": [
            {
                "id": "work",
                "type": "work",
                "kind": stage,
                "stage": stage,
                "prompt": f"/ndf:{args.get('skill')} {refs}（工程: {stage}。進行表 {inp['table']}）{_notes_of(inp, target)}{_context_text(inp)}",
                "next": "end",
            }
        ],
    }
    (where / "plan.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _answer_hold(plan: Path, start: Optional[str], inp: dict) -> Optional[dict]:
    """答え待ちの停止（#2707）の範囲にあるレーンで、新しい承認を生むプランなら流さず `gate`（`metrics.answer_hold`）で返す。

    `supervise.py run` は停止を読まないため、キューの開始の判定（`parallel_hold.plan_hold`）と同じ判定をここで打つ。"""
    import os
    import shlex

    import parallel_hold

    lane = os.environ.get("NDF_RELAY_NAME")
    if not lane:
        return None
    try:
        doc = json.loads(Path(plan).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        doc = None
    reason, _ = parallel_hold.plan_hold(lane, doc if isinstance(doc, dict) else None, start)
    got = parallel_hold.skip_of(reason)
    if got is None:
        return None
    park = f"python3 {shlex.quote(str(PARALLEL))} park {shlex.quote(got['plan'])} --lane {shlex.quote(got['lane'])} --table {shlex.quote(inp['table'])}"
    return _act_report("ndf.plan", "gate", reason, plan=str(plan), answer_hold=got, park=park)


def act_plan(inp: dict, args: dict) -> dict:
    """工程のプランを作り、`supervise.py run` で流す。承認と判断のゲートは `gate` で返す。"""
    work = Path(inp.get("work") or Path.cwd())
    work.mkdir(parents=True, exist_ok=True)
    resume = (inp.get("answered") or {}).get("from")
    try:
        plan = Path(resume["plan"]) if resume else _plan_doc(inp, args, work)
    except RuntimeError as e:
        return _act_report("ndf.plan", "stopped", str(e))
    held = _answer_hold(plan, resume["step"] if resume else None, inp)
    if held is not None:
        return held
    cmd = [sys.executable, str(SUPERVISE), "run", str(plan)] + (["--from", resume["step"]] if resume else [])
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=inp.get("cwd"))
    last = (p.stdout.strip().splitlines() or ["{}"])[-1]
    try:
        out = json.loads(last)
    except json.JSONDecodeError:
        out = {}
    metrics = {"plan": str(plan), "code": p.returncode, **(out.get("metrics") or {})}
    if p.returncode == 0:
        return _act_report("ndf.plan", "ok", out.get("summary") or "プランが終わった", **metrics)
    if p.returncode in GATE_CODES:
        nxt = (out.get("metrics") or {}).get("next_step") or (out.get("metrics") or {}).get("step")
        return _act_report("ndf.plan", "gate", out.get("summary") or "プランが承認ゲートで止まった", **metrics, gate_step=nxt)
    return _act_report("ndf.plan", "stopped", out.get("summary") or f"プランが {p.returncode} で終わった", **metrics)


def _lane_of(store, table_id: str) -> Optional[tuple[str, str]]:
    """スプリントの進行表が指す（計画名, レーン名）。計画の外なら None。"""
    import progress_db as pdb

    with pdb.transaction(store) as conn:
        link = pdb.sprint_link(conn, table_id)
        lane_row = pdb.progress_table(conn, link[0]) if link else None
        plan_row = pdb.progress_table(conn, lane_row.parent_id) if lane_row is not None and lane_row.parent_id else None
    if lane_row is None or plan_row is None:
        return None
    return plan_row.name, pdb.lane_name(lane_row)


def _context_lane(inp: dict) -> Optional[tuple[str, str]]:
    """文脈の lane（レーンで動く開発ループが渡す {plan, lane}）。ループの 1 回の進行表はレーンの順番表の行を持たないため、
    リリースの依頼の宛先はここから読む。無ければ None。"""
    got = (inp.get("context") or {}).get("lane") or {}
    return (got["plan"], got["lane"]) if got.get("plan") and got.get("lane") else None


def _prs_of(outputs: dict) -> list[str]:
    prs: list[str] = []
    for out in outputs.values():
        m = (out or {}).get("metrics") or {}
        for v in [*(m.get("prs") or []), m.get("pr")]:
            if v is not None and str(v).isdigit() and str(v) not in prs:
                prs.append(str(v))
    return prs


def act_request(store, table_id: str, inp: dict, listen_row: str) -> dict:
    """レーンのスプリントのリリースの依頼を送る（今までの 1 スプリントのレーンの依頼のプランを工程へ移した。#3092）。

    依頼を送れば、依頼の記録へ進行表の ID と答えを待つ行を足し、`parallel.py await` が答えをその行へ届けられるようにする。
    自分の Pull Request がすでに本番にある（`parallel.py request` が 0）なら、待つ行へ `released` の答えをここで書く。"""
    import progress_db as pdb
    import progress_model as pm

    lane = _lane_of(store, table_id) or _context_lane(inp)
    if lane is None:
        return _act_report("ndf.request", "ok", "並列の計画の外のスプリントのため、リリースの依頼を送らない", requested=False)
    state = Path(inp.get("work") or Path.cwd()) / "release-request"
    (state / "work").mkdir(parents=True, exist_ok=True)
    prs = _prs_of(inp.get("previous") or {})
    issues = [str(n) for n in inp.get("issues") or []]
    cmd = [sys.executable, str(PARALLEL), "request", lane[0], "--lane", lane[1], "--state", str(state)]
    cmd += (["--prs", *prs] if prs else []) + (["--issues", *issues] if issues else [])
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=inp.get("cwd"))
    record = state / "work" / "request.txt"
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _act_report("ndf.request", "stopped", f"parallel.py request が {p.returncode} で終わり、依頼の記録が無い: {p.stdout[-300:]}")
    import parallel_lane as pl

    data.update({"table": table_id, "row": listen_row})
    pl.write_request(state, data)
    if p.returncode == 0:  # 送らずに済んだ（すでに本番にある）
        value = {"answer": "released", "release": {"version": data.get("version"), "prs": data.get("prs")}}
        with pdb.transaction(store) as conn:
            rev, _ = pdb.definition(conn, table_id)
            # 書き手の表（I6）で `answered` を書けるのはオーガナイザーと conductor。この処理は conductor が打つ `progress.py next` の中で動く
            pdb.append_events(conn, table_id, [pm.EventDraft("answered", "conductor", listen_row, rev, payload=value)])
        return _act_report("ndf.request", "ok", "Pull Request はすでに本番にある。依頼を送らない", requested=False, record=str(record))
    if p.returncode != 10:
        return _act_report("ndf.request", "stopped", f"parallel.py request が {p.returncode} で終わった: {p.stdout[-300:]}")
    nxt = f"python3 {PARALLEL} await {lane[0]} --from {record}"
    return _act_report("ndf.request", "ok", f"リリースの依頼 {data.get('id')} を送った", requested=True, record=str(record), next_step=nxt)


MVV_PACES = ("fast", "auto")
MVV_GATES = {"gate": "design", "release": "release"}  # 待ちの行の kind → mvv-gate.py の --gate


def mvv_gate_of(inp: dict, kind: str, outputs: dict) -> Optional[str]:
    """MVV 判定で答えてよい待ちの行なら mvv-gate.py の `--gate` を返す（純粋な判定）。

    pace: fast / auto のスプリントの承認ゲートとリリースの待ちに当てる。レーンのリリースの依頼を送った
    （`metrics.requested` が真）待ちは、オーガナイザーが答えるため当てない。"""
    if inp.get("pace") not in MVV_PACES or kind not in MVV_GATES:
        return None
    if kind == "release" and any(((o or {}).get("metrics") or {}).get("requested") for o in outputs.values()):
        return None
    return MVV_GATES[kind]


def act_mvv(store, table_id: str, rev: int, row: str, inp: dict, gate: str) -> Optional[dict]:
    """待ちに入る前に MVV 判定（`mvv-gate.py check`）を打つ。今の承認ゲートの段（`plan_mvv_design`・`plan_mvv_release`）と
    同じ判定で、従う（越えない線なし。終了コード 0）なら待ちの行へ `answered`（`by: mvv`・判定の記録のパス）を書いて
    その答えを返す。ほかの判定（終了コード 10 など）と、スプリントの状態が無いときは None（人の答えを待つ）。"""
    import progress_db as pdb
    import progress_model as pm

    state = inp.get("state")
    if not state:
        return None
    where = Path(inp.get("work") or Path.cwd()) / "mvv" / row.replace("/", "_")
    where.mkdir(parents=True, exist_ok=True)
    note = where / "mvv-note.md"
    prs = _prs_of(inp.get("previous") or {})
    cmd = [sys.executable, str(MVV_GATE), "check", "--sprint", str(state), "--gate", gate]
    cmd += ["--mode", str(inp.get("mode") or ""), "--root", str(inp.get("cwd") or Path.cwd()), "--note", str(note)]
    cmd += ["--pr", *prs] if prs else []
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=inp.get("cwd"))
    if p.returncode != 0:
        return None
    value = {"answer": "approved", "by": "mvv", "record": str(note), "gate": gate}
    with pdb.transaction(store) as conn:
        pdb.append_events(conn, table_id, [pm.EventDraft("answered", "conductor", row, rev, payload=value)])
    return value


def act_listen(answer: Optional[dict]) -> dict:
    """承認ゲートの答えかリリースの結果を出力の形へ写す。答えが無い（待ちの期限を過ぎた）なら `stopped`。"""
    if not answer:
        return {**_act_report("ndf.listen", "stopped", "待ちの期限を過ぎた"), "answer": "rejected"}
    value = answer.get("answer")
    status = "ok" if value in ("approved", "released") else "stopped"
    out = {**_act_report("ndf.listen", status, f"答え: {value}"), "answer": value}
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
    rep = _act_report("ndf.record", "ok", f"進捗記録 {len(got) - len(failed)} 件・書けなかった {len(failed)} 件", targets=got)
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
            lane_row = pdb.progress_table(conn, link[0])
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


ACTIVITIES: dict[str, str] = {
    "ndf.plan": "act_plan",
    "ndf.request": "act_request",
    "ndf.listen": "act_listen",
    "ndf.record": "act_record",
    "ndf.reshuffle": "act_reshuffle",
}


def run_activity(store, table_id: str, rev: int, act, inp: dict, answer: Optional[dict] = None) -> dict:
    """`call` の名前から関数を引いて流し、出力を返す。知らない名前は `stopped`。"""
    name = ACTIVITIES.get(act.call)
    if name is None:
        return _act_report(act.call or "unknown", "stopped", f"知らないアクティビティ {act.call}")
    if name == "act_plan":
        return act_plan(inp, act.with_)
    if name == "act_listen":
        return act_listen(answer)
    if name == "act_request":
        return act_request(store, table_id, inp, inp["listen_row"])
    if name == "act_record":
        return act_record(store, table_id, inp["stage"], "finish")
    return act_reshuffle(store, table_id, inp)
