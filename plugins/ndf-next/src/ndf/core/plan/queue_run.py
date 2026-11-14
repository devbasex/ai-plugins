"""キューを流す: 段（並びと `--then`）ごとに、触るファイルの重ならない組でプランを並べて流す。

- 各プランは子のプロセスの `ndf plan next` で流す。プランの回の鍵・回復・承認ゲートの待ちはプランの実行が持つ
- 起動したら、消えない場所（チェックアウトの共通の git ディレクトリの親）へ移り、プランと置き場を絶対のパスで渡す。
  作業場所から起動しても、前のプランの後片付けが作業場所を消した後の段が落ちない（#1886）
- 段は、前の段がすべて完了したときに流す。承認ゲートで待つプランがあっても、段のプランがすべて承認ゲートを人へ届ける役
  （`accepts_gate`）なら流す（#2876）。`{queue_prs}` は前の段で完了したプランの PR の番号で埋める
- キューの記録 `queue.json` に、プランごとの結果を書く。`--resume` は完了したプランを流さずに続ける
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ndf.contracts import result
from ndf.contracts.exit_codes import GATE, OK, STOPPED
from ndf.core.plan import model, queue_decide, runner

QUEUE_SUFFIX = ".queue"
QUEUE_FILE = "queue.json"
QUEUE_PRS = "queue_prs"


@dataclass(frozen=True)
class Entry:
    """キューの 1 本。`path` は絶対のパス、`start` は `--from` で渡した開始点。"""

    path: Path
    plan: model.Plan
    start: Optional[str] = None


def stable_dir(cwd: Path) -> Path:
    """消えない作業ディレクトリ（共通の git ディレクトリの親。主のチェックアウトの根）。"""
    out = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=cwd, capture_output=True, text=True, check=True
    )
    return Path(out.stdout.strip()).resolve().parent


def queue_dir(first: Path, given: Optional[str], cwd: Path) -> Path:
    """キューの記録の置き場。渡されなければ先頭のプランの隣の `<名前>.queue`。"""
    if given is not None:
        return (cwd / given).resolve()
    return first.with_name(first.stem + QUEUE_SUFFIX)


def _recorded(qdir: Path) -> dict[str, dict[str, Any]]:
    found = runner.read_json(qdir / QUEUE_FILE)
    return dict(found["plans"]) if found is not None else {}


def _record(qdir: Path, outcome: queue_decide.Outcome) -> None:
    plans = _recorded(qdir)
    plans[outcome.name] = {"status": outcome.status, "kind": outcome.kind, "prs": list(outcome.prs), "release": outcome.release}
    runner.write_json(qdir / QUEUE_FILE, {"plans": plans})


def _from_record(name: str, row: dict[str, Any]) -> queue_decide.Outcome:
    return queue_decide.Outcome(name, row["status"], row["kind"], "前の回で完了した", None, tuple(row["prs"]), bool(row["release"]))


def _outcome(entry: Entry, out: dict[str, Any]) -> queue_decide.Outcome:
    cause = out["cause"] if isinstance(out.get("cause"), dict) else {}
    prs = tuple(i["number"] for i in out["items"] if isinstance(i, dict) and i.get("kind") == "pr")
    step = next((i.get("step") for i in out["items"] if isinstance(i, dict) and i.get("kind") == "plan"), None)
    return queue_decide.Outcome(
        entry.plan.name, out["status"], cause.get("kind"), str(out["summary"]), out.get("routes"), prs, entry.plan.release, step
    )


def _child_args(entry: Entry, sets: dict[str, str]) -> list[str]:
    args = [sys.executable, "-m", "ndf", "plan", "next", str(entry.path)]
    if entry.start is not None:
        args += ["--from", entry.start]
    for name, value in sets.items():
        args += ["--set", f"{name}={value}"]
    return args


def _read_child(entry: Entry, proc: subprocess.Popen) -> queue_decide.Outcome:
    stdout, stderr = proc.communicate()
    lines = [line for line in stdout.splitlines() if line.strip()]
    try:
        out = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:
        out = None
    if not isinstance(out, dict) or out.get("status") not in (OK, GATE, STOPPED):
        detail = stderr.strip().splitlines()[-1] if stderr.strip() else f"終了コード {proc.returncode}"
        return queue_decide.Outcome(entry.plan.name, STOPPED, "bad_report", f"{entry.plan.name} が結果 JSON を返さなかった: {detail}")
    return _outcome(entry, out)


def _sets(entry: Entry, prior: list[queue_decide.Outcome]) -> tuple[dict[str, str], Optional[queue_decide.Outcome]]:
    """子へ渡す差し込みの値。`{queue_prs}` を使うのに PR が無ければ、流さずに止まった結果を返す。"""
    if "{" + QUEUE_PRS + "}" not in json.dumps(entry.plan.raw, ensure_ascii=False):
        return {}, None
    prs = queue_decide.queue_prs(prior, entry.plan.accepts_gate)
    if not prs:
        return {}, queue_decide.Outcome(entry.plan.name, STOPPED, "input_missing", "{queue_prs} を埋める完了したプランの PR が前の段に無い")
    return {QUEUE_PRS: " ".join(map(str, prs))}, None


def _needs_blocked(entry: Entry, order: list[str], done: dict[str, queue_decide.Outcome]) -> Optional[queue_decide.Outcome]:
    statuses = {name: o.status for name, o in done.items()}
    reason = queue_decide.blocked_reason(entry.plan.name, entry.plan.needs, order, statuses)
    return None if reason is None else queue_decide.Outcome(entry.plan.name, STOPPED, "precondition", reason)


def _run_wave(
    wave: list[Entry], prior: list[queue_decide.Outcome], cwd: Path, order: list[str], done: dict[str, queue_decide.Outcome]
) -> list[queue_decide.Outcome]:
    """組のプランを並べて流し、結果を組の並びで返す。"""
    started: list[tuple[Entry, Optional[subprocess.Popen], Optional[queue_decide.Outcome]]] = []
    for entry in wave:  # noqa: NDF201 上限: 組のプランの本数（キューの引数に渡したプランの数まで）
        sets, refused = _sets(entry, prior)
        refused = refused if refused is not None else _needs_blocked(entry, order, done)
        if refused is not None:
            started.append((entry, None, refused))
            continue
        proc = subprocess.Popen(_child_args(entry, sets), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        started.append((entry, proc, None))
    return [early if proc is None else _read_child(entry, proc) for entry, proc, early in started]


def run(stages: list[list[Entry]], qdir: Path, cwd: Path, resume: bool) -> list[queue_decide.Outcome]:
    """段ごとに流し、すべてのプランの結果をキューの並びで返す。流さなかったプランは status が None。"""
    recorded = _recorded(qdir) if resume else {}
    order = [e.plan.name for stage in stages for e in stage]
    done: dict[str, queue_decide.Outcome] = {}
    prior: list[queue_decide.Outcome] = []
    for stage in stages:
        reason = queue_decide.stage_blocked(prior, all(e.plan.accepts_gate for e in stage)) if prior else None
        if reason is not None:
            done.update({e.plan.name: queue_decide.Outcome(e.plan.name, None, None, reason) for e in stage})
            continue
        current: list[queue_decide.Outcome] = []
        for names in queue_decide.waves([queue_decide.Queued(e.plan.name, e.plan.touches, e.plan.needs) for e in stage]):
            wave = [e for e in stage if e.plan.name in names]
            kept = {e.plan.name: _from_record(e.plan.name, recorded[e.plan.name]) for e in wave if _finished(recorded, e)}
            todo = [e for e in wave if e.plan.name not in kept]
            outs = dict(kept) | {o.name: o for o in _run_wave(todo, prior, cwd, order, done)}
            for name in names:
                _record(qdir, outs[name])
                done[name] = outs[name]
                current.append(outs[name])
        prior += current
    return [done[name] for name in order]


def _finished(recorded: dict[str, dict[str, Any]], entry: Entry) -> bool:
    """`--resume` で流さないプラン（前の回で完了し、開始点を渡されていない）。"""
    row = recorded.get(entry.plan.name)
    return row is not None and row["status"] == OK and entry.start is None


def queue_result(tool: str, outcomes: list[queue_decide.Outcome], stages: list[list[Entry]], qdir: Path) -> dict[str, Any]:
    """キューの結果。next は `--resume` の打ち直し。

    承認ゲートで待つプランの routes は各行（items）に残し、結果の routes は先頭の 1 本の分にする（結果の routes の鍵は
    答えの語彙だけを持つ）。止まったプランは、打ち直しの `--from` を止まったステップにする。同じ回の報告を返さずに、
    止まった所から続く。
    """
    status, first = queue_decide.overall(outcomes)
    items = [_item(o) for o in outcomes]
    metrics = {"plans": len(outcomes), "ok": sum(o.status == OK for o in outcomes)}
    stopped_at = {o.name: o.step for o in outcomes if o.status == STOPPED and o.step is not None}
    resume = result.command(*again_args(stages, qdir, stopped_at), "--resume")
    if status == STOPPED and first is not None:
        kind = first.kind if first.kind is not None else "unknown"
        return result.stopped(tool, kind, f"{first.name}: {first.detail}", items=items, metrics=metrics, next=resume)
    if status == GATE:
        gated = next((o for o in outcomes if o.routes), None)
        # 答えを持たない待ち（流していないプラン）は、答えの語彙の「同じ作業を打ち直す」で打ち直す
        routes = dict(gated.routes) if gated is not None else {"retry": resume}
        where = f"routes は {gated.name} の分。" if gated is not None else ""
        summary = f"承認の答えを待つプランがある（{where}答えた後に next で続ける）"
        return result.result(tool, GATE, summary, items, metrics, routes=routes, next=resume)
    return result.result(tool, OK, f"{len(outcomes)} 本のプランが終わった", items, metrics)


def _item(o: queue_decide.Outcome) -> dict[str, Any]:
    item: dict[str, Any] = {"kind": "plan", "name": o.name, "status": o.status, "detail": o.detail}
    if o.routes:
        item["routes"] = dict(o.routes)
    return item


def again_args(stages: list[list[Entry]], qdir: Path, starts: Optional[dict[str, str]] = None) -> list[str]:
    """このキューを打ち直す引数（エントリポイントの名前の後ろ。絶対のパスで、作業場所の外から打っても同じキューを指す）。

    `starts` は開始点の上書き。
    """
    starts = starts if starts is not None else {}
    args = [runner.TOOL, "queue", *(str(e.path) for e in stages[0])]
    for stage in stages[1:]:
        args += ["--then", *(str(e.path) for e in stage)]
    for e in (e for stage in stages for e in stage):
        start = starts[e.plan.name] if e.plan.name in starts else e.start
        if start is not None:
            args += ["--from", f"{e.plan.name}={start}"]
    return [*args, "--queue-dir", str(qdir)]


def move_to_stable(cwd: Path) -> Path:
    """消えない作業ディレクトリへ移り、そのパスを返す（#1886）。"""
    stable = stable_dir(cwd)
    os.chdir(stable)
    return stable
