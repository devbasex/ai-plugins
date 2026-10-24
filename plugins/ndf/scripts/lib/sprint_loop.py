"""sprint_loop.py: 開発ループの集約（#2089）。`sprint-state.py loop` の副命令だけが呼ぶ。LLM を呼ばない。

ループの記録は、メインディレクトリの `.ndf/loop/<名>/loop.json`（正本）と `laps.md`（描画）である。コミットしない。
周回はスプリント状態ファイルを ID（パス）で参照し、周回を締めるときに費用・秒・承認ゲートの判定・版を写して固定する。
止まる条件（予算・同じ失敗・止める指示）は周回の境（周回の開始の前と終わりの後）でだけ判定する。周回の途中で止めるのは、
承認ゲート 2 を人へ戻した（`gate2_returned`）・プロジェクト MVV が改訂された（`mvv_changed`）・周回を組めなかった
（`refused`）の 3 つだけで、周回は進行中のまま残す。

外の世界（gh・`upkeep.py rank`・`supervise.py`・`sprint-state.py` の別の副命令）は `Env` の関数を通して呼び、テストで
差し替える。

記録と止まる条件は `sprint_loop_record.py`、選定は `sprint_loop_select.py`、周回の締めと承認ゲートは
`sprint_loop_gate.py` にある。
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from typing import Callable

import gate_rejections as gr
import jsonio
import project_mvv as pm
import repo as repo_lib
import sprint_mvv
import step_result
from pace import PaceError, read_loop, read_pace

from sprint_loop_record import (
    TOOL,
    SPRINT_STATE,
    SUPERVISE,
    LAPS_MD,
    STOP_FILE,
    VERSION,
    EXIT_REFUSED,
    EXIT_STOPPED,
    DONE,
    FAILED,
    RETURNED,
    GATE_1,
    GATE_2,
    FINISHED,
    NOT_RUN,
    LoopError,
    Env,
    loop_dir,
    loop_record_path,
    read_loop_record,
    write_record,
    locked,
    current_lap,
    consumed,
    resume_command,
    stop_line,
    stopped_result,
    handoff_path,
    update_handoff,
    lap_rows,
)

from sprint_loop_gate import (
    loop_ok,
    select_cmd,
    wait_lap,
    selected_issues,
    refusal_order,
    build_close,
    _refused,
    copy_mvv_gate,
    read_done_items,
    mark_issue,
    handle_gate,
    failure_key,
    close_lap,
    after_close,
)


# ---------------------------------------------------------------- start


def refuse_loop(summary: str, reasons: list[str]) -> dict:
    nxt = "/ndf:development-workflow #<課題>（1 スプリントの進め方）"
    return {
        **step_result.result(TOOL, "stopped", summary, [{"reason": r} for r in reasons], {"status": "refused"}, next=nxt),
        "exit": EXIT_REFUSED,
    }


def permission(env: Env) -> tuple[dict | None, list[str], object]:
    """ループを使ってよい条件（受け入れ条件 2）。(loop 節, 断る理由, プロジェクト MVV)。"""
    root, why = env.main_dir(), []
    try:
        decl = read_loop(root)
    except PaceError as e:
        return None, [f"ループの設定を読めない: {e}"], None
    if not decl["enabled"]:
        why.append(".ndf/pace.json の loop.enabled が真でない（ループを許していない）")
    try:
        auto = read_pace(root)["auto"]
        if not (auto["enabled"] and auto["verify"]):
            why.append(".ndf/pace.json の auto 節（enabled と verify）が無い。周回は pace: auto で組む")
    except PaceError as e:
        why.append(f"進め方の宣言を読めない: {e}")
    project = pm.load_mvv(root)
    if not project.approved:
        why.append(f"プロジェクト MVV が承認済みでない（{project.status}）")
    return decl, why, project


def apply_budget(env: Env, rec: dict, given: dict) -> None:
    over = {k: v for k, v in given.items() if v is not None}
    if not over:
        return
    new = {**rec["budget"], **over}
    if new != rec["budget"]:
        rec.setdefault("budget_history", []).append({"at": env.now(), "from": dict(rec["budget"]), "to": new})
        rec["budget"] = new


def cmd_start(env: Env, name: str, milestones: list[str], budget: dict, goal: bool = True) -> dict:
    with locked(env, name):
        decl, why, project = permission(env)
        p = loop_record_path(env, name)
        if why:
            return refuse_loop("開発ループを始めない（使ってよい条件に当たらない）", why)
        if not p.is_file():
            return start_new(env, name, decl, project, milestones, budget, goal)
        rec = read_loop_record(env, name)
        before = json.loads(json.dumps(rec))
        apply_budget(env, rec, budget)
        if rec.get("status") == "stopped":
            return resume_stopped(env, rec, before)
        write_record(env, rec, before)
        lap = current_lap(rec)
        status = "in_lap" if lap else "resumed"
        nxt = f"引継ぎ文書 {handoff_path(env, name)} の「次にやること」から続ける" if lap else select_cmd(name)
        return loop_ok(f"開発ループ {name} を続ける（{status}）", rec, status=status, next_=nxt)


def start_new(env: Env, name: str, decl: dict, project, milestones: list[str], budget: dict, goal: bool) -> dict:
    rec = {
        "version": VERSION,
        "name": name,
        "repo": repo_lib.owner_repo(env.main_dir()) or "",
        "started_at": env.now(),
        "project_mvv": {"version": project.version, "sha256": project.sha256},
        "budget": dict(decl["budget"]),
        "budget_history": [],
        "same_failure": decl["same_failure"],
        "max_issues": decl["max_issues"],
        "milestones": list(milestones or []),
        "goal": goal,
        "status": "running",
        "stop": None,
        "stops": [],
        "laps": [],
        "waiting": [],
        "excluded": [],
        "sprint_mvv_approvals": [],
    }
    apply_budget(env, rec, budget)
    write_record(env, rec)
    update_handoff(env, rec, "始めた。次は選定", resume_command(rec))
    return loop_ok(f"開発ループ {name} を始めた", rec, status="new", next_=select_cmd(name))


def resume_stopped(env: Env, rec: dict, before: dict) -> dict:
    """止まった理由ごとに解けたかを見る（I7）。解ければ stop を stops へ移す。"""
    stop, name = rec["stop"], rec["name"]
    cond, nxt = stop["condition"], select_cmd(name)
    lap = current_lap(rec)
    if cond == "mvv_changed":
        return stopped_result(
            env,
            rec,
            "プロジェクト MVV が改訂されたため、このループは再開しない。新しい名前でループを始め直す（/ndf:development-workflow --loop <新しい名前>）",
        )
    if cond == "user_stop":
        (loop_dir(env, name) / STOP_FILE).unlink(missing_ok=True)
    elif cond.startswith("budget_"):
        unit = cond.removeprefix("budget_")
        used = consumed(rec)[unit]
        if used >= rec["budget"][unit]:
            write_record(env, rec, before)
            return stopped_result(
                env, rec, f"予算（{unit}）を超えたまま（消費 {used} / 予算 {rec['budget'][unit]}）。--budget-{unit} で上書きして再開する"
            )
    elif cond == "gate2_returned" and lap:
        answer = gate2_answer(lap)
        if answer is None:
            return stopped_result(env, rec, "承認ゲート 2 の答え（承認か差し戻し）がまだ無い")
        if answer == "rejected":
            close_lap(env, rec, lap, RETURNED)
        else:
            nxt = gate2_pipeline(env, rec, lap, stop)
    elif cond == "refused" and lap:
        nxt = f"python3 {shlex.quote(str(SPRINT_STATE))} loop sprint {name} --mode <モード> --dev <開発版>（モードと版を決め直す）"
    rec["stops"].append({**stop, "resumed_at": env.now()})
    rec["stop"], rec["status"] = None, "running"
    write_record(env, rec, before)
    update_handoff(env, rec, f"再開した（{cond} が解けた）", resume_command(rec))
    return loop_ok(f"開発ループ {name} を再開した（{cond} が解けた）", rec, status="resumed", next_=nxt, resolved=cond)


def gate2_answer(lap: dict) -> str | None:
    """周回の状態に承認ゲート 2 の利用者の承認があれば approved、差し戻しの記録があれば rejected、無ければ None。"""
    state = jsonio.read(lap.get("state_path") or "", missing={}, broken={}, want=dict) if lap.get("state_path") else {}
    if any(g.get("name") == GATE_2 and g.get("by") != "mvv" for g in state.get("gates", [])):
        return "approved"
    try:
        rows = gr.read_rejections()
    except jsonio.JsonReadError:
        rows = []
    sp = str(Path(lap.get("state_path") or "").resolve()) if lap.get("state_path") else None
    if any(r.get("gate") == GATE_2 and r.get("sprint") == sp for r in rows):
        return "rejected"
    return None


def gate2_pipeline(env: Env, rec: dict, lap: dict, stop: dict) -> str:
    """承認ゲート 2 の承認の後の続き: 本番のプランを bump から続け → まとめの queue → wait → loop lap の 1 行。"""
    pr = (stop.get("values") or {}).get("pr")
    close = build_close(env, rec, lap, lap.get("issues_kept") or selected_issues(lap), record_pr=pr)
    done = Path(lap["dir"]) / f"done-{len(lap.get('dones', [])) + 1}.json"
    prod = lap.get("prod_plan") or "<本番のプラン>"
    run = f"python3 {shlex.quote(str(SUPERVISE))} run {shlex.quote(prod)} --from bump"
    queue = (
        f"python3 {shlex.quote(str(SUPERVISE))} queue " + " ".join(map(shlex.quote, close)) + f" --max 3 --done {shlex.quote(str(done))}"
    )
    lap.setdefault("dones", []).append(str(done))
    return f"{run} && {queue} && {wait_lap(rec, done)}"


# ---------------------------------------------------------------- sprint


def cmd_sprint(env: Env, name: str, mode: str, dev: str, prod: str | None, extra: list[str]) -> dict:
    with locked(env, name):
        rec = read_loop_record(env, name)
        before = json.loads(json.dumps(rec))
        lap = current_lap(rec)
        if not lap:
            return refusal_order("進行中の周回が無い（loop select で選んでから組む）")
        if rec.get("status") == "stopped" and (rec.get("stop") or {}).get("condition") != "refused":
            return stopped_result(env, rec, "ループは止まっている")
        issues = selected_issues(lap)
        d = Path(lap["dir"])
        d.mkdir(parents=True, exist_ok=True)
        state = d / "state.json"
        lap.update(mode=mode, versions={"dev": dev, "prod": prod or re.sub(r"-.*$", "", dev)}, state_path=str(state))
        approved = {w["number"] for w in rec.get("waiting", []) if w.get("answer") == "approved"}
        root = str(env.main_dir())
        init = [sys.executable, str(SPRINT_STATE), "init", str(state), "--name", lap["sprint"], "--pace", "auto", "--root", root]
        init += [x for n in issues for x in ("--issue", str(n))] + ["--dev", dev, "--prod", lap["versions"]["prod"]]
        init += ["--goal", resume_command(rec)]
        mvv = d / "mvv.md"
        if mvv.is_file():
            init += ["--mvv", str(mvv), "--milestone", lap["milestone"]]
        code, so, se = env.run(init, cwd=root)
        if code != 0:
            return _refused(env, rec, before, "sprint-state.py init", code, (se or so).strip())
        if mvv.is_file():
            copy_mvv_gate(rec, lap, state, mvv)
        design = [n for n in issues if n not in approved]
        new = [sys.executable, str(SUPERVISE), "new", "sprint", "--pace", "auto", "--state", str(state), "--name", lap["sprint"]]
        new += ["--worktree", root, "--issue", *map(str, issues), "--mode", mode, "--version", dev, "--out", lap["out"]]
        if design:
            new += ["--design", *map(str, design)]
        new += list(extra)
        code, so, se = env.run(new, cwd=root)
        lap["sprint_command"] = shlex.join(new)
        if code != 0:
            return _refused(env, rec, before, "supervise.py new sprint --pace auto", code, (se or so).strip())
        try:
            close = build_close(env, rec, lap, issues)
        except (LoopError, OSError, ValueError) as e:
            return _refused(env, rec, before, "supervise.py new close --pace auto", 1, str(e))
        manifest = Path(lap["out"]) / "sprint.json"
        index = json.loads(manifest.read_text(encoding="utf-8"))["ステージ"]
        lap["manifest"] = str(manifest)
        lap["prod_plan"] = next((p for e in index if e.get("name") == "本番" for p in e.get("plans", [])), None)
        first = next(e for e in index if e.get("command"))
        done = d / "done-1.json"
        lap["dones"] = [str(done)]
        queue = first["command"] + " --then " + " ".join(map(shlex.quote, close)) + f" --done {shlex.quote(str(done))}"
        pipeline = f"{queue} && {wait_lap(rec, done)}"
        if rec.get("status") == "stopped":  # refused からのやり直し
            rec["stops"].append({**rec["stop"], "resumed_at": env.now()})
            rec["stop"], rec["status"] = None, "running"
        write_record(env, rec, before)
        update_handoff(env, rec, f"周回 {lap['n']} のスプリントを組んだ。pipeline を流す", resume_command(rec))
        return loop_ok(
            f"周回 {lap['n']} のスプリント {lap['sprint']} を pace: auto で組んだ",
            rec,
            [{"command": lap["sprint_command"]}, {"command": lap["close_command"]}],
            status="sprint",
            next_=pipeline,
            state_path=str(state),
            out=lap["out"],
            manifest=str(manifest),
            close_command=lap["close_command"],
            queue=queue,
            pipeline=pipeline,
        )


# ---------------------------------------------------------------- answer / approve-mvv / stop


def cmd_answer(env: Env, name: str, number: int, approved: bool, pr: int | None) -> dict:
    with locked(env, name):
        rec = read_loop_record(env, name)
        before = json.loads(json.dumps(rec))
        w = next((w for w in reversed(rec.get("waiting", [])) if w["number"] == number and not w.get("answer")), None)
        if w is None:
            return refusal_order(f"#{number} は人の判断待ちでない")
        w.update(answer="approved" if approved else "rejected", answered_at=env.now())
        if pr:
            w["pr"] = pr
        lap = rec["laps"][w["lap"] - 1]
        state = lap.get("state_path")
        notes = []
        if state:
            cmd = [
                sys.executable,
                str(SPRINT_STATE),
                "gate",
                state,
                GATE_1,
                "--by",
                "user",
                "--what",
                f"開発ループ {name} の #{number} の設計",
            ]
            cmd += ["--outcome", w["answer"], "--root", str(env.main_dir())] + (["--pr", str(w["pr"])] if w.get("pr") else [])
            code, so, se = env.run(cmd, cwd=str(env.main_dir()))
            if code != 0:
                notes.append(f"承認の記録を書けない（{code}）: {(se or so).strip()[:200]}")
        if err := mark_issue(env, rec, w):
            notes.append(err)
        write_record(env, rec, before)
        after = "設計 PR のマージの後の周回で選定の対象に戻る" if approved else "選定の対象に戻さない（差し戻し）"
        return loop_ok(f"#{number} の答え（{w['answer']}）を書いた。{after}", rec, [w, *({"note": n} for n in notes)], status="answered")


def cmd_approve_mvv(env: Env, name: str, milestone: str) -> dict:
    with locked(env, name):
        rec = read_loop_record(env, name)
        before = json.loads(json.dumps(rec))
        try:
            text = sprint_mvv.mvv_sections(env.milestones(rec.get("repo") or "").get(milestone) or "")
        except OSError as e:
            return refusal_order(str(e))
        if text is None:
            return {
                **step_result.result(
                    TOOL,
                    "stopped",
                    f"マイルストーン {milestone} の説明に ## Mission / ## Vision / ## Value がそろわない",
                    [],
                    {"status": "stopped"},
                ),
                "exit": EXIT_STOPPED,
            }
        row = {"milestone": milestone, "sha256": pm.sha256_text(text), "at": env.now(), "by": "user"}
        rec["sprint_mvv_approvals"].append(row)
        write_record(env, rec, before)
        return loop_ok(f"マイルストーン {milestone} のスプリント MVV を承認した（{row['sha256'][:12]}）", rec, [row], status="approved")


def cmd_stop(env: Env, name: str) -> dict:
    read_loop_record(env, name)
    p = loop_dir(env, name) / STOP_FILE
    p.write_text(env.now() + "\n", encoding="utf-8")
    return step_result.result(TOOL, "ok", f"止める指示を置いた（次の周回の境で効く）: {p}", [{"path": str(p)}], {"path": str(p)})


# ---------------------------------------------------------------- lap


def cmd_lap(env: Env, name: str, failed: bool, done: str | None, deferred_issues: list[int], update: Callable | None = None) -> dict:
    with locked(env, name):
        rec = read_loop_record(env, name)
        before = json.loads(json.dumps(rec))
        lap = current_lap(rec)
        if not lap:
            return refusal_order("進行中の周回が無い")
        items = read_done_items(done) if done else []
        if any(i.get("result") == "関門" for i in items):
            return handle_gate(env, rec, before, lap, items)
        if update and lap.get("state_path"):
            update(lap["state_path"], [d for d in lap.get("dones", []) if Path(d).is_file()])
        state = jsonio.read(lap["state_path"], missing={}, broken={}, want=dict) if lap.get("state_path") else {}
        bad = [i for i in items if i.get("result") not in FINISHED and i.get("result") != NOT_RUN]
        outcome = FAILED if failed or bad or failure_key(state) else DONE
        close_lap(env, rec, lap, outcome, state, deferred_issues)
        return after_close(env, rec, before, f"周回 {lap['n']} を {outcome} で締めた")


# ---------------------------------------------------------------- status / list


def relay_stopped(lap: dict | None) -> bool:
    """最後の周回のラッパーの記録に起動回数の上限（max-starts）の stop の行があるか。"""
    d = (lap or {}).get("relay_dir")
    if not d:
        return False
    try:
        rows = jsonio.read_jsonl(Path(d) / "log.jsonl", missing=[], skip_broken=True)
    except jsonio.JsonReadError:
        return False
    return any(r.get("event") == "stop" and r.get("reason") == "max-starts" for r in rows)


def cmd_status(env: Env, name: str) -> dict:
    rec = read_loop_record(env, name)
    lap = current_lap(rec)
    last = rec["laps"][-1] if rec.get("laps") else None
    lines = [
        f"ループ: {name}（{rec.get('status')}）",
        f"周回: {len(rec.get('laps', []))}（進行中 {lap['n'] if lap else 'なし'}）",
        f"消費: {consumed(rec)} / 予算 {rec.get('budget')}",
    ]
    waiting = [w["number"] for w in rec.get("waiting", []) if not w.get("answer")]
    if waiting:
        lines.append("人の判断待ち: " + " ".join(f"#{n}" for n in waiting))
    metrics = {"status": rec.get("status"), "consumed": consumed(rec), "budget": rec.get("budget"), "waiting": waiting}
    if rec.get("stop"):
        lines.append(f"止まった理由: {rec['stop']['condition']}。再開: {resume_command(rec)}")
        metrics.update(stop=rec["stop"], stop_line=stop_line(rec, rec["stop"]["condition"]))
    if relay_stopped(last):
        lines.append(f"ラッパーが起動回数の上限で止めた。再開: {resume_command(rec)}")
        metrics["relay_max_starts"] = True
    return step_result.result(TOOL, "ok", lines[0], lines[1:], metrics)


def cmd_list(env: Env, name: str) -> dict:
    rec = read_loop_record(env, name)
    rows = lap_rows(rec)
    return step_result.result(
        TOOL, "ok", f"ループ {name} の周回 {len(rows)} 件", rows, {"laps": len(rows), "path": str(loop_dir(env, name) / LAPS_MD)}
    )
