"""進行表を読んで工程を流す（`ndf progress next`・`wait`・`answer`・`resync` と、`next` が起こす `work`）。

進行表の状態の正本は事象で、次に何をするかは事象を畳み込んだ表だけから決める（`decide`。受け入れ条件 4）。
再開に `--from` を組み立てる必要は無い。終わったステップ（事象 `step_recorded`）は流し直さない。

- `next` は排他（`store.run_lock`）を取って次の 1 手を決め、工程を流す作業（`work`）を別のプロセスで起こしてすぐ返る。
  長く動く工程を前景で抱えない。待ちは `wait <進行表>` の 1 本で行い、上限で戻れば同じ `wait` を打ち直す（3366-1）
- `work` は排他を持ったまま工程のステップを順に呼ぶ（`invoke`）。打ち直しで直る止まり（止まった理由の表の
  行き先が `retry`）はここで方針どおりに打ち直し、conductor へ返すのは人か conductor の判断が要る止まりだけで、
  `routes` を持つ（SF8）。終了コードは止まった理由の種類から引く
- 承認ゲート（`ndf.listen`）とプランの中の承認ゲートは、行を実行中のまま `work` を抜け、`next` が `gate` と
  答えごとの `routes` を返す。答えは `answer` が事象 `answered` で残す。差し戻し（`revise`）には指摘ファイルが
  要り、指摘の本文も事象に残る（3365-4）
- 工程の出力は工程の出力の規約で照らしてから終了の印を付ける。合わなければ印を付けず失敗にする（受け入れ条件 8）
- 課題の工程表とスプリント PR の「通した工程」は終了の印から作る（受け入れ条件 6）。書けなくても工程は進め、
  `resync` で何度でも同じ結果へ揃え直せる（3377-9）
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Protocol

from ndf.adapters import paths
from ndf.adapters.git import Git, GitError
from ndf.adapters.github import GitHub, GitHubError, client
from ndf import identity
from ndf.contracts import causes, facts, limits, result
from ndf.core import durable
from ndf.core.plan import cli as plan_cli
from ndf.progress import command, definition, invoke, model, stages, store

TOOL = __package__.rpartition(".")[2]
ACTOR = invoke.ACTOR
STEP_EVENT = "step_recorded"
# 番号のぶつかり（同時の追記）で読み直して書き直す回数
APPEND_ATTEMPTS = command.APPEND_ATTEMPTS
# 外から待つ問い合わせの間隔と、作業が排他を取るまでの待ちの間隔（どちらも宣言の値）
WAIT_POLL = plan_cli.WAIT_POLL
LOCK_POLL = durable.POLL
WAIT_DEFAULT_SECONDS = plan_cli.WAIT_DEFAULT_SECONDS
# 作業の置き場（チェックアウトの置き場の下）
WORK_DIR = "progress-work"
# 進捗記録の節の見出しと終わりの印、時刻の表示の時間帯（JST）
ISSUE_SECTION = "## 進行"
PR_SECTION = "## 通した工程"
SECTION_END = f"<!-- {identity.FINAL_NAME}:section-end -->"
JST = timezone(timedelta(hours=9))


class FactUnknown(Exception):
    """工程の定義の条件（`when`）の語を、今の材料から求められない。"""

    def __init__(self, word: str) -> None:
        super().__init__(f"条件 {word} を求められない")
        self.word = word


# --- 進行表の読み書き ---------------------------------------------------------------------


@dataclass(frozen=True)
class View:
    """1 つのトランザクションで読んだ進行表（最新の改訂・定義・事象）。"""

    table_id: str
    rev: int
    snap: stages.Snapshot
    doc: dict[str, Any]
    events: list[model.Event]

    @property
    def rows(self) -> dict[str, model.RowState]:
        return model.fold(self.events, self.rev)

    @property
    def whole(self) -> Optional[str]:
        return model.table_state("sprint", self.events)

    def row_events(self, stage: str) -> list[model.Event]:
        return [e for e in self.events if e.draft.row_key == stage and e.draft.rev == self.rev]

    def context(self) -> dict[str, Any]:
        """スプリントの文脈。作成の時点の値に、工程が書いた出力（`outputs_recorded`）を順に重ねる。"""
        out = dict(self.doc["context"])
        for ev in self.events:
            if ev.draft.kind == invoke.OUTPUTS_EVENT and ev.draft.rev == self.rev:
                out.update(_payload(ev)["outputs"])
        return out


def _payload(ev: model.Event) -> dict[str, Any]:
    return ev.draft.payload if ev.draft.payload is not None else {}


def view(conn: Any, table_id: str) -> View:
    if store.read_table(conn, table_id) is None:
        raise model.Refused(f"進行表 {table_id} は無い")
    rev, raw = store.read_definition(conn, table_id)
    return View(table_id, rev, stages.snapshot_from(raw), raw, store.read_events(conn, table_id))


def read(st: store.Store, table_id: str) -> View:
    with store.transaction(st) as conn:
        return view(conn, table_id)


def append(st: store.Store, table_id: str, build: Callable[[View], list[model.EventDraft]]) -> list[int]:
    """今の進行表を読み、`build` が作った下書きを追記する。番号がぶつかれば読み直して作り直す。"""
    for attempt in range(1, APPEND_ATTEMPTS + 1):
        try:
            with store.transaction(st) as conn:
                v = view(conn, table_id)
                drafts = build(v)
                return store.append_events(conn, table_id, drafts, v.events[-1].seq if v.events else 0) if drafts else []
        except store.Conflict:
            if attempt == APPEND_ATTEMPTS:
                raise
    raise AssertionError("到達しない")


def _write(st: store.Store, table_id: str, *drafts: model.EventDraft) -> list[int]:
    return append(st, table_id, lambda _v: list(drafts))


# --- 工程の中のステップの進み ---------------------------------------------------------------


@dataclass
class StepProgress:
    """実行中の行のステップの進み。`generation` は差し戻しの回数で、差し戻すとステップを初めから流し直す。

    `anchor` は答えを数え始める事象の番号（行の開始・ステップの記録・差し戻しの後の答えだけを使う）。
    """

    generation: int = 0
    done: dict[str, dict[str, Any]] = field(default_factory=dict)
    waiting: Optional[dict[str, Any]] = None
    anchor: int = 0
    answer: Optional[model.Event] = None
    notes: Optional[str] = None

    def next_step(self, stage: stages.StageDef) -> Optional[stages.StepDef]:
        for st in stage.steps:
            if st.id not in self.done:
                return st
        return None

    def answer_value(self) -> Optional[str]:
        return str(_payload(self.answer)["answer"]) if self.answer is not None else None


def _seen(prog: StepProgress, ev: model.Event) -> None:
    kind, p = ev.draft.kind, _payload(ev)
    if kind == "started":
        prog.anchor, prog.answer, prog.waiting = ev.seq, None, None
    elif kind == STEP_EVENT:
        if p["status"] == "finished":
            prog.done[p["step"]], prog.waiting = p["report"], None
        else:
            prog.waiting = p
        prog.anchor, prog.answer = ev.seq, None
    elif kind == "answered" and p["answer"] == "revise" and p["kind"] == invoke.LISTEN:
        prog.generation += 1
        prog.done, prog.waiting, prog.answer, prog.anchor = {}, None, None, ev.seq
        prog.notes = p["notes"]
    elif kind == "answered" and ev.seq > prog.anchor:
        prog.answer = ev


def step_progress(v: View, stage: str) -> StepProgress:
    prog = StepProgress()
    for ev in v.row_events(stage):
        _seen(prog, ev)
    return prog


# --- 次の 1 手（純粋な判断） ----------------------------------------------------------------


@dataclass(frozen=True)
class Move:
    """次の 1 手。`action` は done・complete・start・resume・gate・consume・failed のどれか。"""

    action: str
    stage: Optional[stages.StageDef] = None
    step: Optional[stages.StepDef] = None
    progress: Optional[StepProgress] = None
    event: Optional[model.Event] = None


def _last(events: Iterable[model.Event], kind: str) -> Optional[model.Event]:
    found = None
    for ev in events:
        if ev.draft.kind == kind:
            found = ev
    return found


def _running_move(v: View, s: stages.StageDef) -> Move:
    prog = step_progress(v, s.name)
    step = prog.next_step(s)
    if step is None:
        return Move("resume", s, None, prog)
    listen = invoke.CALLS[step.call] == invoke.LISTEN
    waiting = listen or (prog.waiting is not None and prog.waiting["step"] == step.id)
    if waiting and prog.answer is None:
        return Move("gate", s, step, prog)
    return Move("consume" if listen else "resume", s, step, prog, prog.answer)


def decide(v: View) -> Move:
    """事象を畳み込んだ表だけから次の 1 手を決める。終わった行（終了・無効・中止）は飛ばす。"""
    if model.is_terminal(v.whole):
        return Move("done")
    rows = v.rows
    for s in v.snap.stages:
        state = rows[s.name].state if s.name in rows else None
        if model.is_terminal(state):
            continue
        if state == "failed":
            return Move("failed", s, event=_last(v.row_events(s.name), "failed"))
        if state == "running":
            return _running_move(v, s)
        return Move("start", s)
    return Move("complete")


# --- 結果 JSON ------------------------------------------------------------------------------


def _cmd(*args: str, ask: tuple[str, ...] = ()) -> dict[str, Any]:
    return result.command(TOOL, *args, ask=ask)


def _find_enum(node: Any, key: str) -> Optional[list[str]]:
    """スキーマの木から、性質 `key` の `enum` を探す。"""
    if isinstance(node, dict):
        props = node["properties"] if isinstance(node.get("properties"), dict) else {}
        if key in props and isinstance(props[key], dict) and "enum" in props[key]:
            return list(props[key]["enum"])
        children: Iterable[Any] = node.values()
    elif isinstance(node, list):
        children = node
    else:
        return None
    for child in children:
        found = _find_enum(child, key)
        if found is not None:
            return found
    return None


def choices(m: Move) -> list[str]:
    """待っているステップへの答えの選択肢。承認ゲートは出力の規約の答えの語、プランの承認ゲートはプランの routes。"""
    assert m.step is not None and m.progress is not None
    known = facts.ids(result.ANSWERS)
    if invoke.CALLS[m.step.call] == invoke.LISTEN:
        listed = _find_enum(definition.ref_target(m.step.output_ref), "answer") if m.step.output_ref else None
        return [a for a in (listed if listed is not None else []) if a in known]
    waiting = m.progress.waiting if m.progress.waiting is not None else {}
    routes = waiting["report"]["routes"] if isinstance(waiting["report"].get("routes"), dict) else {}
    return [a for a in routes if a in known]


def _gate_routes(table_id: str, m: Move) -> dict[str, Any]:
    assert m.stage is not None
    out = {}
    for a in choices(m):
        ask = ("notes", "reason") if a == "revise" else ("reason",)
        out[a] = _cmd("answer", table_id, m.stage.name, "--answer", a, ask=ask)
    return out


def failed_routes(table_id: str, stage: str) -> dict[str, Any]:
    """失敗した行で conductor が選ぶ手（答えの語彙の retry・finish・cancel）。"""
    return {
        "retry": _cmd("retry", table_id, stage, ask=("reason",)),
        "finish": _cmd("finish", table_id, stage, ask=("output", "reason")),
        "cancel": _cmd("cancel", table_id, ask=("reason",)),
    }


def _metrics(v: View, m: Move) -> dict[str, Any]:
    out: dict[str, Any] = {"table_id": v.table_id, "rev": v.rev, "state": v.whole, "move": m.action}
    if m.stage is not None:
        out["stage"] = m.stage.name
    if m.step is not None:
        out["step"] = m.step.id
    return out


def _failed_report(v: View, m: Move) -> dict[str, Any]:
    assert m.stage is not None
    p = _payload(m.event) if m.event is not None else {}
    cause = p["cause"] if isinstance(p.get("cause"), dict) else {}
    kind = causes.kind_of(cause)
    kind = kind if kind is not None and kind in causes.table() else "unknown"
    detail = cause["detail"] if isinstance(cause.get("detail"), str) and cause["detail"] else f"工程 {m.stage.name} が失敗した"
    summary = f"工程 {m.stage.name} が失敗した: {detail}"
    return result.stopped(TOOL, kind, detail, summary=summary, metrics=_metrics(v, m), routes=failed_routes(v.table_id, m.stage.name))


def report(v: View, m: Move) -> dict[str, Any]:
    """副作用の無い 1 手の結果（`wait`・`show` と、`next` が流せない手）。"""
    if m.action == "done":
        return result.result(TOOL, result.OK, f"進行表 {v.table_id} は{model.label(v.whole)}", metrics=_metrics(v, m))
    if m.action == "failed":
        return _failed_report(v, m)
    assert m.stage is not None
    if m.action == "gate":
        assert m.step is not None
        summary = f"工程 {m.stage.name} のステップ {m.step.id} が答えを待っている"
        return result.result(TOOL, result.GATE, summary, metrics=_metrics(v, m), routes=_gate_routes(v.table_id, m))
    summary = f"次に流すのは工程 {m.stage.name}（{m.action}）"
    return result.result(TOOL, result.OK, summary, metrics=_metrics(v, m), next=_cmd("next", v.table_id))


# --- 依存（テストが差し替える） ------------------------------------------------------------


class Records(Protocol):
    """課題と PR の本文の読み書き。"""

    def read(self, number: int) -> str: ...

    def write(self, number: int, body: str) -> None: ...


@dataclass(frozen=True)
class GitHubRecords:
    hub: GitHub

    def read(self, number: int) -> str:
        return self.hub.issue_body(number).body

    def write(self, number: int, body: str) -> None:
        self.hub.rest("PATCH", f"/repos/{self.hub.repo}/issues/{int(number)}", {"body": body})


def github_records(cwd: Path) -> Records:
    return GitHubRecords(GitHub(Git(cwd).remote_repo(), client()))


Facts = Callable[[tuple[str, ...], Mapping[str, Any], Path], Mapping[str, bool]]
Launch = Callable[[store.Store, str, Path], None]
Invoker = Callable[[invoke.StageRun, Path, Path, Optional[str]], invoke.StageResult]


def launch_worker(st: store.Store, table_id: str, cwd: Path) -> None:
    """工程を流す作業（`ndf progress work`）を、自分の終わりに巻き込まれない別のプロセスの組で起こす。

    作業の標準出力と標準エラーは作業の置き場の `work.log` へ追記する。"""
    log = work_root(st.root, table_id) / "work.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as out:
        argv = invoke.ndf_command(TOOL, "work", table_id)
        env = invoke.ndf_env(os.environ)
        subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)


def _invoke(run: invoke.StageRun, cwd: Path, work_dir: Path, answer: Optional[str]) -> invoke.StageResult:
    return invoke.invoke(run, cwd, work_dir, answer)


@dataclass(frozen=True)
class Env:
    """`next`・`work` が使う外との接点。`facts` は工程の条件の語の値、`records` は進捗記録の置き場を作る。"""

    facts: Facts
    launch: Launch = launch_worker
    invoker: Invoker = _invoke
    records: Callable[[Path], Records] = github_records
    sleep: Callable[[float], None] = time.sleep


def work_root(root: Path, table_id: str) -> Path:
    """進行表ごとの作業の置き場（プランの実行の置き場と作業の記録）。名前は進行表の識別子の sha256 の頭。"""
    return root / WORK_DIR / hashlib.sha256(table_id.encode()).hexdigest()[: model.REPO_KEY_LENGTH]


# --- 進捗記録（受け入れ条件 6・3377-9） ------------------------------------------------------


def _record_line(name: str, row: Optional[model.RowState]) -> str:
    if row is not None and row.state == "finished" and row.at is not None:
        return f"- [x] {name} — {row.at.astimezone(JST):%Y-%m-%d %H:%M}"
    if row is not None and row.state in ("disabled", "cancelled"):
        why = f"（{row.reason}）" if row.reason else ""
        return f"- [ ] {name} — {model.label(row.state)}{why}"
    return f"- [ ] {name}"


def issue_section(v: View) -> str:
    """課題の工程表（節「進行」の中身）。全工程を並べ、終了の印が付いた行だけに印と時刻を付ける。"""
    rows = v.rows
    lines = [f"モード: {v.snap.mode}", ""] + [_record_line(s.name, rows.get(s.name)) for s in v.snap.stages]
    return "\n".join(lines)


def pr_section(v: View) -> str:
    """スプリント PR の「通した工程」。終了の印が付いた行だけを並べる。"""
    rows = v.rows
    done = [_record_line(s.name, rows[s.name]) for s in v.snap.stages if s.name in rows and rows[s.name].state == "finished"]
    return "\n".join(line.replace("- [x] ", "- ", 1) for line in done)


def replace_section(body: str, heading: str, content: str) -> str:
    """本文の節（見出しから終わりの印まで）を書き直す。無ければ末尾へ足す。何度打っても同じ本文になる。"""
    block = f"{heading}\n\n{content}\n{SECTION_END}\n"
    pattern = re.compile(rf"^{re.escape(heading)}\n.*?^{re.escape(SECTION_END)}\n?", re.MULTILINE | re.DOTALL)
    if pattern.search(body):
        return pattern.sub(lambda _m: block, body, count=1)
    return f"{body.rstrip(chr(10))}\n\n{block}" if body.strip() else block


def _targets(v: View) -> list[tuple[str, int, str, str]]:
    ctx = v.context()
    out = [("issue", int(n), ISSUE_SECTION, issue_section(v)) for n in ctx["issues"]]
    if "pr" in ctx:
        out.append(("pr", int(ctx["pr"]), PR_SECTION, pr_section(v)))
    return out


def sync_records(v: View, cwd: Path, env: Env) -> tuple[list[dict[str, Any]], list[str]]:
    """課題と PR の本文の進捗の節を、進行表の終了の印から書き直す。書けなかった置き場は誤りに載せる。"""
    try:
        records = env.records(cwd)
    except (GitError, GitHubError) as exc:
        return [], [f"進捗記録を書けない: {exc}"]
    items, errors = [], []
    for kind, number, heading, content in _targets(v):
        try:
            body = records.read(number)
            new = replace_section(body, heading, content)
            if new != body:
                records.write(number, new)
            items.append({"kind": kind, "number": number, "changed": new != body})
        except GitHubError as exc:
            errors.append(f"{kind} #{number} の進捗記録を書けない: {exc}")
    return items, errors


def resync(st: store.Store, table_id: str, cwd: Path, env: Env) -> dict[str, Any]:
    v = read(st, table_id)
    items, errors = sync_records(v, cwd, env)
    if errors:
        return result.stopped(TOOL, "transient", "; ".join(errors), items=items, next=_cmd("resync", table_id))
    return result.result(TOOL, result.OK, f"進行表 {table_id} の進捗記録を揃えた（{len(items)} 件）", items=items)


# --- 手を進める（next と work） ---------------------------------------------------------------


@dataclass
class Drive:
    """1 回の駆動。`inline` が真なら工程をこのプロセスで流し（`work`）、偽なら作業を起こして返す（`next`）。"""

    st: store.Store
    table_id: str
    cwd: Path
    env: Env
    inline: bool
    warnings: list[str] = field(default_factory=list)

    def write(self, *drafts: model.EventDraft) -> None:
        _write(self.st, self.table_id, *drafts)

    def synced(self) -> None:
        """行を終えた後に進捗記録を書き直す。記録の失敗は工程を止めず警告に載せる。"""
        _, errors = sync_records(read(self.st, self.table_id), self.cwd, self.env)
        self.warnings.extend(errors)


def _forced(v: View, stage: str) -> bool:
    """conductor が無効から戻した行は、定義の条件を見ずに流す。"""
    last = None
    for ev in v.row_events(stage):
        if ev.draft.state is not None:
            last = ev.draft.kind
    return last == "enabled"


RETRO_STAGE_ID = "retrospective"


def _retro_enabled(v: View) -> bool:
    """振り返りの工程が進行表にあって終わったか。無い・無効・中止なら偽（#3092 の前提 4）。"""
    rows = v.rows
    return any(s.id == RETRO_STAGE_ID and s.name in rows and rows[s.name].state == "finished" for s in v.snap.stages)


TABLE_FACTS: dict[str, Callable[[View], bool]] = {"retro_enabled": _retro_enabled}  # 進行表から求める条件の語


def _when_off(d: Drive, v: View, s: stages.StageDef) -> Optional[str]:
    """定義の条件のうち最初の偽の語。すべて真か、conductor が戻した行なら None。求められなければ `FactUnknown`。"""
    if not s.when or _forced(v, s.name):
        return None
    outer = tuple(w for w in s.when if w not in TABLE_FACTS)
    values = {**d.env.facts(outer, v.context(), d.cwd), **{w: TABLE_FACTS[w](v) for w in s.when if w in TABLE_FACTS}}
    off = [w for w in s.when if not values[w]]
    return off[0] if off else None


def _start(d: Drive, v: View, s: stages.StageDef) -> Optional[dict[str, Any]]:
    """未着手の行を始める。条件が偽なら無効として飛ばし、入力が欠けるか形が違えば始めずに止まる。"""
    m = Move("start", s)
    try:
        off = _when_off(d, v, s)
    except FactUnknown as exc:
        detail = f"工程 {s.name} の{exc}（流すなら disable の後に enable、外すなら disable を打つ）"
        return result.stopped(TOOL, "precondition", detail, metrics=_metrics(v, m))
    if off is not None:
        d.write(model.EventDraft("skipped", ACTOR, s.name, reason=f"条件 {off} が偽", payload={"when": off}, rev=v.rev))
        return None
    try:
        inputs = invoke.resolve_inputs(s, v.context())
    except invoke.StageInputMissing as exc:
        return result.stopped(TOOL, "input_missing", str(exc), items=[{"key": k} for k in exc.keys], metrics=_metrics(v, m))
    except invoke.StageInputInvalid as exc:
        return result.stopped(TOOL, "invalid", str(exc), items=[{"error": e} for e in exc.errors], metrics=_metrics(v, m))
    d.write(model.EventDraft("started", ACTOR, s.name, payload={"inputs": inputs}, rev=v.rev))
    return None if d.inline else _launched(d, v, m)


def _launched(d: Drive, v: View, m: Move) -> dict[str, Any]:
    assert m.stage is not None
    d.env.launch(d.st, d.table_id, d.cwd)
    summary = f"工程 {m.stage.name} から流す作業を起こした。承認ゲート・失敗・完了までは wait で待つ"
    return result.result(TOOL, result.OK, summary, metrics=_metrics(v, m), next=_cmd("wait", d.table_id))


def gate_report(step: stages.StepDef, answer: model.Event) -> dict[str, Any]:
    """承認ゲートのステップの出力（答えを持つ結果 JSON）。"""
    p = _payload(answer)
    summary = f"承認ゲート {step.gate} の答え: {p['answer']}（{answer.draft.reason}）"
    return result.result(TOOL, result.OK, summary, metrics={"answer": p["answer"], "seq": answer.seq})


def _consume(d: Drive, v: View, m: Move) -> None:
    """承認ゲートの答えを読む。承認ならステップを終え、承認しないなら行を失敗にして conductor へ返す。"""
    assert m.stage is not None and m.step is not None and m.event is not None and m.progress is not None
    if _payload(m.event)["answer"] == "approved":
        rep = gate_report(m.step, m.event)
        payload = {"step": m.step.id, "status": "finished", "report": rep, "generation": m.progress.generation}
        d.write(model.EventDraft(STEP_EVENT, ACTOR, m.stage.name, payload=payload, rev=v.rev))
        return
    detail = f"承認ゲート {m.step.gate} で承認されなかった（{m.event.draft.reason}）"
    d.write(_failed(m.stage, m.step.id, v.rev, "refused", detail))
    d.synced()


def _complete(d: Drive, v: View) -> dict[str, Any]:
    d.write(model.EventDraft("completed", ACTOR, None, rev=v.rev))
    d.synced()
    done = read(d.st, d.table_id)
    return result.result(TOOL, result.OK, f"進行表 {d.table_id} を完了にした", metrics=_metrics(done, Move("done")))


def _resume(d: Drive, v: View, m: Move) -> Optional[dict[str, Any]]:
    """実行中の行の続き。`next` なら作業を起こし、`work` なら次のステップか工程の終わりを流す。"""
    assert m.stage is not None
    if not d.inline:
        return _launched(d, v, m)
    if m.step is None:
        _finish_stage(d, v, m.stage)
    else:
        d.write(_run_step(d, v, m.stage, m.step))
    if decide(read(d.st, d.table_id)).stage != m.stage:
        d.synced()
    return None


def _step(d: Drive, v: View, m: Move) -> Optional[dict[str, Any]]:
    """1 手を打つ。続けて次の手を決めるなら None。"""
    if m.action == "complete":
        return _complete(d, v)
    if m.action == "consume":
        _consume(d, v, m)
        return None
    if m.action == "start":
        assert m.stage is not None
        return _start(d, v, m.stage)
    if m.action == "resume":
        return _resume(d, v, m)
    return report(v, m)


def _drive(d: Drive) -> dict[str, Any]:
    first = read(d.st, d.table_id)
    # 1 手ごとに行かステップが 1 つ進む（行ごとに始めと終わり、ステップごとに流すと答えの読み取り）。手の数はこの数を超えない
    for _ in range(3 * (len(first.snap.stages) + len(first.snap.steps()))):
        v = read(d.st, d.table_id)
        out = _step(d, v, decide(v))
        if out is not None:
            if d.warnings:
                out["warnings"] = [*d.warnings, *(out["warnings"] if "warnings" in out else [])]
            return out
    raise AssertionError("手の数が行とステップの数を超えた")


def advance(st: store.Store, table_id: str, cwd: Path, env: Env) -> dict[str, Any]:
    """`next`: 排他を取り、終わるか待つか作業を起こすまで手を進める。作業が動いていれば `wait` を返す。"""
    if is_live(st, table_id):
        summary = f"進行表 {table_id} を流す作業が動いている。終わりは wait で待つ"
        return result.result(TOOL, result.OK, summary, metrics={"table_id": table_id}, next=_cmd("wait", table_id))
    with store.run_lock(st, table_id):
        return _drive(Drive(st, table_id, cwd, env, inline=False))


def is_live(st: store.Store, table_id: str) -> bool:
    """同じ進行表を流す作業（か `next`）が動いているか。排他を取れなければ動いている。"""
    try:
        with store.run_lock(st, table_id):
            held = False
    except model.Refused:
        held = True
    return held


def hand(st: store.Store, v: View) -> Optional[dict[str, Any]]:
    """次に打つ 1 手（`show` が返す。プロセスが落ちた後も同じ）。作業が動いていれば `wait`、終わっていれば無し、ほかは `next`。"""
    if model.is_terminal(v.whole):
        return None
    return _cmd("wait" if is_live(st, v.table_id) else "next", v.table_id)


# --- wait ----------------------------------------------------------------------------------


def wait(st: store.Store, table_id: str, timeout: float, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """作業が動いている間だけ待ち、止まったら今の 1 手を返す。上限に当たれば同じ `wait` を打ち直す手を返す。"""
    interval = limits.value(WAIT_POLL, "interval")
    deadline = time.monotonic() + timeout
    while is_live(st, table_id):
        if time.monotonic() >= deadline:
            detail = f"進行表 {table_id} を流す作業がまだ動いている（{timeout:g} 秒待った）"
            return result.stopped(TOOL, "wait_timeout", detail, next=_cmd("wait", table_id))
        sleep(interval)
    v = read(st, table_id)
    return report(v, decide(v))


# --- answer --------------------------------------------------------------------------------


def _notes(value: str, path: Optional[Path]) -> Optional[str]:
    """差し戻しの指摘の本文。差し戻しには空でない指摘ファイルが要る（3365-4）。"""
    if value != "revise":
        return None
    if path is None:
        raise model.Refused("差し戻し（revise）には指摘ファイル（--notes）が要る")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise model.Refused(f"指摘ファイル {path} を読めない: {exc}") from exc
    if not text.strip():
        raise model.Refused(f"指摘ファイル {path} が空")
    return text


def answer(st: store.Store, table_id: str, stage: str, value: str, reason: str, notes: Optional[Path]) -> dict[str, Any]:
    """待っているステップへの答えを事象 `answered` で残す。待っていない行・選択肢に無い答えは断る。"""
    text = _notes(value, notes)

    def build(v: View) -> list[model.EventDraft]:
        m = decide(v)
        if m.action != "gate" or m.stage is None or m.stage.name != stage or m.step is None:
            raise model.Refused(f"工程 {stage} は答えを待っていない")
        allowed = choices(m)
        if value not in allowed:
            raise model.Refused(f"工程 {stage} の答えは {', '.join(allowed)} のどれか（渡された値 {value}）")
        payload = {"answer": value, "step": m.step.id, "kind": invoke.CALLS[m.step.call], "notes": text}
        if notes is not None:
            payload["notes_path"] = str(notes)
        return [model.EventDraft("answered", "conductor", stage, reason=reason, payload=payload, rev=v.rev)]

    seqs = append(st, table_id, build)
    summary = f"工程 {stage} へ答え {value} を書いた。続きは next で流す"
    return result.result(TOOL, result.OK, summary, metrics={"table_id": table_id, "seqs": seqs}, next=_cmd("next", table_id))


# --- work（next が起こす作業） ----------------------------------------------------------------


def retry_delay(cause: Optional[dict[str, Any]], used: int) -> Optional[float]:
    """打ち直しで直る止まりの次の待ちの秒。打ち直さない（行き先が retry でない・回数が尽きた）なら None。"""
    kind = causes.kind_of(cause)
    if cause is None or kind is None or kind not in causes.table() or causes.row(kind).route != "retry":
        return None
    pol = causes.policy_of(kind)
    if pol is None or pol.attempts is None or used >= pol.attempts:
        return None
    if causes.row(kind).requires_retry_after:
        after = cause.get("retry_after")
        return float(after) if isinstance(after, (int, float)) else None
    base = pol.interval if pol.interval is not None else 0.0
    delay = base * ((pol.factor if pol.factor is not None else 1.0) ** used)
    return min(delay, pol.max_interval) if pol.max_interval is not None else delay


def _retrying(call: Callable[[], invoke.StageResult], sleep: Callable[[float], None]) -> invoke.StageResult:
    used = 0
    res = call()
    while res.status == result.STOPPED and (delay := retry_delay(res.cause, used)) is not None:
        sleep(delay)
        used += 1
        res = call()
    return res


def _failed(s: stages.StageDef, step: Optional[str], rev: int, kind: str, detail: str) -> model.EventDraft:
    return model.EventDraft("failed", ACTOR, s.name, payload={"cause": causes.cause(kind, detail), "step": step}, rev=rev)


def _step_draft(v: View, s: stages.StageDef, step: stages.StepDef, prog: StepProgress, res: invoke.StageResult) -> model.EventDraft:
    """ステップの結果を事象にする。終われば出力の規約で照らし、承認ゲートで止まれば待ちを残し、止まれば失敗にする。"""
    if res.status == result.STOPPED:
        cause = res.cause if res.cause is not None else causes.cause("unknown", "作業が理由なく止まった")
        return model.EventDraft("failed", ACTOR, s.name, payload={"cause": cause, "step": step.id}, rev=v.rev)
    errs = definition.validate_io(step.output_ref, res.report) if step.output_ref else []
    if errs:
        return _failed(s, step.id, v.rev, "bad_report", f"ステップ {step.id} の出力が規約に合わない: {'; '.join(errs)}")
    status = "finished" if res.status == result.OK else "waiting"
    payload = {"step": step.id, "status": status, "report": res.report, "generation": prog.generation, "warnings": list(res.warnings)}
    return model.EventDraft(STEP_EVENT, ACTOR, s.name, payload=payload, rev=v.rev)


def _run_step(d: Drive, v: View, s: stages.StageDef, step: stages.StepDef) -> model.EventDraft:
    """ステップを 1 つ呼ぶ。打ち直しで直る止まりは方針どおりに打ち直す（SF8）。"""
    prog = step_progress(v, s.name)
    try:
        inputs = invoke.resolve_inputs(s, v.context())
    except invoke.StageInputMissing as exc:
        return _failed(s, step.id, v.rev, "input_missing", str(exc))
    except invoke.StageInputInvalid as exc:
        return _failed(s, step.id, v.rev, "invalid", str(exc))
    run = replace(invoke.stage_run(s, step, inputs), notes=prog.notes)
    waited = prog.waiting is not None and prog.waiting["step"] == step.id
    ans = prog.answer_value() if waited else None
    work_dir = work_root(d.st.root, v.table_id) / s.id / f"{step.id}-g{prog.generation}"
    res = _retrying(lambda: d.env.invoker(run, d.cwd, work_dir, ans), d.env.sleep)
    return _step_draft(v, s, step, prog, res)


def _stage_output(s: stages.StageDef, prog: StepProgress) -> tuple[dict[str, Any], dict[str, Any]]:
    """工程の出力（ステップの結果の並び・要約・宣言した鍵の出力）と、ステップが出した出力のすべて。"""
    reports = [prog.done[st.id] for st in s.steps]
    merged: dict[str, Any] = {}
    for rep in reports:
        if isinstance(rep.get("outputs"), dict):
            merged.update(rep["outputs"])
    out: dict[str, Any] = {"steps": reports, "summary": f"{s.name} の {len(reports)} ステップが終わった"}
    kept = {k: val for k, val in merged.items() if k in s.outputs}
    if kept:
        out["outputs"] = kept
    return out, merged


def _finish_stage(d: Drive, v: View, s: stages.StageDef) -> None:
    """全ステップが終わった工程の出力を工程の出力の規約で照らし、合えば出力を文脈へ書いて終了の印を付ける。"""
    out, merged = _stage_output(s, step_progress(v, s.name))
    errs = definition.validate_stage_output(s, out)
    if errs:
        d.write(_failed(s, None, v.rev, "bad_report", f"工程 {s.name} の出力が規約に合わない: {'; '.join(errs)}"))
        return
    draft, warnings = invoke.record_outputs(s, v.rev, merged)
    done = model.EventDraft("finished", ACTOR, s.name, reason=out["summary"], payload={"steps": len(out["steps"])}, rev=v.rev)
    d.write(*([draft] if draft is not None else []), done)
    d.warnings.extend(warnings)


def _work_lock(stack: ExitStack, st: store.Store, table_id: str, sleep: Callable[[float], None]) -> bool:
    """作業の排他を取る。起こした `next` が放すまで待ち、待ちの上限までに取れなければ偽（ほかの作業が流している）。"""
    interval = limits.value(LOCK_POLL, "interval")
    for _ in range(max(1, int(limits.value(WAIT_POLL, "interval") / interval))):
        try:
            stack.enter_context(store.run_lock(st, table_id))
        except model.Refused:
            sleep(interval)
        else:
            return True
    return False


def work(st: store.Store, table_id: str, cwd: Path, env: Env) -> dict[str, Any]:
    """工程を順に流す。答えを待つステップ・失敗・進行表の終わりで抜ける。"""
    with ExitStack() as stack:
        if not _work_lock(stack, st, table_id, env.sleep):
            return result.result(TOOL, result.OK, f"進行表 {table_id} はほかの作業が流している", metrics={"table_id": table_id})
        return _drive(Drive(st, table_id, cwd, env, inline=True))
    raise AssertionError("到達しない")


# --- 副命令 ndf progress のエントリポイント ----------------------------------------------------------------


WORKTREE_FILE = identity.FIXED_WORDS["declaration_dir"] + "/worktree.json"
DOC_FILES = ("**/*.md",)  # `.ndf/project.json` の `docs.files` を省いたときの文書のファイル
BEHAVIOR_DOCS = ("**/skills/**/*.md", "**/agents/*.md")  # 同じく `docs.behavior`（振る舞いを決める文書）


def _declaration(cwd: Path, name: str) -> dict[str, Any]:
    """リポジトリの根の宣言のファイル。無いか、オブジェクトでなければ空。"""
    top = Path(Git(cwd).run("rev-parse", "--show-toplevel").strip())
    path = top / name
    raw = command._read_json(path, "を読めない") if path.exists() else {}
    return raw if isinstance(raw, dict) else {}


def _dig(node: Any, *keys: str) -> Any:
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return node


@dataclass(frozen=True)
class DiffKinds:
    """差分の分け方。`docs_only` は文書が 1 件以上あり、コードと振る舞いを決める文書が 0 件のときだけ真。"""

    code: bool
    docs_only: bool
    behavior: bool


def _patterns(project: Mapping[str, Any], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = _dig(project, "docs", key)
    return tuple(value) if isinstance(value, list) and value and all(isinstance(p, str) for p in value) else default


def diff_kinds(files: list[str], project: Mapping[str, Any]) -> DiffKinds:
    """差分のファイルを、文書（`docs.files`）・振る舞いを決める文書（`docs.behavior`）・コードに分ける。"""
    code = [f for f in files if not paths.path_matches(f, _patterns(project, "files", DOC_FILES))]
    behavior = [f for f in files if f not in code and paths.path_matches(f, _patterns(project, "behavior", BEHAVIOR_DOCS))]
    return DiffKinds(bool(code), bool(files) and not code and not behavior, bool(behavior))


def sprint_diff(context: Mapping[str, Any], cwd: Path) -> list[str]:
    """スプリントブランチとベースブランチ（`.ndf/worktree.json` の `base_branch`）の分岐点からの差分のファイル。

    ベースブランチは手元の名前、無ければ `origin/` の名前で引く。求められなければ `FactUnknown`。
    """
    base = _dig(_declaration(cwd, WORKTREE_FILE), "base_branch")
    if not isinstance(base, str) or not base:
        raise FactUnknown(f"差分を求められない: {WORKTREE_FILE} に base_branch が無い")
    git = Git(Path(context.get("worktree") or cwd))
    head = str(context.get("branch") or "HEAD")
    errors = []
    for ref in (base, f"origin/{base}"):
        try:
            return git.changed_files(git.merge_base(ref, head), head)
        except GitError as exc:
            errors.append(str(exc))
    raise FactUnknown(f"差分を求められない: {errors[-1]}")


def facts_of(words: tuple[str, ...], context: Mapping[str, Any], cwd: Path) -> dict[str, bool]:
    """工程の定義の条件（`when`）の語のうち、進行表の外から求める語の値。求められない語は `FactUnknown`。"""
    kinds = functools.cache(lambda: diff_kinds(sprint_diff(context, cwd), _declaration(cwd, command.PROJECT_FILE)))
    known: dict[str, Callable[[], bool]] = {
        "code_in_diff": lambda: kinds().code,
        "docs_only_diff": lambda: kinds().docs_only,
        "behavior_docs_in_diff": lambda: kinds().behavior,
        "ci_declared": lambda: _dig(_declaration(cwd, command.PROJECT_FILE), "test", "ci", "only") is True,
        "prod_branch": lambda: bool(_dig(_declaration(cwd, WORKTREE_FILE), "production_branch")),
        "release_record": lambda: "version" in context,
        "design_pr": lambda: "design_pr" in context,
    }
    unknown = [w for w in words if w not in known]
    if unknown:
        raise FactUnknown(f"条件 {unknown[0]} を求める手段が無い")
    out = {}
    for w in words:
        try:
            out[w] = known[w]()
        except FactUnknown as exc:
            raise FactUnknown(f"条件 {w} を求められない（{exc}）") from exc
    return out


def _env() -> Env:
    return Env(facts=facts_of)


def _cmd_next(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    return advance(st, a.table_id, cwd, _env())


def _cmd_wait(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    return wait(st, a.table_id, a.timeout)


def _cmd_answer(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    return answer(st, a.table_id, a.stage, a.answer, a.reason, Path(a.notes) if a.notes else None)


def _cmd_resync(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    return resync(st, a.table_id, cwd, _env())


def _cmd_work(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    return work(st, a.table_id, cwd, _env())


def _cmd_show(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    """進行表の表示に、次に打つ 1 手（`next`）を足す。プロセスが落ちた後もこの 1 手で続けられる（3366-1）。"""
    out = command.COMMANDS["show"](a, cwd, st)
    held = hand(st, read(st, a.table_id))
    if held is not None:
        out["next"] = held
    return out


# 副命令の名前 → 受ける関数（名前は関数の `_cmd_<副命令>` から引く）
COMMANDS: dict[str, command.Command] = {
    fn.__name__.removeprefix("_cmd_"): fn for fn in (_cmd_next, _cmd_wait, _cmd_answer, _cmd_resync, _cmd_work, _cmd_show)
}


def parser() -> argparse.ArgumentParser:
    """工程を流す副命令（next・wait・answer・resync・work）と、次の 1 手を足した show の引数。"""
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL}", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)
    for name, text in (
        ("next", "次の工程を流す"),
        ("resync", "進捗記録を揃え直す"),
        ("work", "工程を流す作業（next が起こす）"),
        ("show", "進行表を表示する"),
    ):
        sub.add_parser(name, help=text).add_argument("table_id")
    w = sub.add_parser("wait", help="工程を流す作業の終わりを待つ")
    w.add_argument("table_id")
    w.add_argument("--timeout", type=float, default=WAIT_DEFAULT_SECONDS)
    ans = sub.add_parser("answer", help="答えを待つ工程へ答えを書く")
    ans.add_argument("table_id")
    ans.add_argument("stage", help="工程の名前（工程表の行名）")
    ans.add_argument("--answer", required=True, choices=facts.ids(result.ANSWERS))
    ans.add_argument("--reason", required=True)
    ans.add_argument("--notes", help="差し戻し（revise）の指摘ファイル")
    return ap


def _dispatch(a: argparse.Namespace, cwd: Path) -> dict[str, Any]:
    root = command._root(cwd)
    url = store.default_url(root)
    store.sqlite_path(url)  # 受けない URL の形は、置き場を用意する前に断る
    ready = durable.prepare_store(root, datetime.now(UTC))
    with store.open_store(url, ready.root) as st:
        out = COMMANDS[a.action](a, cwd, st)
        if st.warnings:
            out["warnings"] = [*(out["warnings"] if "warnings" in out else []), *st.warnings]
        return out


def main(argv: list[str]) -> int:
    """副命令 `ndf progress`。工程を流す副命令はここで受け、ほか（new・disable など）は `command` へ渡す。"""
    if not argv or argv[0] not in COMMANDS:
        return command.main(argv)
    try:
        a = parser().parse_args(argv)
    except SystemExit as exc:
        return causes.exit_code("invalid") if exc.code else 0
    try:
        out = _dispatch(a, Path.cwd())
    except command.STOPPABLE as exc:
        out = command._stopped_from(exc)
    return result.emit(out)
