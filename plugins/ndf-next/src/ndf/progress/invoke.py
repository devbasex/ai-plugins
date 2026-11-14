"""工程から作業を呼ぶ層（#1142 の設計の「工程から作業を呼ぶ層」）。

工程の定義のステップ 1 つを、呼び出しの種類（`call`）ごとの作業として呼び、作業の結果 JSON を読む。

| `call` | 種類 | 呼ぶもの |
| --- | --- | --- |
| `ndf.plan` | `plan` | プランの実行（`ndf plan next <雛形>`）。雛形は `contracts/templates/<with.plan>.json` |
| `ndf.skill` | `skill` | LLM の起動（`claude -p`。Skill `<前置き><with.skill>` を回す） |
| `ndf.script` | `script` | 子プロセス（`with.argv`）。入力は環境変数 `NDF_STAGE_INPUT` の JSON で渡す |
| `ndf.listen` | `listen` | 作業を呼ばない。承認ゲートの答えを待つ（`ndf.progress.run` が受ける） |

作業へ渡す引数は工程の定義の `metadata.ndf.inputs` の鍵だけで、値は渡す前にスプリントの文脈の
スキーマ（`contracts/workflow/context.schema.json`）で形を確かめる（3364-7）。作業の出力のうち工程の
定義の `outputs` に無い鍵は書かずに捨て、結果の `warnings` に載せる。プランの実行は進行表を読まず、書かない。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ndf import contracts, identity
from ndf.adapters import claude
from ndf.contracts import limits, result
from ndf.progress import definition, model, stages

# 工程の定義の call → 作業種別
CALLS = {"ndf.plan": "plan", "ndf.skill": "skill", "ndf.script": "script", "ndf.listen": "listen"}
LISTEN = "listen"
# プランの雛形の置き場（契約の置き場の下）
TEMPLATES = "templates"
# script の作業へ入力を渡す環境変数
INPUT_ENV = "NDF_STAGE_INPUT"
NOTES_ENV = "NDF_STAGE_NOTES"
# skill の作業の上限（Skill 1 本を回す起動は実装の工程と同じ桁）
SKILL_LIMIT = "monitor.phase.implement"
SKILL_SYSTEM = "工程の作業を 1 つ行い、最後に結果 JSON（統一 JSON レポート）だけを答える。"
# 打ち直しても直らない `claude -p` の失敗 → 止まった理由（表に無い失敗は前提の欠け）
SKILL_FAILURES = {claude.Failure.USAGE_LIMIT: "usage_limit", claude.Failure.TIMEOUT: "interrupted"}
OUTPUTS_EVENT = stages.OUTPUTS_EVENT
ACTOR = "workflow"
# このプラグインの Python のパッケージの名前（`python -m` で打つ名前）
PACKAGE = __package__.partition(".")[0]
# このパッケージの置き場（`src`。子プロセスの import の道）
SRC = Path(__file__).resolve().parents[2]


class StageInputMissing(Exception):
    """工程の入力の鍵がスプリントの文脈に無い。`keys` は足りない鍵。"""

    def __init__(self, keys: Sequence[str]) -> None:
        super().__init__(f"工程の入力の鍵が文脈に無い: {', '.join(keys)}")
        self.keys = tuple(keys)


class StageInputInvalid(Exception):
    """工程の入力の値が文脈のスキーマの形に合わない。`errors` は誤りの一覧。"""

    def __init__(self, errors: Sequence[str]) -> None:
        super().__init__(f"工程の入力の形が合わない: {'; '.join(errors)}")
        self.errors = tuple(errors)


@dataclass(frozen=True)
class StageRun:
    """ステップ 1 つの呼び出し。`name` は呼ぶプラン・Skill の名前（script は argv の先頭）。"""

    kind: str
    name: str
    inputs: dict[str, Any]
    outputs: tuple[str, ...]
    range: str
    step: str
    args: dict[str, Any] = field(default_factory=dict)
    # 差し戻しの指摘の本文（承認ゲートの revise の答え）。作業へは環境変数 NDF_STAGE_NOTES か問いの末尾で渡す
    notes: Optional[str] = None

    def env(self, extra: Mapping[str, str]) -> dict[str, str]:
        out = ndf_env({**os.environ, **extra})
        if self.notes is not None:
            out[NOTES_ENV] = self.notes
        return out


@dataclass(frozen=True)
class StageResult:
    """作業の結果。`report` は結果 JSON、`outputs` は工程の定義が書くと宣言した鍵だけの値。"""

    report: dict[str, Any]
    outputs: dict[str, Any]
    warnings: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        return str(self.report["status"])

    @property
    def cause(self) -> Optional[dict[str, Any]]:
        held = self.report["cause"] if "cause" in self.report else None
        return held if isinstance(held, dict) else None


def ndf_command(*args: str) -> list[str]:
    """このプラグインの副命令を子プロセスで打つ argv（同じ Python で `-m ndf`。作業ディレクトリを import の道に入れない）。"""
    return [sys.executable, "-P", "-m", PACKAGE, *args]


def ndf_env(env: Mapping[str, str]) -> dict[str, str]:
    """子プロセスの環境変数。このパッケージの置き場（`src`）を `PYTHONPATH` の頭へ足す。"""
    out = dict(env)
    held = out["PYTHONPATH"] if "PYTHONPATH" in out else ""
    out["PYTHONPATH"] = os.pathsep.join(p for p in (str(SRC), held) if p)
    return out


# 外部のコマンドを打つ関数（argv・作業ディレクトリ・環境変数 → 終了した子プロセス）。テストが差し替える
Runner = Callable[[list[str], Path, dict[str, str]], "subprocess.CompletedProcess[str]"]


def run_command(argv: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False)


# --- 引数と値の形 --------------------------------------------------------------------------


def value_errors(values: Mapping[str, Any]) -> list[str]:
    """文脈の鍵と値の組を、スプリントの文脈のスキーマで確かめた誤り（3364-7）。"""
    return [f"文脈の値: {e}" for e in contracts.schema_errors(dict(values), definition.CONTEXT)]


def resolve_inputs(stage: stages.StageDef, context: Mapping[str, Any]) -> dict[str, Any]:
    """工程の定義の `inputs` の鍵だけを文脈から取り、形を確かめて返す（余分な鍵は渡さない）。"""
    missing = [k for k in stage.inputs if k not in context]
    if missing:
        raise StageInputMissing(missing)
    picked = {k: context[k] for k in stage.inputs}
    errs = value_errors(picked)
    if errs:
        raise StageInputInvalid(errs)
    return picked


def stage_run(stage: stages.StageDef, step: stages.StepDef, inputs: Mapping[str, Any]) -> StageRun:
    """工程とステップの定義から呼び出しを組む。知らない `call` は `model.Refused`。"""
    if step.call not in CALLS:
        raise model.Refused(f"工程 {stage.name} のステップ {step.id} の call {step.call} は呼べない")
    kind = CALLS[step.call]
    name = _named(kind, step.args)
    return StageRun(kind, name, dict(inputs), stage.outputs, step.range, step.id, dict(step.args))


def _named(kind: str, args: Mapping[str, Any]) -> str:
    key = {"plan": "plan", "skill": "skill", "script": "argv", LISTEN: None}[kind]
    if key is None:
        return LISTEN
    value = args[key] if key in args else None
    if kind == "script":
        value = value[0] if isinstance(value, list) and value else None
    if not isinstance(value, str) or not value:
        raise model.Refused(f"作業種別 {kind} の with.{key} が無い")
    return value


# --- 作業の結果 ----------------------------------------------------------------------------


def read_report(stdout: str) -> Optional[dict[str, Any]]:
    """標準出力の最後の空でない行の結果 JSON。読めないか形が違えば None。"""
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return None
    try:
        obj = json.loads(lines[-1])
    except json.JSONDecodeError:  # noqa: NDF401 外部コマンド: 結果 JSON を返さない作業は None で bad_report にする
        return None
    return obj if not result.errors(obj) else None


def bad_report(run: StageRun, detail: str) -> StageResult:
    out = result.stopped(_tool(run), "bad_report", f"作業 {run.kind} {run.name} が結果 JSON を返さない: {detail}")
    return StageResult(out, {})


def _tool(run: StageRun) -> str:
    return __package__.rpartition(".")[2] if run.kind == LISTEN else run.kind


def finished(run: StageRun, report: dict[str, Any]) -> StageResult:
    """結果 JSON から、宣言した鍵だけの出力を取る。宣言に無い鍵は捨てて `warnings` に載せる。"""
    given = report["outputs"] if isinstance(report.get("outputs"), dict) else {}
    kept, dropped = stages.keep_declared(run.outputs, given)
    warnings = tuple(f"工程の定義の outputs に無い鍵を捨てた: {k}" for k in dropped)
    errs = value_errors(kept)
    if errs:
        return bad_report(run, "; ".join(errs))
    return StageResult(report, kept, warnings)


# --- 種類ごとの呼び出し ---------------------------------------------------------------------


def template_path(name: str) -> Path:
    return contracts.contracts_dir() / TEMPLATES / f"{name}.json"


def _set_value(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def plan_argv(run: StageRun, work_dir: Path, answer: Optional[str]) -> list[str]:
    """`ndf plan next <雛形> --state-dir <置き場> --set <鍵>=<値>...`（答えがあれば `--answer`）。"""
    argv = ndf_command("plan", "next", str(template_path(run.name)), "--state-dir", str(work_dir))
    for key, value in run.inputs.items():
        argv += ["--set", f"{key}={_set_value(value)}"]
    return argv + (["--answer", answer] if answer else [])


def _from_process(run: StageRun, done: subprocess.CompletedProcess[str]) -> StageResult:
    report = read_report(done.stdout)
    if report is None:
        return bad_report(run, f"終了コード {done.returncode}")
    return finished(run, report)


def _call_plan(run: StageRun, cwd: Path, work_dir: Path, answer: Optional[str], runner: Runner) -> StageResult:
    if not template_path(run.name).is_file():
        return StageResult(result.stopped("plan", "precondition", f"プランの雛形 {run.name} が無い（{template_path(run.name)}）"), {})
    work_dir.mkdir(parents=True, exist_ok=True)
    return _from_process(run, runner(plan_argv(run, work_dir, answer), cwd, run.env({})))


def _call_script(run: StageRun, cwd: Path, runner: Runner) -> StageResult:
    env = run.env({INPUT_ENV: json.dumps(run.inputs, ensure_ascii=False)})
    return _from_process(run, runner([str(a) for a in run.args["argv"]], cwd, env))


def skill_prompt(run: StageRun) -> str:
    """Skill を回す問い。入力の値は文脈の形を通した JSON だけを渡す。"""
    prompt = f"/{identity.load().prefix}{run.name}\n\n入力（JSON）: {json.dumps(run.inputs, ensure_ascii=False)}"
    return prompt if run.notes is None else f"{prompt}\n\n差し戻しの指摘:\n{run.notes}"


def _skill_cause(failure: Optional[claude.Failure]) -> str:
    """`claude -p` が答えを返せなかった理由 → 止まった理由。理由の無い失敗は打ち直せば直りうる。"""
    if failure is None:
        return "transient"
    return SKILL_FAILURES[failure] if failure in SKILL_FAILURES else "precondition"


def _call_skill(run: StageRun, cwd: Path) -> StageResult:
    schema = contracts.load_json(contracts.schema_path("result"))
    call = claude.ClaudeCall(system=SKILL_SYSTEM, permission=claude.Permission.FULL, json_schema=schema)
    reply = claude.call_claude(claude.claude_args(call), skill_prompt(run), limits.seconds(SKILL_LIMIT), cwd)
    if not reply.ok:
        kind = _skill_cause(reply.failure)
        return StageResult(result.stopped("skill", kind, f"Skill {run.name} を回せない: {reply.error}"), {})
    if not isinstance(reply.answer, dict) or result.errors(reply.answer):
        return bad_report(run, "答えが結果 JSON の形に合わない")
    return finished(run, reply.answer)


def invoke(run: StageRun, cwd: Path, work_dir: Path, answer: Optional[str] = None, runner: Runner = run_command) -> StageResult:
    """作業を種類ごとに呼ぶ。`work_dir` はプランの実行の置き場、`answer` はプランの承認ゲートの答え。"""
    if run.kind == "plan":
        return _call_plan(run, cwd, work_dir, answer, runner)
    if run.kind == "script":
        return _call_script(run, cwd, runner)
    if run.kind == "skill":
        return _call_skill(run, cwd)
    raise model.Refused(f"作業種別 {run.kind} は呼ばない（答えを待つステップ）")


def record_outputs(stage: stages.StageDef, rev: int, outputs: Mapping[str, Any]) -> tuple[Optional[model.EventDraft], list[str]]:
    """工程の出力を文脈へ書く事象 `outputs_recorded` の下書き（書き手は作業 `workflow`）。形は `stages.outputs_draft`。"""
    return stages.outputs_draft(stage, rev, outputs, ACTOR)
