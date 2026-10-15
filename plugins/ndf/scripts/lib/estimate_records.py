"""見積もりの記録と実績の記録（#1856）。置き場の解決・行の形の検査・追記と読み出し・課題ごとの実績の合計・取り込み。

置き場は `NDF_ESTIMATE_DIR` → `.ndf/estimate.json` の `records_dir` → `<git の共通ディレクトリ>/ndf/estimate` →
`$XDG_STATE_HOME/ndf/estimate/<リポジトリの絶対パスの sha256 の先頭 12 文字>`。git の共通ディレクトリは
`.git` を読んで決め、`git` を起動しない（部品は subprocess を使わない。設計の非機能の条件）。

どちらの記録も追記だけで、既存の行を書き換えない（I3・I4）。実績の記録は 4 種類の行（`run_start`・`step`・
`run_end`・`human`）を `kind` で分ける。終わりの行の無い実行は、実行中と取り込んだ実行を除いて「中断」と読む。
行の形は設計の「データ構造」が正本である。使う側は `deps.require("schema")` を先に呼ぶ。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

import schema

SCHEMA = 1
ACTUALS = "actuals.jsonl"
ESTIMATES = "estimates.jsonl"
DECL = Path(".ndf") / "estimate.json"
# 見積もりの完了条件。supervise のプランの `フェーズ` と同じ語で、この順に進む
PHASES = ("設計", "実装", "検査", "配布（開発版）", "配布（本番）")
OUTCOMES = ("成功", "承認ゲート", "失敗", "取り消し")
ITEM_KEYS = ("requirements", "information", "spread", "verification", "environment", "rework")
ITEM_VALUES = ("軽", "重", "判定できない")
CONFIG_KEYS = ("models", "harness", "review", "test", "parallel")
MARKS = ("実績", "未校正", "材料なし")


class DeclError(ValueError):
    """`.ndf/estimate.json` が読めない・形が違う。"""


# --- 行の形 ---


class RunStart(schema.Shape):
    run: str
    plan: str
    issues: list[int]
    phase: str | None = None
    from_: str | None = None
    attempt: int
    imported: bool


class StepRow(schema.Shape):
    run: str
    step: str
    type: str
    exit: int | None = None
    seconds: float | None = None
    cost_usd: float | None = None
    runtime: str | None = None
    models: list[str] | None = None
    retry: int = 0


class Config(schema.Shape):
    models: list[str] | None = None
    harness: str | None = None
    review: str | None = None
    test: str | None = None
    parallel: int | None = None


class RunEnd(schema.Shape):
    run: str
    outcome: Literal["成功", "承認ゲート", "失敗", "取り消し"]
    wall_seconds: float
    config: Config


class Human(schema.Shape):
    issues: list[int]
    seconds: float
    what: str | None = None


class Basis(schema.Shape):
    issue: int | None = None
    run: str | None = None
    value: float | None = None
    shared: bool | None = None


class Value(schema.Shape):
    mark: Literal["実績", "未校正", "材料なし"]
    kind: Literal["P50/P80", "通常/追加の調査"] | None = None
    typical: float | None = None
    high: float | None = None
    basis: list[Basis] = []
    note: str | None = None


class Values(schema.Shape):
    A: Value
    H: Value
    L: Value
    C: Value


class Items(schema.Shape):
    requirements: Literal["軽", "重", "判定できない"] = "判定できない"
    information: Literal["軽", "重", "判定できない"] = "判定できない"
    spread: Literal["軽", "重", "判定できない"] = "判定できない"
    verification: Literal["軽", "重", "判定できない"] = "判定できない"
    environment: Literal["軽", "重", "判定できない"] = "判定できない"
    rework: Literal["軽", "重", "判定できない"] = "判定できない"


class Estimate(schema.Shape):
    id: str
    issue: int | None = None
    task: str | None = None
    done: Literal["設計", "実装", "検査", "配布（開発版）", "配布（本番）"]
    items: Items
    config: Config
    values: Values
    size: int | str | None = None


_ACTUAL_SHAPES = {"run_start": RunStart, "step": StepRow, "run_end": RunEnd, "human": Human}
_RENAMED = {"from": "from_"}


def _check(model, row: dict, where: str) -> None:
    data = {_RENAMED.get(k, k): v for k, v in row.items() if k not in ("schema", "kind", "at")}
    schema.load_shape(model, data, where=where)


def check_actual(row: dict) -> None:
    """実績の記録の 1 行の形を検査する。違えば `schema.ShapeError`。"""
    kind = row.get("kind")
    if kind not in _ACTUAL_SHAPES:
        raise schema.ShapeError(f"actuals.kind: 次のどれかにする: {', '.join(_ACTUAL_SHAPES)}", [("kind", "知らない種類")])
    _check(_ACTUAL_SHAPES[kind], row, f"actuals[{kind}]")


def check_estimate(row: dict) -> None:
    """見積もりの記録の 1 行の形を検査する（A・H・L・C を足した欄は知らない項目として落ちる。I1）。"""
    _check(Estimate, row, "estimates")


# --- 置き場 ---


def find_git_dir(start: Path) -> tuple[Path, Path] | None:
    """(リポジトリの根, git の共通ディレクトリ)。`.git` を上へ探して読む。git でなければ None。"""
    start = Path(start).resolve()
    for d in (start, *start.parents):
        dot = d / ".git"
        if dot.is_dir():
            return d, _common_of(dot)
        if dot.is_file():
            try:
                text = dot.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if not text.startswith("gitdir:"):
                return None
            gitdir = Path(text[len("gitdir:") :].strip())
            gitdir = gitdir if gitdir.is_absolute() else (d / gitdir)
            return d, _common_of(gitdir.resolve())
    return None


def _common_of(gitdir: Path) -> Path:
    f = gitdir / "commondir"
    if f.is_file():
        try:
            rel = f.read_text(encoding="utf-8").strip()
        except OSError:
            return gitdir
        p = Path(rel)
        return (p if p.is_absolute() else gitdir / p).resolve()
    return gitdir


def repo_root(start: Path | str | None = None) -> Path:
    """リポジトリの根（git でなければ start そのもの）。"""
    start = Path(start or Path.cwd()).resolve()
    got = find_git_dir(start)
    return got[0] if got else start


def load_decl(root: Path | str | None = None) -> dict:
    """`.ndf/estimate.json`（任意）。無ければ {}。知らない鍵・形の違う値は DeclError。"""
    path = repo_root(root) / DECL
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise DeclError(f"{DECL} を読めない: {e}") from e
    try:
        return schema.dump_shape(schema.load_shape(Decl, data, where=str(DECL)), drop_none=True)
    except schema.ShapeError as e:
        raise DeclError(str(e)) from e


class Decl(schema.Shape):
    window: int | None = None
    min_samples: int | None = None
    size_unit_seconds: float | None = None
    scale: list[int] | None = None
    records_dir: str | None = None


def records_dir(root: Path | str | None = None) -> Path:
    """記録の置き場（上の解決の順）。"""
    if os.environ.get("NDF_ESTIMATE_DIR"):
        return Path(os.environ["NDF_ESTIMATE_DIR"])
    start = Path(root or Path.cwd()).resolve()
    got = find_git_dir(start)
    top = got[0] if got else start
    configured = load_decl(top).get("records_dir")
    if configured:
        p = Path(configured)
        return p if p.is_absolute() else top / p
    if got:
        return got[1] / "ndf" / "estimate"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "ndf" / "estimate" / hashlib.sha256(str(top).encode()).hexdigest()[:12]


# --- 追記と読み出し ---


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _append(path: Path, row: dict) -> str | None:
    data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    except OSError as e:
        return f"{path} を書けない: {e}"
    return None


def append_actual(directory: Path | str, row: dict) -> str | None:
    """実績の記録へ 1 行足す。形が違えば ShapeError、書けなければ誤りの文（書けたら None）。"""
    row = {"schema": SCHEMA, "at": row.get("at") or now_iso(), **{k: v for k, v in row.items() if k not in ("schema", "at")}}
    check_actual(row)
    return _append(Path(directory) / ACTUALS, row)


def append_estimate(directory: Path | str, row: dict) -> str | None:
    """見積もりの記録へ 1 行足す。形が違えば ShapeError、書けなければ誤りの文。"""
    row = {"schema": SCHEMA, "at": row.get("at") or now_iso(), **{k: v for k, v in row.items() if k not in ("schema", "at")}}
    check_estimate(row)
    return _append(Path(directory) / ESTIMATES, row)


def _read(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for raw in text.splitlines():
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("schema") is not None and d.get("kind" if path.name == ACTUALS else "id"):
            out.append(d)
    return out


def read_actuals(directory: Path | str) -> list[dict]:
    """実績の記録の行（`schema` の無い行・壊れた行は読み飛ばす）。無ければ []。"""
    return _read(Path(directory) / ACTUALS)


def read_estimates(directory: Path | str) -> list[dict]:
    """見積もりの記録の行。無ければ []。"""
    return _read(Path(directory) / ESTIMATES)


def human_row(issues: list[int], seconds: float, what: str | None) -> dict:
    return {"kind": "human", "issues": [int(i) for i in issues], "seconds": float(seconds), "what": what}


def run_id(plan: str, started: str) -> str:
    return f"{hashlib.sha256(str(plan).encode()).hexdigest()[:12]}-{started}"


# --- 実行の表と課題ごとの合計 ---


def _ts(at: str | None) -> float | None:
    try:
        return datetime.fromisoformat(str(at)).timestamp()
    except (TypeError, ValueError):
        return None


def plan_alive(run: "Run") -> bool:
    """プランの状態ディレクトリの `step.pid` の子のどれかが生きていれば真（実行中）。"""
    plan = Path(run.plan or "")
    pid_file = plan.with_name(plan.stem + "-state") / "step.pid"
    try:
        data = json.loads(pid_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    for c in (data.get("children") or []) if isinstance(data, dict) else []:
        pid = c.get("pid") if isinstance(c, dict) else None
        if isinstance(pid, int) and pid > 0:
            try:
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                continue
            except PermissionError:
                return True
    return False


@dataclass
class Run:
    run: str
    plan: str
    issues: list[int]
    phase: str | None
    started: str
    retry: bool
    imported: bool
    steps: list[dict] = field(default_factory=list)
    end: dict | None = None
    outcome: str | None = None  # 成功・承認ゲート・失敗・取り消し・中断・実行中。取り込んだ実行は None

    @property
    def config(self) -> dict:
        return (self.end or {}).get("config") or {}


def run_table(rows: list[dict], alive: Callable[[Run], bool] | None = None) -> dict[str, Run]:
    """実績の記録の行から実行ごとの表を組む。終わりの行が無い実行は、取り込んだ実行（None）・実行中・中断に分ける（I3）。"""
    alive = alive or plan_alive
    table: dict[str, Run] = {}
    for r in rows:
        kind, rid = r.get("kind"), r.get("run")
        if kind == "run_start" and rid and rid not in table:
            table[rid] = Run(
                rid,
                str(r.get("plan") or ""),
                [int(i) for i in r.get("issues") or [] if isinstance(i, int)],
                r.get("phase") or None,
                str(r.get("at") or ""),
                bool(r.get("from")),
                bool(r.get("imported")),
            )
        elif kind == "step" and rid in table:
            table[rid].steps.append(r)
        elif kind == "run_end" and rid in table:
            table[rid].end = r
    for run in table.values():
        if run.end:
            run.outcome = run.end.get("outcome")
        elif run.imported:
            run.outcome = None
        else:
            run.outcome = "実行中" if alive(run) else "中断"
    return table


def _phase_index(phase: str | None) -> int | None:
    return PHASES.index(phase) if phase in PHASES else None


@dataclass
class IssueActual:
    issue: int
    a: float = 0.0
    c: float = 0.0
    h: float | None = None
    l: float | None = None
    completed: bool = False
    cost_unknown: bool = False
    shared: bool = False
    config: dict = field(default_factory=dict)
    finished: str | None = None
    runs: list[Run] = field(default_factory=list)


def issue_actuals(
    rows: list[dict], done: str, since: str | None = None, alive: Callable[[Run], bool] | None = None
) -> dict[int, IssueActual]:
    """課題ごとの実績の合計（設計の「課題ごとの実績の合計」）。`since` があれば、その時刻より後に始まった実行だけを足す。

    A は完了条件のフェーズまでの実行（失敗・中断・取り消し・再試行を含む）の `step` の秒の和で、2 つ以上の課題を
    指す実行は課題の数で等分する。L は最初の実行の始まりから完了条件のフェーズの最初の `成功` の終わりまで。
    C は同じ範囲の USD の和。H は `human` の行の秒の和（無ければ None）。"""
    limit = _phase_index(done)
    since_ts = _ts(since) if since else None
    table = run_table(rows, alive)
    out: dict[int, IssueActual] = {}
    for run in table.values():
        if since_ts is not None and (_ts(run.started) or 0) <= since_ts:
            continue
        idx = _phase_index(run.phase)
        if limit is not None and idx is not None and idx > limit:
            continue
        for issue in run.issues:
            out.setdefault(issue, IssueActual(issue)).runs.append(run)
    for issue, acc in out.items():
        acc.runs.sort(key=lambda r: _ts(r.started) or 0)
        ends = [r for r in acc.runs if r.phase == done and r.outcome == "成功"]
        end = ends[0] if ends else None
        cut = _ts(end.end.get("at")) if end else None
        if end:
            acc.completed, acc.config, acc.finished = True, end.config, end.end.get("at")
            acc.runs = [r for r in acc.runs if (_ts(r.started) or 0) <= (cut or 0)]
        for run in acc.runs:
            share = len(run.issues) or 1
            acc.shared |= share > 1
            for s in run.steps:
                if isinstance(s.get("seconds"), (int, float)):
                    acc.a += float(s["seconds"]) / share
                if isinstance(s.get("cost_usd"), (int, float)):
                    acc.c += float(s["cost_usd"]) / share
                else:
                    acc.cost_unknown = True
        if end and cut is not None and acc.runs:
            first = _ts(acc.runs[0].started)
            acc.l = round(cut - first, 1) if first is not None else None
        acc.a, acc.c = round(acc.a, 1), round(acc.c, 4)
    for r in rows:
        if r.get("kind") != "human" or not isinstance(r.get("seconds"), (int, float)):
            continue
        if since_ts is not None and (_ts(r.get("at")) or 0) <= since_ts:
            continue
        for issue in r.get("issues") or []:
            acc = out.setdefault(issue, IssueActual(issue))
            acc.h = (acc.h or 0.0) + float(r["seconds"])
    return out


# --- 取り込み ---


def _plan_of_state(state: Path) -> tuple[str, dict]:
    """状態ディレクトリ（<名前>-state/）の隣のプラン（<名前>.json）か、中の写し（plan.json）を読む。"""
    for plan in (state.with_name(state.name[: -len("-state")] + ".json"), state / "plan.json"):
        try:
            data = json.loads(plan.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return str(plan), data
    return "", {}


def import_progress(sv_dir: Path | str, directory: Path | str) -> dict:
    """supervise の状態ディレクトリの下の `progress.jsonl` から `run_start`・`step` の行を作る。

    `run_end` は作らず、`run_start` の `imported` を真にする。`run` の識別子で重複を除く（何度流しても同じ）。"""
    existing = {r.get("run") for r in read_actuals(directory) if r.get("kind") == "run_start"}
    added = skipped = 0
    for progress in sorted(Path(sv_dir).rglob("progress.jsonl")):
        state = progress.parent
        if not state.name.endswith("-state"):
            continue
        plan, data = _plan_of_state(state)
        if not plan:
            continue
        try:
            lines = progress.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        issues = [int(i) for i in data.get("課題") or [] if isinstance(i, int) or str(i).isdigit()]
        phase = data.get("フェーズ") or None
        attempt, cur, buf = 0, None, []
        batches: list[tuple[dict, list[dict]]] = []
        for raw in lines:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            if d.get("kind") == "run" and d.get("at"):
                if cur:
                    batches.append((cur, buf))
                attempt += 1
                rid = run_id(plan, d["at"])
                cur = {"kind": "run_start", "at": d["at"], "run": rid, "plan": plan, "issues": issues, "phase": phase,
                       "from": d.get("from"), "attempt": attempt, "imported": True}
                buf = []
            elif d.get("kind") == "step" and cur and d.get("step"):
                buf.append(
                    {"kind": "step", "at": d.get("at"), "run": cur["run"], "step": str(d["step"]), "type": str(d.get("type") or ""),
                     "exit": d.get("exit") if isinstance(d.get("exit"), int) else None,
                     "seconds": d.get("seconds") if isinstance(d.get("seconds"), (int, float)) else None,
                     "cost_usd": d.get("cost") if isinstance(d.get("cost"), (int, float)) and d.get("type") in ("work", "judge") else None,
                     "retry": 0}
                )
        if cur:
            batches.append((cur, buf))
        for start, steps in batches:
            if start["run"] in existing:
                skipped += 1
                continue
            for row in (start, *steps):
                err = append_actual(directory, row)
                if err:
                    raise OSError(err)
            existing.add(start["run"])
            added += 1
    return {"added": added, "skipped": skipped}

