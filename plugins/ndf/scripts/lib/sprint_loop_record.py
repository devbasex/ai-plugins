"""sprint_loop_record.py: 開発ループの記録・消費・止まる条件・引継ぎ文書（#2089）。`sprint-state.py loop` の副命令だけが呼ぶ。LLM を呼ばない。

ループの記録は、メインディレクトリの `.ndf/loop/<名>/loop.json`（正本）と `laps.md`（描画）である。コミットしない。
周回はスプリント状態ファイルを ID（パス）で参照し、周回を締めるときに費用・秒・承認ゲートの判定・版を写して固定する。
止まる条件（予算・同じ失敗・止める指示）は周回の境（周回の開始の前と終わりの後）でだけ判定する。周回の途中で止めるのは、
承認ゲート 2 を人へ戻した（`gate2_returned`）・プロジェクト MVV が改訂された（`mvv_changed`）・周回を組めなかった
（`refused`）の 3 つだけで、周回は進行中のまま残す。

外の世界（gh・`upkeep.py rank`・`supervise.py`・`sprint-state.py` の別の副命令）は `Env` の関数を通して呼び、テストで
差し替える。

各副命令は `sprint_loop.py`、周回の締めと承認ゲートは `sprint_loop_gate.py`、選定は `sprint_loop_select.py` が持つ。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import clock
import handoff_doc
import jsonio
import locks
import project_mvv as pm
import repo as repo_lib
import step_result


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


def _run_loop_cmd(cmd: list[str], cwd: str | None = None, stdin: str | None = None) -> tuple[int, str, str]:
    p = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def _upkeep_rank(root: Path) -> tuple[int, dict]:
    code, out, err = _run_loop_cmd([sys.executable, str(UPKEEP), "rank", "--root", str(root)])
    try:
        return code, json.loads(out.strip().splitlines()[-1]) if out.strip() else {"summary": err.strip()[:300]}
    except ValueError:
        return code, {"summary": (out or err).strip()[:300]}


def _milestones(repo: str) -> dict:
    """開いているマイルストーンの {題: 説明}。"""
    code, out, err = _run_loop_cmd(
        ["gh", "api", f"repos/{repo or '{owner}/{repo}'}/milestones?state=open&per_page=100", "--jq", "map({title, description})"]
    )
    if code != 0:
        raise OSError(f"マイルストーンを読めない: {err.strip()[:300]}")
    return {m["title"]: m.get("description") or "" for m in json.loads(out or "[]")}


def _pr_state(pr: int) -> str:
    code, out, _ = _run_loop_cmd(["gh", "pr", "view", str(pr), "--json", "state", "--jq", ".state"])
    return out.strip() if code == 0 else ""


def _issue_body(n: int) -> str:
    code, out, err = _run_loop_cmd(["gh", "issue", "view", str(n), "--json", "body", "--jq", ".body"])
    if code != 0:
        raise OSError(f"課題 #{n} を読めない: {err.strip()[:300]}")
    return out


def _edit_issue(n: int, body: str) -> None:
    code, _, err = _run_loop_cmd(["gh", "issue", "edit", str(n), "--body-file", "-"], stdin=body)
    if code != 0:
        raise OSError(f"課題 #{n} の本文を書けない: {err.strip()[:300]}")


def _upkeep_state_dir(repo: str) -> Path:
    base = os.environ.get("NDF_UPKEEP_STATE_DIR") or str(Path(tempfile.gettempdir()) / "ndf" / "backlog-refinement")
    return Path(base) / repo.replace("/", "--")


@dataclass
class Env:
    """外の世界への入口。テストは関数を差し替える。"""

    root: Path
    run: Callable = _run_loop_cmd
    rank: Callable = _upkeep_rank
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


def loop_record_path(env: Env, name: str) -> Path:
    return loop_dir(env, name) / RECORD


def read_loop_record(env: Env, name: str) -> dict:
    p = loop_record_path(env, name)
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


def _loop_time(t) -> _dt.datetime | None:
    return clock.parse(t, naive="utc") if t else None


def _overlap(a0, a1, b0, b1) -> float:
    lo, hi = max(a0, b0), min(a1, b1)
    return max(0.0, (hi - lo).total_seconds())


def active_seconds(rec: dict) -> float:
    """動いていた時間（I8）: 終わった周回の区間の和から、周回の途中で止まっていた時間（stops の at〜resumed_at と
    周回の区間の重なり）を引く。周回の境で止まった時間は周回の区間の外にあるため引かない。"""
    total = 0.0
    stops = [(_loop_time(s.get("at")), _loop_time(s.get("resumed_at"))) for s in rec.get("stops", [])]
    for lap in rec.get("laps", []):
        s, e = _loop_time(lap.get("started_at")), _loop_time(lap.get("ended_at"))
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
            cut = _loop_time(s["resumed_at"])
    key, count = None, 0
    for lap in reversed([lap for lap in rec.get("laps", []) if lap.get("outcome") != IN_PROGRESS]):
        if cut and (_loop_time(lap.get("started_at")) or cut) < cut:
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


def mvv_change_stop(env: Env, rec: dict) -> dict | None:
    """プロジェクト MVV が周回の開始時から改訂されたか（未承認を含む）。改訂されていれば止まる理由を返す。"""
    project = pm.load_mvv(env.main_dir())
    started = (rec.get("project_mvv") or {}).get("sha256")
    if not project.approved or project.sha256 != started:
        return {"condition": "mvv_changed", "values": {"started": started, "now": project.sha256 if project.approved else project.status}}
    return None


def boundary_stop(env: Env, rec: dict) -> dict | None:
    """周回の境の判定。最初に当たったものを返す（判定に使った値は values）。当たらなければ None。"""
    stop_file = loop_dir(env, rec["name"]) / STOP_FILE
    if stop_file.exists():
        return {"condition": "user_stop", "values": {"at": stop_file.read_text(encoding="utf-8").strip()}}
    if stop := mvv_change_stop(env, rec):
        return stop
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


def _md_cell(v) -> str:
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
            f"| {r['n']} | {_md_cell(issues)} | {_md_cell(ver)} | {_md_cell(r['cost'])} | {_md_cell(r['seconds'])} | {_md_cell(r['outcome'])} | {_md_cell(r['stop'])} | {_md_cell(deferred)} |"
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
                f"| #{s['number']} | {_md_cell(s.get('milestone'))} | {_md_cell(s.get('rank'))} | {s['decision']} | {_md_cell(s.get('reason'))} |"
            )
        for g in lap.get("gates", []):
            lines.append(
                f"\n- {g.get('name')}: {g.get('by', 'user')} {_md_cell(g.get('verdict'))}（根拠 {_md_cell(', '.join(g.get('basis') or []))}）"
            )
        if lap.get("refine"):
            lines.append(
                f"- 棚卸し: 持ち越し {_md_cell(lap['refine'].get('deferred'))}・順位の変化 {len(lap['refine'].get('rank_changes') or [])} 件"
            )
        if lap.get("retro"):
            lines.append(f"- 振り返り: {lap['retro']}")
    waiting = [w for w in rec.get("waiting", [])]
    if waiting:
        lines += ["", "## 人の判断待ち", "", "| 課題 | 承認ゲート | 理由 | 答え |", "| --- | --- | --- | --- |"]
        lines += [f"| #{w['number']} | {w.get('gate')} | {_md_cell(w.get('reason'))} | {_md_cell(w.get('answer'))} |" for w in waiting]
    if rec.get("stop"):
        lines += [
            "",
            f"## 止まった理由: {rec['stop']['condition']}",
            "",
            f"- 値: {json.dumps(rec['stop']['values'], ensure_ascii=False)}",
            f"- 再開: `{resume_command(rec)}`",
        ]
    return "\n".join(lines) + "\n"
