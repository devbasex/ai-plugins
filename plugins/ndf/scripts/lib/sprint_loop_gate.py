"""sprint_loop_gate.py: 開発ループの周回の締め・まとめのステージ・承認ゲートの扱い（#2089）。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
from pathlib import Path

import gh_sections
import jsonio
import project_mvv as pm
import step_result

from sprint_loop_record import (
    TOOL,
    SPRINT_STATE,
    SUPERVISE,
    EXIT_REFUSED,
    IN_PROGRESS,
    FAILED,
    RETURNED,
    GATE_1,
    GATE_2,
    DESIGN_PHASE,
    FINISHED,
    NOT_RUN,
    LoopError,
    Env,
    read_loop_record,
    write_record,
    locked,
    current_lap,
    consumed,
    boundary_stop,
    mvv_change_stop,
    resume_command,
    stop_and_report,
    update_handoff,
    selected_issues,
)


def loop_ok(summary: str, rec: dict, items=None, status: str = "ok", next_: str | None = None, **metrics) -> dict:
    lap = current_lap(rec)
    m = {
        "status": status,
        "lap": lap["n"] if lap else len(rec.get("laps", [])),
        "consumed": consumed(rec),
        "budget": rec.get("budget"),
        **metrics,
    }
    if next_:
        m["next"] = next_
    return step_result.result(TOOL, "ok", summary, items or [], m, next=next_)


def select_cmd(name: str) -> str:
    return f"python3 {shlex.quote(str(SPRINT_STATE))} loop select {name}"


def wait_lap(rec: dict, done: Path) -> str:
    return f"python3 {shlex.quote(str(SUPERVISE))} wait {shlex.quote(str(done))} && python3 {shlex.quote(str(SPRINT_STATE))} loop lap {rec['name']} --done {shlex.quote(str(done))}"


def refusal_order(summary: str) -> dict:
    return {**step_result.result(TOOL, "stopped", summary, [], {"status": "refused"}), "exit": EXIT_REFUSED}


# ---------------------------------------------------------------- sprint


def build_close(env: Env, rec: dict, lap: dict, issues: list[int], record_pr=None, tag: str = "close") -> list[str]:
    """まとめのステージ（new close --pace auto）を組み、プランのパスを返す。断れば LoopError。"""
    prev = rec["laps"][lap["n"] - 2] if lap["n"] >= 2 else None
    carry = ((prev or {}).get("refine") or {}).get("deferred") or []
    out = Path(lap["dir"]) / tag
    cmd = [sys.executable, str(SUPERVISE), "new", "close", "--pace", "auto", "--name", lap["sprint"], "--worktree", str(env.main_dir())]
    cmd += ["--issue", *map(str, issues), "--state", lap["state_path"], "--out", str(out), "--mode", lap.get("mode") or "standard"]
    v = lap.get("versions") or {}
    if v.get("dev"):
        cmd += ["--version", v["dev"], "--prod", v.get("prod") or re.sub(r"-.*$", "", v["dev"])]
    if carry:
        cmd += ["--carry", *map(str, carry)]
    if record_pr:
        cmd += ["--record-pr", str(record_pr)]
    if lap.get("milestone"):
        cmd += ["--milestone", lap["milestone"]]
    code, so, se = env.run(cmd, cwd=str(env.main_dir()))
    lap["close_command"] = shlex.join(cmd)
    if code != 0:
        raise LoopError(f"new close --pace auto が断った（{code}）: {(se or so).strip()[:300]}")
    manifest = json.loads((out / "sprint.json").read_text(encoding="utf-8"))
    return [p for e in manifest["ステージ"] for p in e.get("plans", [])]


def _refused(env: Env, rec: dict, before: dict, step: str, code: int, summary: str) -> dict:
    stop = {"condition": "refused", "values": {"step": step, "exit": code, "summary": summary[:300]}}
    return stop_and_report(env, rec, before, stop, f"周回を組めなかった（{step}）: {summary[:200]}")


def copy_mvv_gate(rec: dict, lap: dict, state: Path, mvv: Path) -> None:
    """承認済みのスプリント MVV を周回の状態へ承認ゲート MVV の記録として写す（決定 11）。"""
    sha = pm.sha256_text(mvv.read_text(encoding="utf-8"))
    hit = next((a for a in rec.get("sprint_mvv_approvals", []) if a["milestone"] == lap["milestone"] and a["sha256"] == sha), None)
    if not hit:
        return
    m = jsonio.read(state, want=dict)
    file_sha = hashlib.sha256(Path((m.get("mvv") or {}).get("path") or mvv).read_bytes()).hexdigest()
    entry = {"name": "MVV", "what": f"開発ループ {rec['name']} の承認（{hit['at']}）", "at": hit["at"], "sha256": file_sha}
    m["gates"] = [g for g in m.get("gates", []) if g.get("name") != "MVV"] + [entry]
    jsonio.write_atomic(state, m, indent=1)


# ---------------------------------------------------------------- gate


def read_done_items(path: str) -> list[dict]:
    return jsonio.read(path, want=dict).get("items", [])


def plan_info(plan: str) -> dict:
    try:
        return json.loads(Path(plan).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def last_verdict(state_path: str, gate: str, issue: int | None = None) -> dict:
    """mvv-gate.jsonl の、この周回の状態の最後の判定の行（gate は design / release）。無ければ空。"""
    try:
        rows = jsonio.read_jsonl(pm.gate_log_path(), missing=[], skip_broken=True)
    except jsonio.JsonReadError:
        rows = []
    sp = str(Path(state_path).resolve())
    hits = [r for r in rows if r.get("gate") == gate and str(Path(r.get("sprint") or "").resolve()) == sp]
    if issue is not None:
        tagged = [r for r in hits if any(str(x).startswith(f"#{issue} ") for x in r.get("material") or [])]
        hits = tagged or hits
    return hits[-1] if hits else {}


def gate_copy(name: str, row: dict) -> dict:
    return {
        "name": name,
        "by": "mvv",
        "verdict": row.get("verdict", "unknown"),
        "basis": row.get("basis") or [],
        "pr": row.get("pr"),
        "at": row.get("at", ""),
    }


def waiting_section(rec: dict, w: dict) -> str:
    how = f"python3 {SPRINT_STATE} loop answer {rec['name']} {w['number']} --approved|--rejected"
    pr = f"#{w['pr']}" if w.get("pr") else "—"
    lines = [
        f"- 判定: {w.get('verdict', 'unknown')}（承認ゲート {w['gate']}）",
        f"- 理由: {w.get('reason') or '—'}",
        f"- 根拠の項目: {', '.join(w.get('basis') or []) or '—'}",
        f"- 設計 Pull Request: {pr}",
        f"- 答え: {w.get('answer') or 'まだ'}",
        f"- 答え方: `{how}`",
    ]
    return "\n".join(lines)


def mark_issue(env: Env, rec: dict, w: dict) -> str | None:
    """課題の本文に「人の判断待ち（開発ループ <名>）」の節を書く（本文全体を書き直す）。書けなければ理由を返す。"""
    heading = f"## 人の判断待ち（開発ループ {rec['name']}）"
    try:
        body = env.issue_body(w["number"])
        env.edit_issue(w["number"], gh_sections.replace_section(body, heading, waiting_section(rec, w)))
    except OSError as e:
        return str(e)
    return None


def cmd_gate(env: Env, name: str, done: str) -> dict:
    with locked(env, name):
        rec = read_loop_record(env, name)
        before = json.loads(json.dumps(rec))
        lap = current_lap(rec)
        if not lap or not lap.get("state_path"):
            return refusal_order("スプリントを組んだ進行中の周回が無い")
        return handle_gate(env, rec, before, lap, read_done_items(done))


def handle_gate(env: Env, rec: dict, before: dict, lap: dict, items: list[dict]) -> dict:
    gated = [i for i in items if i.get("result") == "関門"]
    if not gated:
        return refusal_order("done に承認ゲートで止まった計画が無い")
    if stop := mvv_change_stop(env, rec):
        return stop_and_report(env, rec, before, stop, "プロジェクト MVV が改訂された（承認ゲートの機械のチェック）")
    gate1 = [i for i in gated if plan_info(i["plan"]).get("フェーズ") == DESIGN_PHASE]
    if len(gate1) != len(gated):
        return handle_gate2(env, rec, before, lap)
    moved, kept, notes = move_to_waiting(env, rec, lap, gate1)
    if not kept:
        close_lap(env, rec, lap, RETURNED)
        return after_close(env, rec, before, f"承認ゲート 1 で全課題を人の判断待ちへ移した（{' '.join(f'#{n}' for n in moved)}）")
    try:
        queue, done = continuation(env, rec, lap, moved, kept)
    except (LoopError, OSError, ValueError, KeyError, StopIteration) as e:
        return _refused(env, rec, before, "続きの queue", 1, str(e))
    pipeline = f"{queue} && {wait_lap(rec, done)}"
    write_record(env, rec, before)
    update_handoff(env, rec, f"承認ゲート 1 で {' '.join(f'#{n}' for n in moved)} を人の判断待ちへ移し、残りで続ける", resume_command(rec))
    return loop_ok(
        f"承認ゲート 1 で {' '.join(f'#{n}' for n in moved)} を人の判断待ちへ移した。残り {' '.join(f'#{n}' for n in kept)} で続ける",
        rec,
        [{"waiting": moved}, *({"note": x} for x in notes)],
        status="continue",
        next_=pipeline,
        gate=1,
        waiting=moved,
        queue=queue,
        pipeline=pipeline,
    )


def handle_gate2(env: Env, rec: dict, before: dict, lap: dict) -> dict:
    """承認ゲート 2 を人へ戻す。本番を出さずに止まる。"""
    row = last_verdict(lap["state_path"], "release")
    lap.setdefault("gates", []).append(gate_copy(GATE_2, row))
    values = {"pr": row.get("pr"), "verdict": row.get("verdict", "unknown"), "reasons": row.get("reasons") or []}
    stop = {"condition": "gate2_returned", "values": values}
    return stop_and_report(env, rec, before, stop, "承認ゲート 2 を人へ戻した。本番を出さずに止まる（利用者の承認を求める）", {"gate": 2})


def move_to_waiting(env: Env, rec: dict, lap: dict, gate1: list[dict]) -> tuple[list[int], list[int], list[str]]:
    """承認ゲート 1 で止まった計画の課題を人の判断待ちへ移す。移した課題・残る課題・書けなかった理由を返す。"""
    moved, notes = [], []
    for i in gate1:
        for n in plan_info(i["plan"]).get("課題", []):
            row = last_verdict(lap["state_path"], "design", n)
            reasons = row.get("reasons") or []
            w = {
                "number": int(n), "reason": "／".join(reasons) or row.get("verdict", "判定を読めない"), "verdict": row.get("verdict", "unknown"),
                "basis": row.get("basis") or [], "gate": 1, "pr": row.get("pr"), "lap": lap["n"], "at": env.now(),
            }  # fmt: skip
            rec["waiting"].append(w)
            lap.setdefault("gates", []).append(gate_copy(GATE_1, row))
            moved.append(int(n))
            if err := mark_issue(env, rec, w):
                notes.append(err)
    kept = [n for n in (lap.get("issues_kept") or selected_issues(lap)) if n not in moved]
    lap["issues_kept"] = kept
    return moved, kept, notes


def continuation(env: Env, rec: dict, lap: dict, moved: list[int], kept: list[int]) -> tuple[str, Path]:
    """承認ゲート 1 の後の続きの queue: 関門 1 の後の then_of のステージから、外した課題だけの計画を除き、
    外した課題を除いた new close --pace auto を最後の --then につなぐ。"""
    index = json.loads(Path(lap["manifest"]).read_text(encoding="utf-8"))["ステージ"]
    at = next(i for i, e in enumerate(index) if e.get("name") == GATE_1)
    stages = []
    for e in index[at + 1 :]:
        plans = [
            p for p in e.get("plans", []) if not (set(plan_info(p).get("課題", [])) and set(plan_info(p).get("課題", [])) <= set(moved))
        ]
        if plans:
            stages.append(plans)
    tag = f"close-{len(lap.get('dones', [])) + 1}"
    stages.append(build_close(env, rec, lap, kept, tag=tag))
    done = Path(lap["dir"]) / f"done-{len(lap.get('dones', [])) + 1}.json"
    lap.setdefault("dones", []).append(str(done))
    head = f"python3 {shlex.quote(str(SUPERVISE))} queue " + " ".join(map(shlex.quote, stages[0])) + " --max 3"
    return head + "".join(" --then " + " ".join(map(shlex.quote, s)) for s in stages[1:]) + f" --done {shlex.quote(str(done))}", done


# ---------------------------------------------------------------- lap


def failure_key(state: dict) -> dict | None:
    """スプリント状態の行（完了でもまだでもない最初の行）の課題・種類・止まったところ（決定 8）。理由の文は含めない。"""
    for p in state.get("plans", []):
        row = p.get("row") or {}
        if row.get("result") not in (None, "まだ", NOT_RUN, *FINISHED):  # 関門は loop gate が扱い、流さなかった計画は失敗の元でない
            return {"issues": sorted(p.get("issues") or []), "kind": p.get("kind", ""), "step": row.get("result")}
    return None


def copy_state_gates(lap: dict, state: dict) -> None:
    """スプリント状態の承認ゲートを周回へ写す（写してあるものは重ねない）。"""
    for g in state.get("gates", []):
        copy = {
            "name": g.get("name"),
            "by": g.get("by", "user"),
            "verdict": g.get("verdict", g.get("outcome", "approved")),
            "basis": g.get("basis") or [],
            "pr": g.get("pr"),
            "at": g.get("at", ""),
        }
        if copy not in lap.setdefault("gates", []):
            lap["gates"].append(copy)


def refine_summary(env: Env, rec: dict) -> dict:
    """棚卸しの結果（upkeep の state dir の candidates.json と rank.json）を周回の記録の形にする。"""
    sd = env.upkeep_dir(rec.get("repo") or "")
    cands = jsonio.read(sd / "candidates.json", missing={}, broken={}, want=dict)
    rank = jsonio.read(sd / "rank.json", missing={}, broken={}, want=dict)
    return {
        "report": str(sd / "candidates.json") if cands else "",
        "rank_changes": (rank.get("metrics") or {}).get("changes") or [],
        "deferred": (cands.get("metrics") or {}).get("deferred") or [],
    }


def close_lap(env: Env, rec: dict, lap: dict, outcome: str, state: dict | None = None, deferred_issues=(), done_rows=()) -> None:
    """周回を締め、スプリント状態とまとめの記録から値を写して固定する。"""
    if lap.get("outcome") != IN_PROGRESS:
        raise LoopError(f"周回 {lap['n']} は締めてある（I1）")
    state = (
        state if state is not None else (jsonio.read(lap["state_path"], missing={}, broken={}, want=dict) if lap.get("state_path") else {})
    )
    rows = [p.get("row") or {} for p in state.get("plans", [])]
    lap["cost"] = round(sum(float(r.get("cost") or 0) for r in rows), 6)
    lap["seconds"] = round(sum(float(r.get("seconds") or 0) for r in rows), 3)
    lap["versions"] = state.get("versions") or lap.get("versions") or {}
    copy_state_gates(lap, state)
    close_rep = next((p for p in state.get("plans", []) if p.get("kind") == "まとめ"), None)
    if close_rep:
        lap["retro"] = (close_rep.get("row") or {}).get("report", "")
    lap["refine"] = refine_summary(env, rec)
    lap["deferred_issues"] = sorted(set(lap.get("deferred_issues") or []) | {int(n) for n in deferred_issues})
    lap["failure"] = failure_key(state) if outcome == FAILED else None
    if outcome == FAILED and lap["failure"] is None:
        lap["failure"] = {"issues": selected_issues(lap), "kind": "周回", "step": "conductor"}
    lap["relay_dir"] = os.environ.get("NDF_RELAY_DIR")
    lap["outcome"], lap["ended_at"] = outcome, env.now()


def after_close(env: Env, rec: dict, before: dict, summary: str) -> dict:
    """周回を締めた後の境の判定。止まれば 3、続けば ndf_next を返す。"""
    stop = boundary_stop(env, rec)
    if stop:
        return stop_and_report(env, rec, before, stop, f"{summary}。止まる条件に当たった（{stop['condition']}）")
    write_record(env, rec, before)
    nxt = resume_command(rec)
    update_handoff(env, rec, f"周回 {rec['laps'][-1]['n']} を締めた。次の周回へ", nxt)
    return loop_ok(f"{summary}。次の周回へ進む", rec, [rec["laps"][-1]], status="continue", next_=nxt, ndf_next=f"```ndf-next\n{nxt}\n```")
