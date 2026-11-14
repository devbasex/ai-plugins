"""工程から作業を呼ぶ層（#1142 の設計の「工程から作業を呼ぶ層」）。

工程の定義のステップ 1 つを、呼び出しの種類（`call`）ごとの作業として呼び、作業の結果 JSON を読む。

| `call` | 種類 | 呼ぶもの |
| --- | --- | --- |
| `ndf.plan` | `plan` | プランの実行（`ndf plan next <プラン>`）。プランは雛形 `<with.plan>`（`ndf.core.route.templates()` の鍵）を埋めたもの |
| `ndf.skill` | `skill` | LLM の起動（`claude -p`。Skill `<前置き><with.skill>` を回す） |
| `ndf.script` | `script` | 子プロセス（`with.argv`）。入力は環境変数 `NDF_STAGE_INPUT` の JSON で渡す |
| `ndf.listen` | `listen` | 作業を呼ばない。承認ゲートの答えを待つ（`ndf.progress.run` が受ける） |

作業へ渡す引数は工程の定義の `metadata.ndf.inputs` の鍵だけで、値は渡す前にスプリントの文脈の
スキーマ（`contracts/workflow/context.schema.json`）で形を確かめる（3364-7）。作業の出力のうち工程の
定義の `outputs` に無い鍵は書かずに捨て、結果の `warnings` に載せる。プランの実行は進行表を読まず、書かない。

雛形は `ndf.core.route` から引き、雛形の差し込み（`{{名前}}`）を入力の値と `ndf route render` と同じ省いた値の
決め方（`route.derived`）で埋めたプランを実行の置き場へ書いて流す。プランへの作業場所（文脈の `worktree`）は
差し込みの値（`--set`）でなく、埋めたプランの作業場所（プランの `worktree`）で渡す。差し込みの `worktree` はプランの
実行が決まった名前として持ち、上書きを断るためである。止まったプランを打ち直すときは、前の回の結果の打ち直し（`next` の `--from`）と
同じく止まったステップから新しい回を始める。同じ引数で打ち直すと、プランは前の回の控えの結果を返すだけだからである。
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
from ndf.adapters.git import GitError
from ndf.contracts import limits, result
from ndf.core import durable, route
from ndf.core.plan import cli as plan_cli
from ndf.core.plan import files as plan_files
from ndf.core.plan import runner as plan_runner
from ndf.progress import definition, model, stages

# 作業種別と、工程の定義の call → 作業種別（plan は副命令 `ndf plan` の名前）
PLAN = plan_cli.TOOL
SKILL = "skill"
SCRIPT = "script"
LISTEN = "listen"
CALLS = {"ndf.plan": PLAN, "ndf.skill": SKILL, "ndf.script": SCRIPT, "ndf.listen": LISTEN}
# 作業種別 → 呼ぶものの名前を持つ with の鍵（listen は作業を呼ばないので無い）
NAME_ARG = {PLAN: "plan", SKILL: "skill", SCRIPT: "argv"}
# script の作業へ入力を渡す環境変数
INPUT_ENV = "NDF_STAGE_INPUT"
NOTES_ENV = "NDF_STAGE_NOTES"
# skill の作業の上限（Skill 1 本を回す起動は実装の工程と同じ桁）
SKILL_LIMIT = "monitor.phase.implement"
SKILL_SYSTEM = "工程の作業を 1 つ行い、最後に結果 JSON（統一 JSON レポート）だけを答える。"
# 打ち直しても直らない `claude -p` の失敗 → 止まった理由（表に無い失敗は前提の欠け）
SKILL_FAILURES = {claude.Failure.USAGE_LIMIT: "usage_limit", claude.Failure.TIMEOUT: "interrupted"}
ACTOR = "workflow"
# このプラグインの Python のパッケージの名前（`python -m` で打つ名前）
PACKAGE = __package__.partition(".")[0]
# このパッケージの置き場（`src`。子プロセスの import の道）
SRC = Path(__file__).resolve().parents[2]


class StageInputError(Exception):
    """工程の入力を文脈から渡せない。`kind` は止まった理由の語、`items` は結果 JSON の項目。"""

    kind = "invalid"

    @property
    def items(self) -> list[dict[str, str]]:
        return []


class StageInputMissing(StageInputError):
    """工程の入力の鍵がスプリントの文脈に無い。`keys` は足りない鍵。"""

    kind = "input_missing"

    def __init__(self, keys: Sequence[str]) -> None:
        super().__init__(f"工程の入力の鍵が文脈に無い: {', '.join(keys)}")
        self.keys = tuple(keys)

    @property
    def items(self) -> list[dict[str, str]]:
        return [{"key": k} for k in self.keys]


class StageInputInvalid(StageInputError):
    """工程の入力の値が文脈のスキーマの形に合わない。`errors` は誤りの一覧。"""

    kind = "invalid"

    def __init__(self, errors: Sequence[str]) -> None:
        super().__init__(f"工程の入力の形が合わない: {'; '.join(errors)}")
        self.errors = tuple(errors)

    @property
    def items(self) -> list[dict[str, str]]:
        return [{"error": e} for e in self.errors]


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


# Skill を回す `claude -p` を打つ関数（argv・問い・上限の秒・作業ディレクトリ → 答え）。テストが差し替える
SkillCaller = Callable[[Sequence[str], str, float, Optional[Path]], claude.ClaudeReply]


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


def kind_of(step: stages.StepDef) -> str:
    """ステップの作業種別（`call` から引く）。"""
    return CALLS[step.call]


def is_listen(step: stages.StepDef) -> bool:
    """作業を呼ばず、承認ゲートの答えを待つステップか。"""
    return kind_of(step) == LISTEN


def stage_run(stage: stages.StageDef, step: stages.StepDef, inputs: Mapping[str, Any]) -> StageRun:
    """工程とステップの定義から呼び出しを組む。知らない `call` は `model.Refused`。"""
    if step.call not in CALLS:
        raise model.Refused(f"工程 {stage.name} のステップ {step.id} の call {step.call} は呼べない")
    kind = kind_of(step)
    name = _named(kind, step.args)
    return StageRun(kind, name, dict(inputs), stage.outputs, step.range, step.id, dict(step.args))


def _named(kind: str, args: Mapping[str, Any]) -> str:
    if kind == LISTEN:
        return LISTEN
    key = NAME_ARG[kind]
    value = args[key] if key in args else None
    if kind == SCRIPT:
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


def template(name: str) -> Optional[route.Template]:
    """プランの雛形（`route.templates()` の鍵 `name`）。無ければ None。"""
    found = route.templates()
    return found[name] if name in found else None


def _param_value(value: Any) -> route.Value:
    """入力の値を雛形の差し込みの値（文字列か文字列の並び）にする。"""
    if isinstance(value, list):
        return [_set_value(v) for v in value]
    return _set_value(value)


def plan_path(run: StageRun, work_dir: Path, cwd: Path) -> Path:
    """雛形を入力の値で埋めたプランを実行の置き場へ書き、そのパスを返す。

    差し込みのうち入力に無いものは `ndf route render` と同じく打った場所から決め（`route.derived`）、それでも欠ければ
    `route.RouteError`。雛形が無ければ `model.Refused`。
    """
    t = template(run.name)
    if t is None:
        raise model.Refused(f"プランの雛形 {run.name} が無い")
    given = {k: _param_value(v) for k, v in run.inputs.items() if k in t.params}
    plan = route.render(t, route.derived(t, given, cwd))
    out = work_dir / f"{run.name}.json"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def plan_start(work_dir: Path) -> Optional[str]:
    """プランを打ち直す開始点。前の回が止まっていれば、その結果の打ち直し（`next`）の `--from`（止まったステップ）。

    止まっていなければ前の回の開始点（承認の答えを渡すときも同じ回に届く）。前の回が無ければ None（初めから）。
    """
    record = plan_files.read_json(work_dir / plan_runner.RUN_FILE)
    if record is None:
        return None
    done = plan_files.read_json(plan_runner.result_path(work_dir, durable.run_name(record["key"])))
    again = done["next"] if done is not None and done["status"] == result.STOPPED and isinstance(done.get("next"), dict) else None
    argv = again["argv"] if again is not None else []
    if "--from" in argv[:-1]:
        return str(argv[argv.index("--from") + 1])
    start = record["start"] if "start" in record else None
    return start if isinstance(start, str) else None


def _set_value(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def plan_argv(run: StageRun, work_dir: Path, answer: Optional[str], cwd: Path) -> list[str]:
    """`ndf plan next <プラン> [--from <ステップ>] --state-dir <置き場> --set <鍵>=<値>...`（答えがあれば `--answer`）。

    作業場所（`worktree`）は `--set` に載せず、埋めたプランの作業場所で渡す（`plan_path`）。
    """
    start = plan_start(work_dir)
    argv = ndf_command(
        PLAN, "next", str(plan_path(run, work_dir, cwd)), *(["--from", start] if start else []), "--state-dir", str(work_dir)
    )
    for key, value in run.inputs.items():
        if key != "worktree":
            argv += ["--set", f"{key}={_set_value(value)}"]
    return argv + (["--answer", answer] if answer else [])


def same_run(before: StageResult, after: StageResult) -> bool:
    """2 つの結果が同じプランの回の結果か（打ち直しても回が変わらず、プランが控えの結果を返した）。"""
    run_id = before.report["run_id"] if "run_id" in before.report else None
    return isinstance(run_id, str) and "run_id" in after.report and after.report["run_id"] == run_id


def _from_process(run: StageRun, done: subprocess.CompletedProcess[str]) -> StageResult:
    report = read_report(done.stdout)
    if report is None:
        return bad_report(run, f"終了コード {done.returncode}")
    return finished(run, report)


def _call_plan(run: StageRun, cwd: Path, work_dir: Path, answer: Optional[str], runner: Runner) -> StageResult:
    try:
        if template(run.name) is None:
            return StageResult(result.stopped(PLAN, "precondition", f"プランの雛形 {run.name} が無い"), {})
        work_dir.mkdir(parents=True, exist_ok=True)
        argv = plan_argv(run, work_dir, answer, cwd)
    except route.RouteError as exc:
        return StageResult(result.stopped(PLAN, "invalid", f"プランの雛形 {run.name} を埋められない: {exc}"), {})
    except GitError as exc:
        return StageResult(result.stopped(PLAN, "precondition", f"プランの雛形 {run.name} の省いた値を決められない: {exc}"), {})
    return _from_process(run, runner(argv, cwd, run.env({})))


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


def _call_skill(run: StageRun, cwd: Path, call_claude: SkillCaller) -> StageResult:
    schema = contracts.load_json(contracts.schema_path("result"))
    call = claude.ClaudeCall(system=SKILL_SYSTEM, permission=claude.Permission.FULL, json_schema=schema)
    reply = call_claude(claude.claude_args(call), skill_prompt(run), limits.seconds(SKILL_LIMIT), cwd)
    if not reply.ok:
        kind = _skill_cause(reply.failure)
        return StageResult(result.stopped(SKILL, kind, f"Skill {run.name} を回せない: {reply.error}"), {})
    if not isinstance(reply.answer, dict) or result.errors(reply.answer):
        return bad_report(run, "答えが結果 JSON の形に合わない")
    return finished(run, reply.answer)


def invoke(
    run: StageRun,
    cwd: Path,
    work_dir: Path,
    answer: Optional[str] = None,
    runner: Runner = run_command,
    *,
    call_claude: SkillCaller = claude.call_claude,
) -> StageResult:
    """作業を種類ごとに呼ぶ。`work_dir` はプランの実行の置き場、`answer` はプランの承認ゲートの答え。

    `runner` は plan・script の子プロセスを、`call_claude` は skill の `claude -p` を打つ。
    """
    if run.kind == PLAN:
        return _call_plan(run, cwd, work_dir, answer, runner)
    if run.kind == SCRIPT:
        return _call_script(run, cwd, runner)
    if run.kind == SKILL:
        return _call_skill(run, cwd, call_claude)
    raise model.Refused(f"作業種別 {run.kind} は呼ばない（答えを待つステップ）")
