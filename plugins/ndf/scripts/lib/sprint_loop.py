"""sprint_loop.py: 開発ループの集約（#2089）。`sprint-state.py loop` の副命令だけが呼ぶ。LLM を呼ばない。

ループの記録は、メインディレクトリの `.ndf/loop/<名>/loop.json`（正本）と `laps.md`（描画）である。コミットしない。
周回はスプリント状態ファイルを ID（パス）で参照し、周回を締めるときに費用・秒・承認ゲートの判定・版を写して固定する。
止まる条件（予算・同じ失敗・止める指示）は周回の境（周回の開始の前と終わりの後）でだけ判定する。周回の途中で止めるのは、
承認ゲート 2 を人へ戻した（`gate2_returned`）・プロジェクト MVV が改訂された（`mvv_changed`）・周回を組めなかった
（`refused`）の 3 つだけで、周回は進行中のまま残す。

外の世界（gh・`upkeep.py rank`・`supervise.py`・`sprint-state.py` の別の副命令）は `Env` の関数を通して呼び、テストで
差し替える。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import clock
import gate_rejections as gr
import gh_sections
import handoff_doc
import jsonio
import locks
import project_mvv as pm
import repo as repo_lib
import sprint_mvv
import step_result
from pace import PaceError, read_loop, read_pace

TOOL = "sprint-state"
SCRIPTS = Path(__file__).resolve().parents[1]
SPRINT_STATE = SCRIPTS / "sprint-state.py"
SUPERVISE = SCRIPTS / "supervise.py"
UPKEEP = SCRIPTS.parent / "skills" / "backlog-refinement" / "scripts" / "upkeep.py"
HANDOFF_TEMPLATE = SCRIPTS / "data" / "handoff-template.md"
LOOP_DIR = Path(".ndf") / "loop"
HANDOFF_DIR = Path(".ndf") / "handoff"
RECORD = "loop.json"
LAPS_MD = "laps.md"
STOP_FILE = "stop"
NAME_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_-]*$")
VERSION = 1

EXIT_REFUSED = step_result.EXIT_VIOLATION  # 1: 断った・呼び出しの順の誤り
EXIT_STOPPED = step_result.EXIT_PRECONDITION  # 3: 止まる条件に当たった（失敗ではない）

IN_PROGRESS, DONE, FAILED, RETURNED = "進行中", "完了", "失敗", "人へ戻した"
SELECTED, DROPPED = "選んだ", "外した"
BOUNDARY_CONDITIONS = ("user_stop", "mvv_changed", "same_failure", "budget_laps", "budget_usd", "budget_hours")
SLOT_FIT = "当てた"  # upkeep_rank_config.SLOT_FIT（rank の出力をそのまま読む）
OVER_SCALE = "尺度を超える"  # upkeep_rank_estimate.OVER_SCALE
GATE_1, GATE_2 = "関門 1", "関門 2"
DESIGN_PHASE = "設計"
FINISHED = ("完了", "関門")
NOT_RUN = "流さなかった"  # supervise_lib.queue.NOT_RUN（前のステージが完了でないときに流さなかった計画）
PAUSE_RANK = step_result.EXIT_PAUSE  # rank の 20（段階の欠けた課題がある）は続ける


class LoopError(Exception):
    """記録が無い・壊れている・呼び出しの順が誤っている（終了コード 1）。"""


# ---------------------------------------------------------------- 外の世界


def _run(cmd: list[str], cwd: str | None = None, stdin: str | None = None) -> tuple[int, str, str]:
    p = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def _rank(root: Path) -> tuple[int, dict]:
    code, out, err = _run([sys.executable, str(UPKEEP), "rank", "--root", str(root)])
    try:
        return code, json.loads(out.strip().splitlines()[-1]) if out.strip() else {"summary": err.strip()[:300]}
    except ValueError:
        return code, {"summary": (out or err).strip()[:300]}


def _milestones(repo: str) -> dict:
    """開いているマイルストーンの {題: 説明}。"""
    code, out, err = _run(
        ["gh", "api", f"repos/{repo or '{owner}/{repo}'}/milestones?state=open&per_page=100", "--jq", "map({title, description})"]
    )
    if code != 0:
        raise OSError(f"マイルストーンを読めない: {err.strip()[:300]}")
    return {m["title"]: m.get("description") or "" for m in json.loads(out or "[]")}


def _pr_state(pr: int) -> str:
    code, out, _ = _run(["gh", "pr", "view", str(pr), "--json", "state", "--jq", ".state"])
    return out.strip() if code == 0 else ""


def _issue_body(n: int) -> str:
    code, out, err = _run(["gh", "issue", "view", str(n), "--json", "body", "--jq", ".body"])
    if code != 0:
        raise OSError(f"課題 #{n} を読めない: {err.strip()[:300]}")
    return out


def _edit_issue(n: int, body: str) -> None:
    code, _, err = _run(["gh", "issue", "edit", str(n), "--body-file", "-"], stdin=body)
    if code != 0:
        raise OSError(f"課題 #{n} の本文を書けない: {err.strip()[:300]}")


def _upkeep_state_dir(repo: str) -> Path:
    base = os.environ.get("NDF_UPKEEP_STATE_DIR") or str(Path(tempfile.gettempdir()) / "ndf" / "backlog-refinement")
    return Path(base) / repo.replace("/", "--")


@dataclass
class Env:
    """外の世界への入口。テストは関数を差し替える。"""

    root: Path
    run: Callable = _run
    rank: Callable = _rank
    milestones: Callable = _milestones
    pr_state: Callable = _pr_state
    issue_body: Callable = _issue_body
    edit_issue: Callable = _edit_issue
    upkeep_dir: Callable = _upkeep_state_dir
    now: Callable = field(default=lambda: clock.now_iso("utc"))
    main: Path | None = None

    def main_dir(self) -> Path:
        if self.main is None:
            self.main = repo_lib.main_dir(self.root) or self.root
        return self.main


# ---------------------------------------------------------------- 記録


def loop_dir(env: Env, name: str) -> Path:
    if not NAME_RE.match(name or ""):
        raise LoopError(f"ループの名前は英数字・- ・_ で書く: {name!r}")
    return env.main_dir() / LOOP_DIR / name


def record_path(env: Env, name: str) -> Path:
    return loop_dir(env, name) / RECORD


def read_record(env: Env, name: str) -> dict:
    p = record_path(env, name)
    try:
        rec = jsonio.read(p, want=dict)
    except jsonio.JsonReadError as e:
        raise LoopError(f"ループの記録が無いか読めない: {p}（{e.detail or e}）")
    check_laps(rec)
    return rec


def check_laps(rec: dict) -> None:
    """周回の番号は 1 から 1 ずつ増え、進行中は高々 1 つ（I1・I2）。破れていれば LoopError。"""
    laps = rec.get("laps", [])
    if [lap.get("n") for lap in laps] != list(range(1, len(laps) + 1)):
        raise LoopError("ループの記録が壊れている（周回の番号が 1 から続いていない）")
    if sum(1 for lap in laps if lap.get("outcome") == IN_PROGRESS) > 1:
        raise LoopError("ループの記録が壊れている（進行中の周回が 2 つ以上ある）")


def write_record(env: Env, rec: dict, before: dict | None = None) -> None:
    """記録を書く。終わった周回の行を書き換える書き込みは拒む（I1）。`laps.md` も描き直す。"""
    check_laps(rec)
    if before is not None:
        old = {lap["n"]: lap for lap in before.get("laps", []) if lap.get("outcome") != IN_PROGRESS}
        new = {lap["n"]: lap for lap in rec.get("laps", [])}
        changed = [n for n, lap in old.items() if new.get(n) != lap]
        if changed:
            raise LoopError(f"終わった周回の行は書き換えない（周回 {changed}）")
    d = loop_dir(env, rec["name"])
    d.mkdir(parents=True, exist_ok=True)
    jsonio.write_atomic(d / RECORD, rec, indent=1)
    (d / LAPS_MD).write_text(render_laps(rec), encoding="utf-8")


def locked(env: Env, name: str):
    """同じループの記録を 2 つのセッションが同時に書かない（`sprint-state.py gate` と同じ排他）。"""
    d = loop_dir(env, name)
    d.mkdir(parents=True, exist_ok=True)
    return locks.exclusive(d / RECORD, timeout=60)


def current_lap(rec: dict) -> dict | None:
    return next((lap for lap in rec.get("laps", []) if lap.get("outcome") == IN_PROGRESS), None)


# ---------------------------------------------------------------- 消費と止まる条件


def _parse(t) -> _dt.datetime | None:
    return clock.parse(t, naive="utc") if t else None


def _overlap(a0, a1, b0, b1) -> float:
    lo, hi = max(a0, b0), min(a1, b1)
    return max(0.0, (hi - lo).total_seconds())


def active_seconds(rec: dict) -> float:
    """動いていた時間（I8）: 終わった周回の区間の和から、周回の途中で止まっていた時間（stops の at〜resumed_at と
    周回の区間の重なり）を引く。周回の境で止まった時間は周回の区間の外にあるため引かない。"""
    total = 0.0
    stops = [(_parse(s.get("at")), _parse(s.get("resumed_at"))) for s in rec.get("stops", [])]
    for lap in rec.get("laps", []):
        s, e = _parse(lap.get("started_at")), _parse(lap.get("ended_at"))
        if not s or not e:
            continue
        span = max(0.0, (e - s).total_seconds())
        for a, b in stops:
            if a and b:
                span -= _overlap(s, e, a, b)
        total += max(0.0, span)
    return total


def consumed(rec: dict) -> dict:
    """予算の消費（周回の数・費用・動いていた時間）。書き込まない。"""
    closed = [lap for lap in rec.get("laps", []) if lap.get("outcome") != IN_PROGRESS]
    usd = sum(float(lap.get("cost") or 0) for lap in closed)
    return {"laps": len(closed), "usd": round(usd, 6), "hours": round(active_seconds(rec) / 3600, 6)}


def failure_streak(rec: dict) -> tuple[dict | None, int]:
    """直近の失敗の周回が同じ鍵で何回続くか（決定 8）。完了の周回で数え直し、same_failure で止まった後の最後の再開より
    前に始まった周回は数えない。"""
    cut = None
    for s in rec.get("stops", []):
        if s.get("condition") == "same_failure" and s.get("resumed_at"):
            cut = _parse(s["resumed_at"])
    key, count = None, 0
    for lap in reversed([lap for lap in rec.get("laps", []) if lap.get("outcome") != IN_PROGRESS]):
        if cut and (_parse(lap.get("started_at")) or cut) < cut:
            break
        if lap.get("outcome") == DONE:
            break
        if lap.get("outcome") != FAILED:
            continue
        k = lap.get("failure")
        if key is None:
            key, count = k, 1
        elif k == key:
            count += 1
        else:
            break
    return key, count


def boundary_stop(env: Env, rec: dict) -> dict | None:
    """周回の境の判定。最初に当たったものを返す（判定に使った値は values）。当たらなければ None。"""
    stop_file = loop_dir(env, rec["name"]) / STOP_FILE
    if stop_file.exists():
        return {"condition": "user_stop", "values": {"at": stop_file.read_text(encoding="utf-8").strip()}}
    project = pm.load_mvv(env.main_dir())
    started = (rec.get("project_mvv") or {}).get("sha256")
    if not project.approved or project.sha256 != started:
        return {"condition": "mvv_changed", "values": {"started": started, "now": project.sha256 if project.approved else project.status}}
    key, count = failure_streak(rec)
    if key is not None and count >= rec.get("same_failure", 2):
        return {"condition": "same_failure", "values": {"key": key, "count": count}}
    used, budget = consumed(rec), rec.get("budget", {})
    for unit in ("laps", "usd", "hours"):
        if unit in budget and used[unit] >= budget[unit]:
            return {"condition": f"budget_{unit}", "values": {"consumed": used[unit], "budget": budget[unit]}}
    return None


def resume_command(rec: dict) -> str:
    """再開のコマンド（ndf-next の中身。名前を必ず含め、終わりの条件の節を末尾に足す。決定 12 の B）。"""
    head = "/goal " if rec.get("goal", True) else ""
    return f"{head}/ndf:development-workflow --loop {rec['name']} -- 終わり: [ndf-loop] stopped {rec['name']} の行を出したとき"


def stop_line(rec: dict, condition: str) -> str:
    return f"[ndf-loop] stopped {rec['name']} {condition}"


def write_stop(env: Env, rec: dict, stop: dict) -> dict:
    lap = current_lap(rec) or (rec["laps"][-1] if rec.get("laps") else None)
    rec["stop"] = {**stop, "lap": lap["n"] if lap else 0, "at": env.now()}
    rec["status"] = "stopped"
    return rec["stop"]


def stopped_result(env: Env, rec: dict, summary: str, extra_metrics: dict | None = None) -> dict:
    """止まったときの結果（終了コード 3）。items に条件・値・再開のコマンド、metrics.stop_line に決まった語の 1 行。"""
    stop = rec["stop"]
    resume = resume_command(rec)
    update_handoff(env, rec, f"止まった（{stop['condition']}）。理由が解けたら再開のコマンドで続ける", resume)
    items = [{"condition": stop["condition"], "values": stop["values"], "resume": resume}]
    metrics = {
        "status": "stopped",
        "stop": stop,
        "stop_line": stop_line(rec, stop["condition"]),
        "consumed": consumed(rec),
        **(extra_metrics or {}),
    }
    return {**step_result.result(TOOL, "stopped", summary, items, metrics), "exit": EXIT_STOPPED}


# ---------------------------------------------------------------- 引継ぎ文書と laps.md


def handoff_path(env: Env, name: str) -> Path:
    return env.main_dir() / HANDOFF_DIR / f"loop-{name}.md"


def update_handoff(env: Env, rec: dict, now_text: str, command: str) -> Path:
    """引継ぎ文書 loop-<名> の「現在地」と「次に実行するコマンド」を置き換える（無ければ雛形から作る）。"""
    p = handoff_path(env, rec["name"])
    text = (
        p.read_text(encoding="utf-8")
        if p.is_file()
        else HANDOFF_TEMPLATE.read_text(encoding="utf-8").replace("{title}", f"開発ループ {rec['name']}")
    )
    lap = current_lap(rec)
    where = f"\n- ループ: {rec['name']}（{rec.get('status')}）。周回 {lap['n'] if lap else len(rec.get('laps', []))}。{now_text}\n"
    where += f"- 記録: `{loop_dir(env, rec['name']) / LAPS_MD}`\n\n"
    text = handoff_doc.replace_body(text, "現在地", where) or text
    fence = "`" * max(3, max((len(r) for r in re.findall(r"`+", command)), default=0) + 1)
    text = handoff_doc.replace_body(text, handoff_doc.COMMAND_SECTION, f"\n{fence}ndf-next\n{command}\n{fence}\n") or text
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _cell(v) -> str:
    return str(v).replace("|", "\\|") if v not in (None, "", []) else "—"


def lap_rows(rec: dict) -> list[dict]:
    """周回の一覧（何周目・選んだ課題・版・費用・止まった理由）。"""
    stops = {s.get("lap"): s.get("condition") for s in [*rec.get("stops", []), *([rec["stop"]] if rec.get("stop") else [])]}
    rows = []
    for lap in rec.get("laps", []):
        chosen = [r["number"] for r in lap.get("selection", []) if r.get("decision") == SELECTED]
        rows.append(
            {
                "n": lap["n"],
                "issues": chosen,
                "versions": lap.get("versions") or {},
                "cost": lap.get("cost"),
                "seconds": lap.get("seconds"),
                "outcome": lap.get("outcome"),
                "stop": stops.get(lap["n"], ""),
                "deferred_issues": lap.get("deferred_issues", []),
            }
        )
    return rows


def render_laps(rec: dict) -> str:
    lines = [f"# 開発ループ {rec['name']}", "", f"- 状態: {rec.get('status')}。予算: {rec.get('budget')}。消費: {consumed(rec)}", ""]
    lines += [
        "| 周回 | 課題 | 版 | 費用 | 秒 | 結果 | 止まった理由 | 後へ回した Issue |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in lap_rows(rec):
        v = r["versions"]
        ver = " / ".join(x for x in (v.get("dev"), v.get("prod")) if x)
        issues = " ".join(f"#{n}" for n in r["issues"])
        deferred = " ".join(f"#{n}" for n in r["deferred_issues"])
        lines.append(
            f"| {r['n']} | {_cell(issues)} | {_cell(ver)} | {_cell(r['cost'])} | {_cell(r['seconds'])} | {_cell(r['outcome'])} | {_cell(r['stop'])} | {_cell(deferred)} |"
        )
    for lap in rec.get("laps", []):
        lines += [
            "",
            f"## 周回 {lap['n']}（{lap.get('sprint', '')}）",
            "",
            "| 課題 | マイルストーン | 順位 | 選定 | 理由 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for s in lap.get("selection", []):
            lines.append(
                f"| #{s['number']} | {_cell(s.get('milestone'))} | {_cell(s.get('rank'))} | {s['decision']} | {_cell(s.get('reason'))} |"
            )
        for g in lap.get("gates", []):
            lines.append(
                f"\n- {g.get('name')}: {g.get('by', 'user')} {_cell(g.get('verdict'))}（根拠 {_cell(', '.join(g.get('basis') or []))}）"
            )
        if lap.get("refine"):
            lines.append(
                f"- 棚卸し: 持ち越し {_cell(lap['refine'].get('deferred'))}・順位の変化 {len(lap['refine'].get('rank_changes') or [])} 件"
            )
        if lap.get("retro"):
            lines.append(f"- 振り返り: {lap['retro']}")
    waiting = [w for w in rec.get("waiting", [])]
    if waiting:
        lines += ["", "## 人の判断待ち", "", "| 課題 | 承認ゲート | 理由 | 答え |", "| --- | --- | --- | --- |"]
        lines += [f"| #{w['number']} | {w.get('gate')} | {_cell(w.get('reason'))} | {_cell(w.get('answer'))} |" for w in waiting]
    if rec.get("stop"):
        lines += [
            "",
            f"## 止まった理由: {rec['stop']['condition']}",
            "",
            f"- 値: {json.dumps(rec['stop']['values'], ensure_ascii=False)}",
            f"- 再開: `{resume_command(rec)}`",
        ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- start


def refusal(summary: str, reasons: list[str]) -> dict:
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
        p = record_path(env, name)
        if why:
            return refusal("開発ループを始めない（使ってよい条件に当たらない）", why)
        if not p.is_file():
            return start_new(env, name, decl, project, milestones, budget, goal)
        rec = read_record(env, name)
        before = json.loads(json.dumps(rec))
        apply_budget(env, rec, budget)
        if rec.get("status") == "stopped":
            return resume_stopped(env, rec, before)
        write_record(env, rec, before)
        lap = current_lap(rec)
        status = "in_lap" if lap else "resumed"
        nxt = f"引継ぎ文書 {handoff_path(env, name)} の「次にやること」から続ける" if lap else select_cmd(name)
        return ok(f"開発ループ {name} を続ける（{status}）", rec, status=status, next_=nxt)


def ok(summary: str, rec: dict, items=None, status: str = "ok", next_: str | None = None, **metrics) -> dict:
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
    return ok(f"開発ループ {name} を始めた", rec, status="new", next_=select_cmd(name))


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
    return ok(f"開発ループ {name} を再開した（{cond} が解けた）", rec, status="resumed", next_=nxt, resolved=cond)


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
    close = build_close(env, rec, lap, lap.get("issues_kept") or selected(lap), record_pr=pr)
    done = Path(lap["dir"]) / f"done-{len(lap.get('dones', [])) + 1}.json"
    prod = lap.get("prod_plan") or "<本番のプラン>"
    run = f"python3 {shlex.quote(str(SUPERVISE))} run {shlex.quote(prod)} --from bump"
    queue = (
        f"python3 {shlex.quote(str(SUPERVISE))} queue " + " ".join(map(shlex.quote, close)) + f" --max 3 --done {shlex.quote(str(done))}"
    )
    lap.setdefault("dones", []).append(str(done))
    return f"{run} && {queue} && {wait_lap(rec, done)}"


def wait_lap(rec: dict, done: Path) -> str:
    return f"python3 {shlex.quote(str(SUPERVISE))} wait {shlex.quote(str(done))} && python3 {shlex.quote(str(SPRINT_STATE))} loop lap {rec['name']} --done {shlex.quote(str(done))}"


# ---------------------------------------------------------------- select


def selected(lap: dict) -> list[int]:
    return [r["number"] for r in lap.get("selection", []) if r.get("decision") == SELECTED]


def mvv_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def candidates_of(ms: dict, max_issues: int) -> list[dict]:
    """マイルストーンの候補（順位の順）: 境界までの行（境界が無ければ先頭から max_issues 件）＋大きい課題の枠の 1 切れ（当てたとき）
    ＋品質の枠の課題（宣言があるとき）。"""
    rows = sorted(ms.get("rows", []), key=lambda r: r["rank"])
    b = ms.get("boundary")
    if b:
        idx = next((i for i, r in enumerate(rows) if r["number"] == b["number"]), len(rows) - 1)
        nums = {r["number"] for r in rows[: idx + 1]}
    else:
        nums = {r["number"] for r in rows[:max_issues]}
    slot = ms.get("large_slot") or {}
    if slot.get("status") == SLOT_FIT and slot.get("slice"):
        nums.add(slot["slice"])
    nums |= set((ms.get("quality_slot") or {}).get("issues") or [])
    return [r for r in rows if r["number"] in nums]


def exclusion_reason(env: Env, rec: dict, row: dict, lap_n: int, lap_created: set) -> str | None:
    n = row["number"]
    if row.get("waiting_decision"):
        return "人の判断待ち（棚卸し）"
    for w in rec.get("waiting", []):
        if w["number"] != n:
            continue
        if not w.get("answer"):
            return "人の判断待ち"
        if w["answer"] == "rejected":
            return "差し戻し"
        if w["answer"] == "approved" and not w.get("merged_at"):
            if w.get("pr") and env.pr_state(int(w["pr"])) == "MERGED":
                w["merged_at"] = env.now()
            else:
                return "設計 PR がマージ待ち"
    for e in rec.get("excluded", []):
        if e["number"] == n and e.get("lap") == lap_n:
            return e["reason"]
    if n in lap_created:
        return "周回の中で起票された（次の周回の棚卸しの後に選ぶ）"
    if row.get("size") == OVER_SCALE:
        return "容量（尺度を超える）"
    return None


def choose(env: Env, rec: dict, rank: dict, lap_n: int, descriptions: dict, lap_created: set = frozenset()) -> dict:
    """選定（決定 2）。入力は rank の出力・記録・マイルストーンの説明だけ。戻り値は
    {"milestone", "selection", "candidates"}。選べたマイルストーンが無ければ milestone は None。"""
    m = rank.get("metrics") or {}
    by_title = {ms["title"]: ms for ms in m.get("milestones", [])}
    order = [t for t in m.get("order", []) if t in by_title and (not rec.get("milestones") or t in rec["milestones"])]
    approvals = {(a["milestone"], a["sha256"]) for a in rec.get("sprint_mvv_approvals", [])}
    rows_out, total = [], 0
    for title in order:
        ms = by_title[title]
        cands = candidates_of(ms, rec["max_issues"])
        total += len(cands)
        text = sprint_mvv.mvv_sections(descriptions.get(title) or "")
        unapproved = text is not None and (title, mvv_sha(text)) not in approvals
        picked, here = [], []
        for r in cands:
            why = "スプリント MVV が未承認" if unapproved else exclusion_reason(env, rec, r, lap_n, lap_created)
            if why is None and len(picked) >= rec["max_issues"]:
                why = "容量"
            if why is None:
                picked.append(r["number"])
                reason = "品質の枠" if r.get("quality_slot") else "順位"
                here.append({"number": r["number"], "milestone": title, "rank": r["rank"], "decision": SELECTED, "reason": reason})
            else:
                here.append({"number": r["number"], "milestone": title, "rank": r["rank"], "decision": DROPPED, "reason": why})
        rows_out += here
        if picked:
            cand_nums = {r["number"] for r in cands}
            rest = [r for r in sorted(ms.get("rows", []), key=lambda r: r["rank"]) if r["number"] not in cand_nums]
            rows_out += [
                {"number": r["number"], "milestone": title, "rank": r["rank"], "decision": DROPPED, "reason": "順位（切り出しの境界の外）"}
                for r in rest
            ]
            return {"milestone": title, "selection": rows_out, "candidates": total, "sprint_mvv": text}
    return {"milestone": None, "selection": rows_out, "candidates": total, "sprint_mvv": None}


def cmd_select(env: Env, name: str) -> dict:
    with locked(env, name):
        rec = read_record(env, name)
        before = json.loads(json.dumps(rec))
        if rec.get("status") == "stopped":
            return stopped_result(env, rec, f"ループ {name} は止まっている。再開のコマンド（loop start）で理由を判定し直す")
        if current_lap(rec):
            return refusal_order("進行中の周回がある（I2）。周回を締めてから選ぶ")
        stop = boundary_stop(env, rec)
        if stop:
            write_stop(env, rec, stop)
            write_record(env, rec, before)
            return stopped_result(env, rec, f"止まる条件に当たった（{stop['condition']}）")
        return run_selection(env, rec, before, len(rec["laps"]) + 1)


def refusal_order(summary: str) -> dict:
    return {**step_result.result(TOOL, "stopped", summary, [], {"status": "refused"}), "exit": EXIT_REFUSED}


def run_selection(env: Env, rec: dict, before: dict, n: int, lap: dict | None = None) -> dict:
    code, rank = env.rank(env.main_dir())
    if code not in (0, PAUSE_RANK):
        return refusal_order(f"upkeep.py rank が {code} を返した。選定を推さない: {rank.get('summary', '')}")
    try:
        descriptions = env.milestones(rec.get("repo") or "")
    except OSError as e:
        return refusal_order(str(e))
    got = choose(env, rec, rank, n, descriptions, set((lap or {}).get("created") or []))
    if got["milestone"] is None:
        if lap is not None:
            rec["laps"].remove(lap)  # スプリントを組む前の周回は取り下げ、除外の一覧（excluded の lap）を次の選定に残す
        cond = "no_issue" if got["candidates"] == 0 else "outside_mvv"
        reasons: dict = {}
        for r in got["selection"]:
            reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
        values = (
            {"milestones": rec.get("milestones") or (rank.get("metrics") or {}).get("order", [])}
            if cond == "no_issue"
            else {"reasons": reasons}
        )
        write_stop(env, rec, {"condition": cond, "values": values})
        write_record(env, rec, before)
        return stopped_result(
            env,
            rec,
            "着手できる課題が無い" if cond == "no_issue" else "候補がすべて MVV の外・人の判断待ちとして外れた",
            {"selection": got["selection"]},
        )
    d = loop_dir(env, rec["name"]) / f"lap-{n}"
    new = lap or {"n": n, "outcome": IN_PROGRESS, "started_at": env.now(), "created": [], "gates": [], "deferred_issues": []}
    new.update(
        sprint=f"{rec['name']}-{n}",
        dir=str(d),
        state_path=None,
        out=str(d / "sprint"),
        milestone=got["milestone"],
        rank_digest=(rank.get("metrics") or {}).get("digest", ""),
        selection=got["selection"],
    )
    if lap is None:
        rec["laps"].append(new)
    if got["sprint_mvv"]:
        d.mkdir(parents=True, exist_ok=True)
        (d / "mvv.md").write_text(got["sprint_mvv"], encoding="utf-8")
    write_record(env, rec, before)
    chosen = selected(new)
    nxt = f"モードと版を決め、対象外は loop exclude で外し、python3 {shlex.quote(str(SPRINT_STATE))} loop sprint {rec['name']} --mode <モード> --dev <開発版> を打つ"
    update_handoff(env, rec, f"周回 {n} の課題を選んだ: {' '.join(f'#{x}' for x in chosen)}", resume_command(rec))
    return ok(
        f"周回 {n} の課題を選んだ（{got['milestone']}）: " + " ".join(f"#{x}" for x in chosen),
        rec,
        got["selection"],
        status="selected",
        next_=nxt,
        selected=chosen,
        excluded=[r for r in got["selection"] if r["decision"] == DROPPED],
        milestone=got["milestone"],
        rank_digest=new["rank_digest"],
    )


def cmd_exclude(env: Env, name: str, number: int, reason: str) -> dict:
    with locked(env, name):
        rec = read_record(env, name)
        before = json.loads(json.dumps(rec))
        lap = current_lap(rec)
        if not lap or lap.get("state_path"):
            return refusal_order("スプリントを組む前の進行中の周回が無い（loop exclude は選定の後・loop sprint の前に打つ）")
        rec["excluded"].append({"number": number, "reason": reason, "lap": lap["n"]})
        return run_selection(env, rec, before, lap["n"], lap)


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
    write_stop(env, rec, {"condition": "refused", "values": {"step": step, "exit": code, "summary": summary[:300]}})
    write_record(env, rec, before)
    return stopped_result(env, rec, f"周回を組めなかった（{step}）: {summary[:200]}")


def cmd_sprint(env: Env, name: str, mode: str, dev: str, prod: str | None, extra: list[str]) -> dict:
    with locked(env, name):
        rec = read_record(env, name)
        before = json.loads(json.dumps(rec))
        lap = current_lap(rec)
        if not lap:
            return refusal_order("進行中の周回が無い（loop select で選んでから組む）")
        if rec.get("status") == "stopped" and (rec.get("stop") or {}).get("condition") != "refused":
            return stopped_result(env, rec, "ループは止まっている")
        issues = selected(lap)
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
        return ok(
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


def copy_mvv_gate(rec: dict, lap: dict, state: Path, mvv: Path) -> None:
    """承認済みのスプリント MVV を周回の状態へ承認ゲート MVV の記録として写す（決定 11）。"""
    sha = mvv_sha(mvv.read_text(encoding="utf-8"))
    hit = next((a for a in rec.get("sprint_mvv_approvals", []) if a["milestone"] == lap["milestone"] and a["sha256"] == sha), None)
    if not hit:
        return
    m = jsonio.read(state, want=dict)
    file_sha = hashlib.sha256(Path((m.get("mvv") or {}).get("path") or mvv).read_bytes()).hexdigest()
    entry = {"name": "MVV", "what": f"開発ループ {rec['name']} の承認（{hit['at']}）", "at": hit["at"], "sha256": file_sha}
    m["gates"] = [g for g in m.get("gates", []) if g.get("name") != "MVV"] + [entry]
    jsonio.write_atomic(state, m, indent=1)


# ---------------------------------------------------------------- gate


def done_items(path: str) -> list[dict]:
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
        rec = read_record(env, name)
        before = json.loads(json.dumps(rec))
        lap = current_lap(rec)
        if not lap or not lap.get("state_path"):
            return refusal_order("スプリントを組んだ進行中の周回が無い")
        return handle_gate(env, rec, before, lap, done_items(done))


def handle_gate(env: Env, rec: dict, before: dict, lap: dict, items: list[dict]) -> dict:
    gated = [i for i in items if i.get("result") == "関門"]
    if not gated:
        return refusal_order("done に承認ゲートで止まった計画が無い")
    project = pm.load_mvv(env.main_dir())
    started = (rec.get("project_mvv") or {}).get("sha256")
    if not project.approved or project.sha256 != started:
        write_stop(
            env,
            rec,
            {"condition": "mvv_changed", "values": {"started": started, "now": project.sha256 if project.approved else project.status}},
        )
        write_record(env, rec, before)
        return stopped_result(env, rec, "プロジェクト MVV が改訂された（承認ゲートの機械のチェック）")
    gate1 = [i for i in gated if plan_info(i["plan"]).get("フェーズ") == DESIGN_PHASE]
    if len(gate1) != len(gated):
        row = last_verdict(lap["state_path"], "release")
        lap.setdefault("gates", []).append(gate_copy(GATE_2, row))
        values = {"pr": row.get("pr"), "verdict": row.get("verdict", "unknown"), "reasons": row.get("reasons") or []}
        write_stop(env, rec, {"condition": "gate2_returned", "values": values})
        write_record(env, rec, before)
        return stopped_result(env, rec, "承認ゲート 2 を人へ戻した。本番を出さずに止まる（利用者の承認を求める）", {"gate": 2})
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
    kept = [n for n in (lap.get("issues_kept") or selected(lap)) if n not in moved]
    lap["issues_kept"] = kept
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
    return ok(
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


# ---------------------------------------------------------------- answer / approve-mvv / stop


def cmd_answer(env: Env, name: str, number: int, approved: bool, pr: int | None) -> dict:
    with locked(env, name):
        rec = read_record(env, name)
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
        return ok(f"#{number} の答え（{w['answer']}）を書いた。{after}", rec, [w, *({"note": n} for n in notes)], status="answered")


def cmd_approve_mvv(env: Env, name: str, milestone: str) -> dict:
    with locked(env, name):
        rec = read_record(env, name)
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
        row = {"milestone": milestone, "sha256": mvv_sha(text), "at": env.now(), "by": "user"}
        rec["sprint_mvv_approvals"].append(row)
        write_record(env, rec, before)
        return ok(f"マイルストーン {milestone} のスプリント MVV を承認した（{row['sha256'][:12]}）", rec, [row], status="approved")


def cmd_stop(env: Env, name: str) -> dict:
    read_record(env, name)
    p = loop_dir(env, name) / STOP_FILE
    p.write_text(env.now() + "\n", encoding="utf-8")
    return step_result.result(TOOL, "ok", f"止める指示を置いた（次の周回の境で効く）: {p}", [{"path": str(p)}], {"path": str(p)})


# ---------------------------------------------------------------- lap


def failure_key(state: dict) -> dict | None:
    """スプリント状態の行（完了でもまだでもない最初の行）の課題・種類・止まったところ（決定 8）。理由の文は含めない。"""
    for p in state.get("plans", []):
        row = p.get("row") or {}
        if row.get("result") not in (None, "まだ", NOT_RUN, *FINISHED):  # 関門は loop gate が扱い、流さなかった計画は失敗の元でない
            return {"issues": sorted(p.get("issues") or []), "kind": p.get("kind", ""), "step": row.get("result")}
    return None


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
    close_rep = next((p for p in state.get("plans", []) if p.get("kind") == "まとめ"), None)
    if close_rep:
        lap["retro"] = (close_rep.get("row") or {}).get("report", "")
    sd = env.upkeep_dir(rec.get("repo") or "")
    cands = jsonio.read(sd / "candidates.json", missing={}, broken={}, want=dict)
    rank = jsonio.read(sd / "rank.json", missing={}, broken={}, want=dict)
    lap["refine"] = {
        "report": str(sd / "candidates.json") if cands else "",
        "rank_changes": (rank.get("metrics") or {}).get("changes") or [],
        "deferred": (cands.get("metrics") or {}).get("deferred") or [],
    }
    lap["deferred_issues"] = sorted(set(lap.get("deferred_issues") or []) | {int(n) for n in deferred_issues})
    lap["failure"] = failure_key(state) if outcome == FAILED else None
    if outcome == FAILED and lap["failure"] is None:
        lap["failure"] = {"issues": selected(lap), "kind": "周回", "step": "conductor"}
    lap["relay_dir"] = os.environ.get("NDF_RELAY_DIR")
    lap["outcome"], lap["ended_at"] = outcome, env.now()


def after_close(env: Env, rec: dict, before: dict, summary: str) -> dict:
    """周回を締めた後の境の判定。止まれば 3、続けば ndf_next を返す。"""
    stop = boundary_stop(env, rec)
    if stop:
        write_stop(env, rec, stop)
        write_record(env, rec, before)
        return stopped_result(env, rec, f"{summary}。止まる条件に当たった（{stop['condition']}）")
    write_record(env, rec, before)
    nxt = resume_command(rec)
    update_handoff(env, rec, f"周回 {rec['laps'][-1]['n']} を締めた。次の周回へ", nxt)
    return ok(f"{summary}。次の周回へ進む", rec, [rec["laps"][-1]], status="continue", next_=nxt, ndf_next=f"```ndf-next\n{nxt}\n```")


def cmd_lap(env: Env, name: str, failed: bool, done: str | None, deferred_issues: list[int], update: Callable | None = None) -> dict:
    with locked(env, name):
        rec = read_record(env, name)
        before = json.loads(json.dumps(rec))
        lap = current_lap(rec)
        if not lap:
            return refusal_order("進行中の周回が無い")
        items = done_items(done) if done else []
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
    rec = read_record(env, name)
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
    rec = read_record(env, name)
    rows = lap_rows(rec)
    return step_result.result(
        TOOL, "ok", f"ループ {name} の周回 {len(rows)} 件", rows, {"laps": len(rows), "path": str(loop_dir(env, name) / LAPS_MD)}
    )
