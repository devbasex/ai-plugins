"""sprint_loop.py: 開発ループの集約（#2089）。`sprint-state.py loop` の副命令だけが呼ぶ。LLM を呼ばない。

記録の置き場・ループの 1 回の固定・止まる条件・`Env` の決まりは `sprint_loop_record.py` の説明にある。選定は
`sprint_loop_select.py`、ループの 1 回の締めと承認ゲートは `sprint_loop_gate.py` にある。
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Callable

import design_pr_merge
import gate1_requests
import jsonio
import mode_judgment
import project_mvv as pm
import repo as repo_lib
import revise_note
import run_environment
import sprint_loop_gate
import sprint_mvv
import step_result
from pace import PaceError, hours_items, over_budget, read_loop, read_pace
from sprint_loop_argv import design_issues, lap_carry, new_sprint_argv, next_pipeline, sprint_refusal, sprint_state_init_argv, table_of

from sprint_loop_record import (
    TOOL,
    SPRINT_STATE,
    LAPS_MD,
    VERSION,
    BUDGET_DEFAULT,
    EXIT_REFUSED,
    EXIT_STOPPED,
    DONE,
    FAILED,
    RETURNED,
    QUESTION_PENDING,
    PENDING,
    DESIGN_GATE,
    FINISHED,
    NOT_RUN,
    LoopError,
    Env,
    ResumeContext,
    prod_of,
    loop_dir,
    loop_record_path,
    read_loop_record,
    write_record,
    locked,
    open_record,
    current_lap,
    consumed,
    resume_command,
    stop_line,
    stopped_result,
    handoff_path,
    update_handoff,
    lap_rows,
    pending_laps,
    selected_issues,
)

from sprint_loop_gate import (
    loop_ok,
    select_cmd,
    wait_lap,
    refusal_order,
    refused_stop,
    copy_mvv_gate,
    read_done_items,
    continuation,
    CONTINUATION_STEP,
    mark_issue,
    refresh_state,
    handle_gate,
    failure_key,
    close_lap,
    after_close,
    research_command,
    owner_line,
    mode_decision,
    sync_mode,
)

from sprint_loop_gate2 import finish_held, gate2_answer, gate2_pipeline, pending_pipeline, cmd_reclose as cmd_reclose
from sprint_loop_select import resume_answer_hold
from sprint_loop_stop import STOP_FILE, read_stop_order, stop_order_text


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
            why.append(".ndf/pace.json の auto 節（enabled と verify）が無い。ループの 1 回は pace: auto で組む")
    except PaceError as e:
        why.append(f"進め方の宣言を読めない: {e}")
    project = pm.load_mvv(root)
    if not project.approved:
        why.append(f"プロジェクト MVV が承認済みでない（{project.status}）")
    return decl, why, project


def apply_budget(env: Env, rec: dict, given: dict, defaults: dict) -> None:
    over = {k: (defaults[k] if v == BUDGET_DEFAULT else v) for k, v in given.items() if v is not None}
    new = {**rec["budget"], **over}
    if new != rec["budget"]:
        rec.setdefault("budget_history", []).append({"at": env.now(), "from": dict(rec["budget"]), "to": new})
        rec["budget"] = new


def cmd_start(env: Env, name: str, milestones: list[str], budget: dict, goal: bool = True) -> dict:
    out = start_or_resume(env, name, milestones, budget, goal)
    out["items"] = [*out["items"], *hours_items(env.main_dir())]  # 業務時間の宣言の誤り（#2913）。警告を出しても始める
    return out


def start_or_resume(env: Env, name: str, milestones: list[str], budget: dict, goal: bool) -> dict:
    with locked(env, name):
        decl, why, project = permission(env)
        p = loop_record_path(env, name)
        if why:
            return refuse_loop("開発ループを始めない（使ってよい条件に当たらない）", why)
        if not p.is_file():
            return start_new(env, name, decl, project, milestones, budget, goal)
        rec = read_loop_record(env, name)
        before = json.loads(json.dumps(rec))
        apply_budget(env, rec, budget, decl["budget"])
        report = run_environment.note(env.main_dir(), rec)
        if rec.get("status") == "stopped":
            return run_environment.with_report(resume_stopped(env, rec, before), report)
        write_record(env, rec, before)
        lap = current_lap(rec)
        status = "in_lap" if lap else "resumed"
        nxt = f"引継ぎ文書 {handoff_path(env, name)} の「次にやること」から続ける" if lap else select_cmd(name)
        return run_environment.with_report(loop_ok(f"開発ループ {name} を続ける（{status}）", rec, status=status, next_=nxt), report)


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
        "reorder_width": decl.get("reorder_width", 0),  # 逆転の幅。以後は選定も開始も読むだけ（#2997 の決定 2）
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
    apply_budget(env, rec, budget, decl["budget"])
    report = run_environment.note(env.main_dir(), rec)
    write_record(env, rec)
    update_handoff(env, rec, "始めた。次は選定", resume_command(rec), select_cmd(name))
    return run_environment.with_report(loop_ok(f"開発ループ {name} を始めた", rec, status="new", next_=select_cmd(name)), report)


def resume_record(env: Env, rec: dict) -> None:
    """止まった記録を解く: stop を再開の時刻つきで stops へ移し、running に戻す。"""
    rec["stops"].append({**rec["stop"], "resumed_at": env.now()})
    rec["stop"], rec["status"] = None, "running"


def _resume_mvv_changed(ctx: ResumeContext) -> tuple[dict | None, str]:
    why = "プロジェクト MVV が改訂されたため、このループは再開しない。新しい名前でループを始め直す（/ndf:development-workflow --loop <新しい名前>）"
    return stopped_result(ctx.env, ctx.rec, why), ctx.nxt


def _resume_user_stop(ctx: ResumeContext) -> tuple[dict | None, str]:
    (loop_dir(ctx.env, ctx.rec["name"]) / STOP_FILE).unlink(missing_ok=True)
    return None, ctx.nxt


def _resume_budget(ctx: ResumeContext) -> tuple[dict | None, str]:
    env, rec, stop, nxt = ctx.env, ctx.rec, ctx.stop, ctx.nxt
    unit = stop["condition"].removeprefix("budget_")
    used = consumed(rec)[unit]
    if over_budget(used, rec["budget"].get(unit)):
        write_record(env, rec, ctx.before)
        return (
            stopped_result(
                env, rec, f"予算（{unit}）を超えたまま（消費 {used} / 予算 {rec['budget'][unit]}）。--budget-{unit} で上書きして再開する"
            ),
            nxt,
        )
    return None, nxt


def _resume_gate2(ctx: ResumeContext) -> tuple[dict | None, str]:
    env, rec, stop, lap, nxt = ctx.env, ctx.rec, ctx.stop, ctx.lap, ctx.nxt
    if not lap:
        return None, nxt
    answer = gate2_answer(lap, stop.get("at"))
    if answer is None:
        return stopped_result(env, rec, "リリース承認ゲートの答え（承認か差し戻し）がまだ無い"), nxt
    if answer == "rejected":
        close_lap(env, rec, lap, RETURNED)
        return None, nxt
    return None, gate2_pipeline(env, rec, lap, stop)


def _resume_refused(ctx: ResumeContext) -> tuple[dict | None, str]:
    env, rec, stop, lap, nxt = ctx.env, ctx.rec, ctx.stop, ctx.lap, ctx.nxt
    if not lap:
        return None, nxt
    if (stop.get("values") or {}).get("step") != CONTINUATION_STEP:
        return (
            None,
            f"python3 {shlex.quote(str(SPRINT_STATE))} loop sprint {rec['name']} --judgments <判定の根拠のファイル>（判定の根拠を確かめて組み直す）",
        )
    # 設計承認ゲートの後の続きを組めなかった: 判断待ちへ移した課題を戻さず、残した課題で続きを組み直す
    moved = [w["number"] for w in rec.get("waiting", []) if w.get("lap") == lap["n"]]
    try:
        queue, done = continuation(env, rec, lap, moved, lap.get("issues_kept") or [])
    except (LoopError, OSError, ValueError, KeyError, StopIteration) as e:
        write_record(env, rec, ctx.before)
        return stopped_result(env, rec, f"設計承認ゲートの後の続きをまだ組めない: {str(e)[:200]}"), nxt
    return None, f"{queue} && {wait_lap(rec, done)}"


_RESUME_HANDLERS = {
    "mvv_changed": _resume_mvv_changed,
    "user_stop": _resume_user_stop,
    "gate2_returned": _resume_gate2,
    "refused": _resume_refused,
    "answer_hold": resume_answer_hold,
}


def _resume_handler(cond: str):
    """止まった理由に対応する再開の手続き。budget_* は接頭辞で引く。無ければ None（そのまま解く）。"""
    if cond.startswith("budget_"):
        return _resume_budget
    return _RESUME_HANDLERS.get(cond)


def resume_stopped(env: Env, rec: dict, before: dict) -> dict:
    """止まった理由ごとに解けたかを見る（I7）。解ければ stop を stops へ移す。"""
    stop, name = rec["stop"], rec["name"]
    cond, nxt = stop["condition"], select_cmd(name)
    handler = _resume_handler(cond)
    if handler:
        stopped, nxt = handler(ResumeContext(env, rec, before, stop, current_lap(rec), nxt))
        if stopped is not None:
            return stopped
    resume_record(env, rec)
    write_record(env, rec, before)
    update_handoff(env, rec, f"再開した（{cond} が解けた）", resume_command(rec), nxt)
    return loop_ok(f"開発ループ {name} を再開した（{cond} が解けた）", rec, status="resumed", next_=nxt, resolved=cond)


# ---------------------------------------------------------------- sprint


def read_judgments(path: str | None, selected: list[int]) -> list[dict]:
    """判定の根拠のファイルを読み、検査を通った根拠を返す（#2923 の I1・I2）。読めなければ JudgmentError。"""
    if not path:
        raise mode_judgment.JudgmentError(["判定の根拠のファイル（--judgments）が無い"])
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise mode_judgment.JudgmentError([f"判定の根拠のファイルを読めない（{path}）: {str(e)[:200]}"]) from e
    return mode_judgment.check_judgments(data, selected)


def judgment_refusal(reasons: list[str]) -> dict:
    """判定の根拠が欠ける・合わないときの断り（終了コード 1。記録は書き換えない）。"""
    summary = f"判定の根拠が契約に合わない（{len(reasons)} 件）。mode-materials.py で材料を集め、根拠を直して loop sprint を打ち直す"
    items = [{"reason": r} for r in reasons]
    return {**step_result.result(TOOL, "stopped", summary, items, {"status": "refused"}), "exit": EXIT_REFUSED}


def _sprint_blocked(env: Env, rec: dict, lap: dict | None) -> dict | None:
    """ループの 1 回を組めないときの断り（1 回が無い・止まっている・設計承認ゲートの後）。組めれば None。"""
    if not lap:
        return refusal_order("進行中のループの 1 回が無い（loop select で選んでから組む）")
    if rec.get("status") == "stopped" and (rec.get("stop") or {}).get("condition") != "refused":
        return stopped_result(env, rec, "ループは止まっている")
    if refused := sprint_refusal(rec, lap):  # 設計承認ゲートの後のループの 1 回・答え待ちの停止の範囲の設計（#2913 の I1）
        return {**refusal_order(refused[1]), "exit": refused[0]}
    return None


def _decide_mode(judgments: str | None, issues: list[int], mode: str | None) -> tuple[list[dict], str]:
    """判定の根拠からモードを決め、`mode` を渡したときは照合する。合わなければ JudgmentError。"""
    judged = read_judgments(judgments, issues)
    decided = mode_judgment.sprint_mode(judged)
    if mode and mode != decided:
        raise mode_judgment.JudgmentError([f"--mode {mode} が判定の根拠から決まるモード {decided} と違う"])
    return judged, decided


def _make_plans(env: Env, rec: dict, before: dict, lap: dict, issues: list[int], extra: list[str]) -> tuple[dict | None, list, str | None]:
    """スプリント状態の初期化・出し直しの指摘の入力・ループの 1 回の工程の進行表（`progress.py new --pace auto`）を作る（#3092）。
    (止めた結果, 飛ばした課題, 進行表の ID) を返す。まとめの工程も同じ進行表の行にある。"""
    root, state = str(env.main_dir()), Path(lap["state_path"])
    mvv = Path(lap["dir"]) / "mvv.md"
    code, so, se = env.run(sprint_state_init_argv(root, rec, lap, issues), cwd=root)
    if code != 0:
        return refused_stop(env, rec, before, "sprint-state.py init", code, (se or so).strip()), [], None
    if mvv.is_file():
        copy_mvv_gate(rec, lap, state, mvv)
    try:
        notes, skipped = revise_note.write_inputs(env.main_dir(), rec, design_issues(rec, issues), lap["dir"])
        new = new_sprint_argv(root, rec, lap, [*notes, *extra], lap_carry(rec, lap))
    except OSError as e:
        return refused_stop(env, rec, before, "出し直しの指摘の入力", 1, str(e)), [], None
    code, so, se = env.run(new, cwd=root)
    lap["sprint_command"] = shlex.join(new)
    table = table_of(so) if code == 0 else None
    if table is None:
        return refused_stop(env, rec, before, "progress.py new --pace auto", code or 1, (se or so).strip()), [], None
    return None, skipped, table


def cmd_sprint(
    env: Env, name: str, mode: str | None, dev: str | None, prod: str | None, extra: list[str], judgments: str | None = None
) -> dict:
    """ループの 1 回を組む。モードは判定の根拠のファイルから算出し、`mode` を渡したときは照合だけに使う（#2923 の決定 2）。
    根拠の検査はどの書き込みよりも前に置く（I1〜I3・I8 で断ったときに記録を変えない）。
    ループの 1 回は工程の進行表 1 つで流す（#3092）。`next` に返す 1 行が進行表を流し、`loop lap` で締める。"""
    dev = dev or ""
    if prod and not dev:
        return refusal_order("--prod は --dev と一緒に渡す（版を流す時点に決めるなら両方を省く）")
    with open_record(env, name) as (rec, before):
        lap = current_lap(rec)
        if blocked := _sprint_blocked(env, rec, lap):
            return blocked
        issues = selected_issues(lap)
        try:
            judged, mode = _decide_mode(judgments, issues, mode)
        except mode_judgment.JudgmentError as e:
            return judgment_refusal(e.reasons)
        d = Path(lap["dir"])
        d.mkdir(parents=True, exist_ok=True)
        state = d / "state.json"
        root = str(env.main_dir())
        lap.update(mode=mode, versions={"dev": dev, "prod": prod or prod_of(dev)}, state_path=str(state))
        if judgments:
            lap["judgments_path"] = str(Path(judgments).resolve())
        stopped, skipped, table = _make_plans(env, rec, before, lap, issues, extra)
        if stopped:
            return stopped
        lap["table"] = table
        done = d / "done-1.json"
        pipeline = next_pipeline(rec, table, done)
        lap["dones"] = [str(done)]
        lap.update(judgments=judged, mode_decisions=[mode_decision(env, "loop sprint", issues, None, mode)], sprint_extra=list(extra))
        if rec.get("status") == "stopped":  # refused からのやり直し
            resume_record(env, rec)
        write_record(env, rec, before)
        update_handoff(env, rec, f"ループの {lap['n']} 回目 の進行表を作った。pipeline を流す", resume_command(rec), pipeline)
        synced = sync_mode(env, issues, mode)
        return loop_ok(
            f"ループの {lap['n']} 回目 のスプリント {lap['sprint']} の進行表を pace: auto で作った",
            rec,
            [
                {"command": lap["sprint_command"]},
                *({"skipped_loop": x} for x in skipped),
                *({"note": x} for x in synced),
            ],
            status="sprint",
            mode=mode,
            judgments={str(j["number"]): j["row"] for j in judged},
            next_=pipeline,
            state_path=str(state),
            table=table,
            pipeline=pipeline,
            research=research_command(root, rec, lap, done),
        )


def cmd_answer(env: Env, name: str, number: int, approved: bool, pr: int | None, revise: bool = False, note: str | None = None) -> dict:
    with open_record(env, name) as (rec, before):
        w = next((w for w in reversed(rec.get("waiting", [])) if w["number"] == number and not w.get("answer")), None)
        if w is None:
            return refusal_order(f"#{number} は人の判断待ちでない")
        if revise:
            try:
                text = Path(note or "").read_text(encoding="utf-8").strip()
            except OSError as e:
                return refusal_order(f"指摘ファイルを読めない: {e}")
        if pr:
            w["pr"] = pr
        if approved and w.get("pr") and (moved := regated_answer(env, w)):
            write_record(env, rec, before)
            return loop_ok(moved, rec, [w], status="regated")
        notes = apply_answer(env, rec, w, "revise" if revise else ("approved" if approved else "rejected"), pr, text if revise else None)
        merge = None
        if approved and w.get("pr"):  # 承認した設計のマージ（#2507）。成否によらず答えは書き、0 で返る（I4）
            out = design_pr_merge.merge_approved(env.main_dir(), int(w["pr"]), w.get("stamp"), run=env.run)
            merge = sprint_loop_gate.note_merge(w, out, w.get("lap") or len(rec["laps"]), env.now())
        write_record(env, rec, before)
        after = answer_after(approved, revise, merge)
        return loop_ok(f"#{number} の答え（{w['answer']}）を書いた。{after}", rec, [w, *({"note": n} for n in notes)], status="answered")


def answer_after(approved: bool, revise: bool, merge: dict | None) -> str:
    """答えを書いた後に人へ示す、この後の扱いの文面を返す（答えの種別と設計 PR のマージの結果から決める）。"""
    if revise:
        return "ループの次の 1 回で先頭に選び、指摘を読んで同じ設計 Pull Request を直す"
    if not approved:
        return "選定の対象に戻さない（差し戻し）"
    if merge and merge["result"] in sprint_loop_gate.MERGED_RESULTS:
        return "設計 PR をマージした。ループの次の 1 回で先頭に選ぶ"
    if merge and merge["result"] == "production":
        return f"設計 PR をマージしない（{merge['reason']}）。リリース承認ゲートで人がマージするまで実装に選ばない"
    if merge:
        return f"設計 PR をマージできない（{merge['result']}: {merge['reason']}）。ループの次の 1 回の境でマージし直す"
    return "設計 PR のマージの後のループの 1 回で選定の対象に戻る"


def regated_answer(env: Env, w: dict) -> str | None:
    """1 本のループの承認の答えの前に、承認した先端と今の先端を比べる（#2507）。違えば（または行に承認した先端が無ければ）、
    答えを当てずに行の承認した先端を今の先端へ書き換え、答え直しを促す 1 行を返す。読めなければ比べない（マージの部品が読み直す）。"""
    head = sprint_loop_gate.design_head(env, w)
    stamp = w.get("stamp") or ""
    if not head or head == stamp or (stamp.strip("0") and gate1_requests.carried_over(env, w, head)):
        return None
    w.update(stamp=head, restamped=stamp)
    if not stamp.strip("0"):
        return (
            f"#{w['number']} の設計 PR #{w['pr']} の承認した先端が記録に無い。今の先端 {head[:12]} を記録した。この先端を見てから答え直す"
        )
    return f"#{w['number']} の設計 PR #{w['pr']} の先端が問いの後に動いた（{stamp[:12]} → {head[:12]}）。新しい先端を見てから答え直す"


def apply_answer(env: Env, rec: dict, w: dict, answer: str, pr: int | None, note: str | None = None) -> list[str]:
    """人の判断待ちの行へ答えを書き、スプリント状態の承認の記録と課題の本文の節を更新する。書けなかった理由を返す。
    直して出し直す（revise）は答えの確定でないため承認の記録を書かず、指摘と回数を行と本文に書く（#2436 の 6e）。"""
    w.update(answer=answer, answered_at=env.now())
    if pr:
        w["pr"] = pr
    notes: list[str] = []
    if answer == "revise":
        w["note"] = note or ""
        revise_note.count_round(rec, w)
        if err := mark_issue(env, rec, w):
            notes.append(err)
        return notes
    lap = rec["laps"][w["lap"] - 1]
    state = lap.get("state_path")
    if state and w.get("by") != "mvv":  # MVV 判定で承認した答え（#2507）は mvv-gate.py が承認の記録を書いた。書き直さない
        cmd = [
            sys.executable,
            str(SPRINT_STATE),
            "gate",
            state,
            DESIGN_GATE,
            "--by",
            "user",
            "--what",
            f"開発ループ {rec['name']} の #{w['number']} の設計",
        ]
        cmd += ["--outcome", w["answer"], "--root", str(env.main_dir())] + (["--pr", str(w["pr"])] if w.get("pr") else [])
        code, so, se = env.run(cmd, cwd=str(env.main_dir()))
        if code != 0:
            notes.append(f"承認の記録を書けない（{code}）: {(se or so).strip()[:200]}")
    if err := mark_issue(env, rec, w):
        notes.append(err)
    return notes


def apply_gate1_answers(env: Env, rec: dict, answers: dict) -> None:
    """オーガナイザーへ送った設計の承認の答えを、持ち主のレーンがループの 1 回の境で処理する（#2436 の 6e・6f）。
    承認ならラベル design-approved を付けて承認を書き、直して出し直すなら指摘を、やらないなら差し戻しを書く。"""
    for w in [w for w in rec.get("waiting", []) if w.get("request") and not w.get("answer")]:
        a = (answers.get(w["request"]) or {}).get("answer")
        if not isinstance(a, dict):  # 問い直した行は、前の依頼への答えも読む（#2510 の I10）
            a = gate1_requests.answer_before(env, w, answers)
        if not isinstance(a, dict):
            continue
        result = a.get("result")
        if result == "approved" and w.get("pr"):
            if sprint_loop_gate.regate1(env, w):  # 問いの後に設計 PR が動いた: 新しい先端で問い直す
                continue
            code, so, se = env.run(["gh", "pr", "edit", str(w["pr"]), "--add-label", "design-approved"], cwd=str(env.main_dir()))
            if code != 0:
                w["label_error"] = (se or so).strip()[:200]
            w["by"] = "mvv" if a.get("by") == "mvv" else "user"
        if result in ("approved", "rejected", "revise"):
            apply_answer(env, rec, w, result, w.get("pr"), a.get("note"))
        if result == "approved" and isinstance(a.get("merge"), dict):  # オーガナイザーが答えの前に試みたマージの結果を写す（#2507）
            sprint_loop_gate.note_merge(w, a["merge"], w.get("lap") or len(rec["laps"]), env.now())


def cmd_approve_mvv(env: Env, name: str, milestone: str) -> dict:
    with open_record(env, name) as (rec, before):
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


def cmd_stop(env: Env, name: str, by: str = "unknown", reason: str = "") -> dict:
    read_loop_record(env, name)
    (p := loop_dir(env, name) / STOP_FILE).write_text(stop_order_text(env.now(), by, reason), encoding="utf-8")
    order = {"path": str(p), **read_stop_order(p)}
    return step_result.result(TOOL, "ok", f"止める指示を置いた（主体 {by}。ループの次の 1 回の境で効く）: {p}", [order], order)


# ---------------------------------------------------------------- lap


def _lap_by_table(env: Env, rec: dict, before: dict, lap: dict, failed: bool, done: str | None, deferred_issues: list[int]) -> dict:
    """工程の進行表で流したループの 1 回を、`progress.py next` の出力（done）で締める（#3092）。

    完了なら完了、工程の失敗・中止なら失敗で締める。答え待ち（`gate`）では締めず、答えの後に同じ 1 行を打ち直す。"""
    rep = jsonio.read(done, missing={}, broken={}, want=dict) if done else {}
    status = rep.get("status")
    if status == "gate" and not failed:
        pipeline = next_pipeline(rec, lap["table"], Path(done))
        write_record(env, rec, before)
        update_handoff(env, rec, f"ループの {lap['n']} 回目 の進行表が答えを待っている", resume_command(rec), pipeline)
        return loop_ok(
            f"ループの {lap['n']} 回目 の進行表が答えを待っている: {rep.get('summary', '')}",
            rec,
            rep.get("items") or [],
            status="gate",
            next_=f"progress.py answer で答えてから: {pipeline}",
            table=lap["table"],
            pipeline=pipeline,
        )
    state = jsonio.read(lap["state_path"], missing={}, broken={}, want=dict) if lap.get("state_path") else {}
    ok = status == "ok" if done else failure_key(state) is None  # done を渡さない締めは、今までどおりスプリント状態の行で決める
    outcome = DONE if ok and not failed else FAILED
    close_lap(env, rec, lap, outcome, state, deferred_issues)
    rows = [i.get("row") for i in rep.get("items") or [] if i.get("kind") == "failed" and i.get("row")]
    if outcome == FAILED and rows:  # 同じ失敗の判定（failure_streak）が読む鍵: 課題・失敗した工程（理由の文は含めない）
        lap["failure"] = {"issues": selected_issues(lap), "kind": rows[0], "step": "失敗"}
    return after_close(env, rec, before, f"ループの {lap['n']} 回目 を {outcome} で締めた")


def cmd_lap(env: Env, name: str, failed: bool, done: str | None, deferred_issues: list[int], update: Callable | None = None) -> dict:
    with open_record(env, name) as (rec, before):
        lap = current_lap(rec)
        if not lap:
            return refusal_order("進行中のループの 1 回が無い")
        if lap.get("table"):
            return _lap_by_table(env, rec, before, lap, failed, done, deferred_issues)
        items = read_done_items(done) if done else []
        refresh_state(lap, update)
        if any(i.get("result") == "関門" for i in items):
            return handle_gate(env, rec, before, lap, items)
        state = jsonio.read(lap["state_path"], missing={}, broken={}, want=dict) if lap.get("state_path") else {}
        # 差し込みを埋められずに流さなかった計画（unfilled。記録の PR が見つからないまとめなど）は失敗に数える（#2230 の決定 7）
        bad = [i for i in items if i.get("unfilled") or (i.get("result") not in FINISHED and i.get("result") != NOT_RUN)]
        outcome = FAILED if failed or bad or failure_key(state) else DONE
        close_lap(env, rec, lap, outcome, state, deferred_issues)
        return after_close(env, rec, before, f"ループの {lap['n']} 回目 を {outcome} で締めた")


def cmd_finish(env: Env, name: str, n: int, done: str, update: Callable | None = None) -> dict:
    """保留したループの 1 回（リリース待ち・質問待ち）の残りを流した done を読み、ループの 1 回を 1 回だけ締め直す（#2436 の 6a・6d）。"""
    with open_record(env, name) as (rec, before):
        lap = rec["laps"][n - 1] if 1 <= n <= len(rec.get("laps", [])) else None
        if not lap or lap.get("outcome") not in PENDING:
            return refusal_order(f"ループの {n} 回目 は保留したループの 1 回でない（締め直しは 1 回だけ）")
        if done not in lap.setdefault("dones", []):
            lap["dones"].append(done)
        refresh_state(lap, update)
        state = jsonio.read(lap["state_path"], missing={}, broken={}, want=dict) if lap.get("state_path") else {}
        return finish_held(env, rec, before, lap, read_done_items(done), state)


def cmd_pending(env: Env, name: str) -> dict:
    """答えの届いた保留したループの 1 回のまとめの pipeline だけを返す（止まったレーンの残りを一時レーンが流す。異常系 11）。"""
    with open_record(env, name) as (rec, before):
        return pending_pipeline(env, rec, before)


def cmd_hold_question(env: Env, name: str, ref: str, plan: str, step: str, parallel: str | None = None) -> dict:
    """作業の途中の問いを問いの hook がオーガナイザーへ送ったループの 1 回を「質問待ち」で締め、止まったプランとステップを残して
    ループの次の 1 回へ進む（#2436 の 6d）。答えはループの 1 回の境で読み、止まったプランを続きのステップから流す。"""
    with open_record(env, name) as (rec, before):
        lap = current_lap(rec)
        if not lap:
            return refusal_order("進行中のループの 1 回が無い")
        lap["question"] = {"request": ref, "plan": plan, "step": step, "plan_name": parallel or lane_plan_name(env)}
        close_lap(env, rec, lap, QUESTION_PENDING)
        lap["held_at"] = lap["ended_at"]
        return after_close(env, rec, before, f"ループの {lap['n']} 回目 の問い（{ref}）をオーガナイザーへ送り、質問待ちで締めた")


def lane_plan_name(env: Env) -> str | None:
    """自分（NDF_RELAY_NAME）が属する動いている並列の計画の名前（無ければ None）。"""
    try:
        import parallel_lane

        got = parallel_lane.lane_of(env.main_dir())
    except Exception:  # noqa: BLE001 — 計画を読めないときはレーンでないものとして扱う
        return None
    return got[0] if got else None


# ---------------------------------------------------------------- status / list


def cmd_status(env: Env, name: str) -> dict:
    rec = read_loop_record(env, name)
    lap = current_lap(rec)
    lines = [
        f"ループ: {name}（{rec.get('status')}）",
        f"ループの 1 回: {len(rec.get('laps', []))}（進行中 {lap['n'] if lap else 'なし'}）",
        f"消費: {consumed(rec)} / 予算 {rec.get('budget')}",
    ]
    waiting = [w["number"] for w in rec.get("waiting", []) if not w.get("answer")]
    if waiting:
        lines.append("人の判断待ち: " + " ".join(f"#{n}" for n in waiting))
    held = [
        {"lap": x["n"], "outcome": x["outcome"], "answer": ((x.get("release") or {}).get("answer") or {}).get("result")}
        for x in pending_laps(rec)
    ]
    for h in held:
        lines.append(f"ループの {h['lap']} 回目: {h['outcome']}（答え: {h['answer'] or 'まだ'}）")
    line, owner = owner_line(env)
    lines.append(line)
    metrics = {
        "status": rec.get("status"),
        "consumed": consumed(rec),
        "budget": rec.get("budget"),
        "waiting": waiting,
        "held": held,
        "review_owner": owner,
    }
    if rec.get("stop"):
        lines.append(f"止まった理由: {rec['stop']['condition']}。再開: {resume_command(rec)}")
        metrics.update(stop=rec["stop"], stop_line=stop_line(rec, rec["stop"]["condition"]))
    return step_result.result(TOOL, "ok", lines[0], lines[1:], metrics)


def cmd_list(env: Env, name: str) -> dict:
    rec = read_loop_record(env, name)
    rows = lap_rows(rec)
    return step_result.result(
        TOOL, "ok", f"ループ {name} のループの {len(rows)} 回目 件", rows, {"laps": len(rows), "path": str(loop_dir(env, name) / LAPS_MD)}
    )
