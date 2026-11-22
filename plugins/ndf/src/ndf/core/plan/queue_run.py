"""キューを流す: ステップ（並びと `--then`）ごとに、触るファイルの重ならない組でプランを並べて流す。

- 各プランは子のプロセスの `ndf plan next` で流す。プランの回のキー・回復・承認ゲートの待ちはプランの実行が持つ
- 起動したら、消えない場所（チェックアウトの共通の git ディレクトリの親）へ移り、プランと保存先を絶対のパスで渡す。
  作業場所から起動しても、前のプランのクリーンアップが作業場所を消した後のステップが落ちない（#1886）
- ステップは、前のステップがすべて完了したときに流す。承認ゲートで待つプランがあっても、ステップのプランがすべて承認ゲートを人へ届ける役
  （`accepts_gate`）なら流す（#2876）。`{queue_prs}` は前のステップで完了したプランの PR の番号で埋める
- キューの記録 `queue.json` に、プランごとの結果を書く。`--resume` は完了したプランを流さずに続ける
- `--fresh` は子の `ndf plan next` へ渡し、各プランを前の回に戻らず先頭から流す（#1592）。打ち直し（next）には渡さない
- `--max <n>` は同時に流すプランの本数の上限。触るファイルの重ならない組の中で、終わったプランの空いたスロットへ次のプランを
  起動し、`n` 本を保って流す（組を `n` 本ずつに区切って待たない）。打ち直しにも渡す
"""

from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ndf.adapters import run_log
from ndf.cli import CHILD_ENCODING, ndf_command
from ndf.contracts import result
from ndf.contracts.exit_codes import GATE, OK, STOPPED
from ndf.core.plan import files, model, queue_decide, runner, steps, wait_decide

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
    """キューの記録の保存先。渡されなければ先頭のプランの隣の `<名前>.queue`。"""
    if given is not None:
        return (cwd / given).resolve()
    return first.with_name(first.stem + QUEUE_SUFFIX)


def _recorded(qdir: Path) -> dict[str, dict[str, Any]]:
    found = files.read_json(qdir / QUEUE_FILE)
    return dict(found["plans"]) if found is not None else {}


def _record(qdir: Path, outcome: queue_decide.Outcome) -> None:
    plans = _recorded(qdir)
    plans[outcome.name] = {"status": outcome.status, "kind": outcome.kind, "prs": list(outcome.prs), "release": outcome.release}
    files.write_json(qdir / QUEUE_FILE, {"plans": plans})


def _from_record(name: str, row: dict[str, Any]) -> queue_decide.Outcome:
    return queue_decide.Outcome(name, row["status"], row["kind"], "前の回で完了した", None, tuple(row["prs"]), bool(row["release"]))


def _outcome(entry: Entry, out: dict[str, Any]) -> queue_decide.Outcome:
    cause = out["cause"] if isinstance(out.get("cause"), dict) else {}
    prs = tuple(i["number"] for i in out["items"] if isinstance(i, dict) and i.get("kind") == "pr")
    step = next((i.get("step") for i in out["items"] if isinstance(i, dict) and i.get("kind") == "plan"), None)
    return queue_decide.Outcome(
        entry.plan.name, out["status"], cause.get("kind"), str(out["summary"]), out.get("routes"), prs, entry.plan.release, step
    )


def _child_args(entry: Entry, sets: dict[str, str], fresh: bool = False) -> list[str]:
    args = ndf_command("plan", "next", str(entry.path))
    if entry.start is not None:
        args += ["--from", entry.start]
    for name, value in sets.items():
        args += ["--set", f"{name}={value}"]
    if fresh:
        args.append("--fresh")
    return args


def _read_child(entry: Entry, proc: subprocess.Popen) -> queue_decide.Outcome:
    stdout, stderr = proc.communicate()
    out = steps.last_json(stdout)
    if out is None:
        detail = run_log.redact(stderr.strip().splitlines()[-1]) if stderr.strip() else f"終了コード {proc.returncode}"
        return queue_decide.Outcome(entry.plan.name, STOPPED, "bad_report", f"{entry.plan.name} が結果 JSON を返さなかった: {detail}")
    return _outcome(entry, out)


def _sets(entry: Entry, prior: list[queue_decide.Outcome]) -> tuple[dict[str, str], Optional[queue_decide.Outcome]]:
    """子へ渡す差し込みの値。`{queue_prs}` を使うのに PR が無ければ、流さずに止まった結果を返す。"""
    if "{" + QUEUE_PRS + "}" not in json.dumps(entry.plan.raw, ensure_ascii=False):
        return {}, None
    prs = queue_decide.queue_prs(prior, entry.plan.accepts_gate)
    if not prs:
        return {}, queue_decide.Outcome(
            entry.plan.name, STOPPED, "input_missing", "{queue_prs} を埋める完了したプランの PR が前のステップに無い"
        )
    return {QUEUE_PRS: " ".join(map(str, prs))}, None


def _needs_blocked(entry: Entry, order: list[str], done: dict[str, queue_decide.Outcome]) -> Optional[queue_decide.Outcome]:
    statuses = {name: o.status for name, o in done.items()}
    reason = queue_decide.blocked_reason(entry.plan.name, entry.plan.needs, order, statuses)
    return None if reason is None else queue_decide.Outcome(entry.plan.name, STOPPED, "precondition", reason)


@dataclass(frozen=True)
class QueueOptions:
    """キューの選択肢。`resume` は完了したプランを流さずに続け、`fresh` は子の `ndf plan next` へ `--fresh` を渡す（#1592）。

    `max_parallel` は同時に流すプランの本数の上限（None は組の本数まで）。空いたスロットへ次のプランを起動して本数を保つ。
    """

    resume: bool = False
    fresh: bool = False
    max_parallel: Optional[int] = None


class _QueueExecution:
    """1 回のキューの実行の状態。`done` は流した・止めたプランの結果、`prior` は前のステップまでの結果（ステップを流し終えてから足す）。"""

    def __init__(self, stages: list[list[Entry]], qdir: Path, cwd: Path, options: QueueOptions) -> None:
        self.qdir = qdir
        self.cwd = cwd
        self.options = options
        self.recorded = _recorded(qdir) if options.resume else {}
        self.order = [e.plan.name for stage in stages for e in stage]
        self.done: dict[str, queue_decide.Outcome] = {}
        self.prior: list[queue_decide.Outcome] = []

    def run(self, stages: list[list[Entry]]) -> list[queue_decide.Outcome]:
        for stage in stages:
            self._run_stage(stage)
        return [self.done[name] for name in self.order]

    def _run_stage(self, stage: list[Entry]) -> None:
        """ステップを組ごとに流す。前のステップが止まっていれば流さずに、ステップのプランを status が None の結果にする。"""
        reason = queue_decide.stage_blocked(self.prior, all(e.plan.accepts_gate for e in stage)) if self.prior else None
        if reason is not None:
            self.done.update({e.plan.name: queue_decide.Outcome(e.plan.name, None, None, reason) for e in stage})
            return
        current: list[queue_decide.Outcome] = []
        queued = [queue_decide.Queued(e.plan.name, e.plan.touches, e.plan.needs) for e in stage]
        for names in queue_decide.waves(queued, {o.name for o in self.prior}):
            current += self._run_group(stage, names)
        self.prior += current

    def _run_group(self, stage: list[Entry], names: list[str]) -> list[queue_decide.Outcome]:
        """組を流し、各結果を `queue.json` へ書いてから `done` に載せる。結果を組の並びで返す。"""
        wave = [e for e in stage if e.plan.name in names]
        kept = {e.plan.name: _from_record(e.plan.name, self.recorded[e.plan.name]) for e in wave if _finished(self.recorded, e)}
        todo = [e for e in wave if e.plan.name not in kept]
        outs = dict(kept) | {o.name: o for o in self._launch(todo)}
        for name in names:
            _record(self.qdir, outs[name])
            self.done[name] = outs[name]
        return [outs[name] for name in names]

    def _launch(self, wave: list[Entry]) -> list[queue_decide.Outcome]:
        """組のプランを `--max` の本数まで並べて流し、結果を組の並びで返す。流さないプランは起動せずに結果にする。"""
        early: dict[str, queue_decide.Outcome] = {}
        ready: list[tuple[Entry, dict[str, str]]] = []
        for entry in wave:
            sets, refused = _sets(entry, self.prior)
            refused = refused if refused is not None else _needs_blocked(entry, self.order, self.done)
            if refused is not None:
                early[entry.plan.name] = refused
            else:
                ready.append((entry, sets))
        size = self.options.max_parallel
        ran = self._all_at_once(ready) if size is None or size >= len(ready) else self._rolling(ready, size)
        outs = early | ran
        return [outs[e.plan.name] for e in wave]

    def _start(self, entry: Entry, sets: dict[str, str]) -> subprocess.Popen:
        return subprocess.Popen(
            _child_args(entry, sets, self.options.fresh),
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding=CHILD_ENCODING,
            errors="replace",
        )

    def _all_at_once(self, ready: list[tuple[Entry, dict[str, str]]]) -> dict[str, queue_decide.Outcome]:
        """すべて起動してから、組の並びで回収する。"""
        started = [(entry, self._start(entry, sets)) for entry, sets in ready]
        return {entry.plan.name: _read_child(entry, proc) for entry, proc in started}

    def _rolling(self, ready: list[tuple[Entry, dict[str, str]]], size: int) -> dict[str, queue_decide.Outcome]:
        """`size` 本を保って流す。終わったプランを回収するたびに、空いたスロットへ次のプランを起動する。

        子の出力は回収の糸（thread）ごとに読み切るため、出力の多い子がパイプで詰まって終わらない形にならない。
        """
        pending = list(ready)
        outs: dict[str, queue_decide.Outcome] = {}
        running: dict[Future[queue_decide.Outcome], Entry] = {}
        with ThreadPoolExecutor(max_workers=size) as pool:
            while pending or running:
                while pending and len(running) < size:
                    entry, sets = pending.pop(0)
                    running[pool.submit(_read_child, entry, self._start(entry, sets))] = entry
                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in finished:
                    outs[running.pop(future).plan.name] = future.result()
        return outs


def run(stages: list[list[Entry]], qdir: Path, cwd: Path, options: QueueOptions) -> list[queue_decide.Outcome]:
    """ステップごとに流し、すべてのプランの結果をキューの並びで返す。流さなかったプランは status が None。

    `options.fresh` は子の `ndf plan next` へ `--fresh` を渡す（#1592）。
    """
    return _QueueExecution(stages, qdir, cwd, options).run(stages)


def _finished(recorded: dict[str, dict[str, Any]], entry: Entry) -> bool:
    """`--resume` で流さないプラン（前の回で完了し、開始点を渡されていない）。"""
    row = recorded.get(entry.plan.name)
    return row is not None and row["status"] == OK and entry.start is None


def queue_result(
    tool: str, outcomes: list[queue_decide.Outcome], stages: list[list[Entry]], qdir: Path, max_parallel: Optional[int] = None
) -> dict[str, Any]:
    """キューの結果。next は `--resume` の打ち直し。

    承認ゲートで待つプランの routes は各行（items）に残し、結果の routes は先頭の 1 本の分にする（結果の routes のキーは
    回答の語彙だけを持つ）。止まったプランは、打ち直しの `--from` を止まったステップにする。同じ回の報告を返さずに、
    止まった所から続く。
    """
    status, first = queue_decide.overall(outcomes)
    items = [_item(o) for o in outcomes]
    metrics = {"plans": len(outcomes), "ok": sum(o.status == OK for o in outcomes)}
    stopped_at = {o.name: o.step for o in outcomes if o.status == STOPPED and o.step is not None}
    resume = result.command(*again_args(stages, qdir, stopped_at, max_parallel), "--resume")
    if status == STOPPED and first is not None:
        return _stopped_result(tool, first, items, metrics, resume)
    if status == GATE:
        return _gate_result(tool, outcomes, items, metrics, resume)
    return result.result(tool, OK, f"{len(outcomes)} 本のプランが終わった", items, metrics)


def _stopped_result(
    tool: str, first: queue_decide.Outcome, items: list[dict[str, Any]], metrics: dict[str, int], resume: dict[str, Any]
) -> dict[str, Any]:
    """先頭の止まったプランの種類で止まった結果。"""
    kind = first.kind if first.kind is not None else "unknown"
    return result.stopped(tool, kind, f"{first.name}: {first.detail}", items=items, metrics=metrics, next=resume)


def _gate_result(
    tool: str, outcomes: list[queue_decide.Outcome], items: list[dict[str, Any]], metrics: dict[str, int], resume: dict[str, Any]
) -> dict[str, Any]:
    """承認の回答を待つ結果。routes は routes を持つ先頭の 1 本の分。"""
    gated = next((o for o in outcomes if o.routes), None)
    routes = wait_decide.gate_routes(gated.routes if gated is not None else None, resume)
    where = f"routes は {gated.name} の分。" if gated is not None else ""
    summary = f"承認の回答を待つプランがある（{where}回答した後に next で続ける）"
    return result.result(tool, GATE, summary, items, metrics, routes=routes, next=resume)


def _item(o: queue_decide.Outcome) -> dict[str, Any]:
    item: dict[str, Any] = {"kind": "plan", "name": o.name, "status": o.status, "detail": o.detail}
    if o.routes:
        item["routes"] = dict(o.routes)
    return item


def again_args(
    stages: list[list[Entry]], qdir: Path, starts: Optional[dict[str, str]] = None, max_parallel: Optional[int] = None
) -> list[str]:
    """このキューを打ち直す引数（エントリポイントの名前の後ろ。絶対のパスで、作業場所の外から打っても同じキューを指す）。

    `starts` は開始点の上書き。`max_parallel` があれば `--max` で渡す。
    """
    starts = starts if starts is not None else {}
    args = [runner.TOOL, "queue", *(str(e.path) for e in stages[0])]
    for stage in stages[1:]:
        args += ["--then", *(str(e.path) for e in stage)]
    for e in (e for stage in stages for e in stage):
        start = starts[e.plan.name] if e.plan.name in starts else e.start
        if start is not None:
            args += ["--from", f"{e.plan.name}={start}"]
    if max_parallel is not None:
        args += ["--max", str(max_parallel)]
    return [*args, "--queue-dir", str(qdir)]


def move_to_stable(cwd: Path) -> Path:
    """消えない作業ディレクトリへ移り、そのパスを返す（#1886）。"""
    stable = stable_dir(cwd)
    os.chdir(stable)
    return stable
