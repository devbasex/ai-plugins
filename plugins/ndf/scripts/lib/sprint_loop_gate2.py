"""開発ループの承認ゲート 2 の答えの読み取りと、承認の後の続きのコマンド（sprint_loop から分けた）。"""

from __future__ import annotations

import shlex
from pathlib import Path

import clock
import gate_rejections as gr
import jsonio

from sprint_loop_record import GATE_2, SUPERVISE, Env, selected_issues
from sprint_loop_gate import (
    build_close,
    gate2_resume_step,
    plan_info,
    queue_command,
    stages_after,
    wait_lap,
)


EPOCH = clock.parse("1970-01-01T00:00:00Z")  # 時刻の無い答え（止まった時刻の無い古い記録のとき）の並びの値


def gate2_answer(lap: dict, since: str | None = None) -> str | None:
    """承認ゲート 2 の答え: 止まった時刻（since）より後の、周回の状態の利用者の承認と差し戻しの記録のうち最新のもの。
    approved / rejected、どちらも無ければ None。since より前の答え（同じ周回の前の関門への答え）は数えない。"""
    path = lap.get("state_path") or ""
    state = jsonio.read(path, missing={}, broken={}, want=dict) if path else {}
    try:
        rows = gr.read_rejections()
    except jsonio.JsonReadError:
        rows = []
    sp = str(Path(path).resolve()) if path else None
    # 承認の行（#2183）は状態ファイルの gates の方で読む
    rejections = [r for r in rows if gr.is_rejection(r) and r.get("gate") == GATE_2 and r.get("sprint") == sp]
    return latest_gate2_answer(state.get("gates", []), rejections, since)


def latest_gate2_answer(gates: list[dict], rejections: list[dict], since: str | None) -> str | None:
    """関門 2 の利用者の承認（gates）と差し戻し（rejections）のうち、since 以後で最新のもの。同じ時刻は承認を採る。"""
    after = clock.parse(since, naive="utc") if since else None

    def fresh(at):
        t = clock.parse(at, naive="utc") if at else None
        if after is None:
            return t or EPOCH
        return t if t is not None and t >= after else None

    answers = [(k, "approved") for g in gates if g.get("name") == GATE_2 and g.get("by") != "mvv" and (k := fresh(g.get("at")))]
    answers += [(k, "rejected") for r in rejections if (k := fresh(r.get("at")))]
    return max(answers, key=lambda a: a[0])[1] if answers else None


def gate2_pipeline(env: Env, rec: dict, lap: dict, stop: dict) -> str:
    """承認ゲート 2 の承認の後の続き: 止まった計画を経路に合う再開のステップから続け → 同じ queue の残りのステージと
    まとめの queue → wait → loop lap の 1 行。続けるステップが無い計画（gate-2 のプラン）は手で行う本番を先に示す。"""
    # 止まった記録の values.pr は検査のスプリント PR で、記録の PR ではない。まとめは {queue_record} で流す時点に探す（#2230 の決定 5）
    close = build_close(env, rec, lap, lap.get("issues_kept") or selected_issues(lap))
    done = Path(lap["dir"]) / f"done-{len(lap.get('dones', [])) + 1}.json"
    plan = lap.get("gate2_plan") or lap.get("prod_plan") or "<本番のプラン>"
    moved = {w["number"] for w in rec.get("waiting", []) if w.get("lap") == lap["n"]}
    rest = [
        [p for p in s if not (set(plan_info(p).get("課題") or []) and set(plan_info(p).get("課題") or []) <= moved)]
        for s in stages_after(lap, plan)
    ]
    stages = [s for s in rest if s] + [close]
    queue = queue_command(stages, done)
    lap.setdefault("dones", []).append(str(done))
    step = gate2_resume_step(plan)
    if step is None:
        return f"手で行う本番のステージ（{plan} の後）を終えてから: {queue} && {wait_lap(rec, done)}"
    run = f"python3 {shlex.quote(str(SUPERVISE))} run {shlex.quote(plan)} --from {step}"
    return f"{run} && {queue} && {wait_lap(rec, done)}"
