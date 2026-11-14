"""工程から作業を呼ぶ層（#1142 の設計の「工程から作業を呼ぶ層」）。

工程の定義のステップ 1 つを、呼び出しの種類（`call`）ごとの作業として呼び、作業の結果 JSON を読む。

| `call` | 種類 | 呼ぶもの |
| --- | --- | --- |
| `ndf.plan` | `plan` | プランの実行（`ndf plan next <プラン>`）。プランは雛形 `<with.plan>`（`ndf.core.route.templates()` の鍵）を埋めたもの |
| `ndf.skill` | `skill` | LLM の起動（`claude -p`。Skill `<前置き><with.skill>` を回す） |
| `ndf.script` | `script` | 子プロセス（`with.argv`）。入力は環境変数 `NDF_STAGE_INPUT` の JSON で渡す |
| `ndf.listen`（工程の定義の `listen` の要素） | `listen` | 作業を呼ばない。承認ゲートの答えを待つ（`ndf.progress.run` が受ける） |

作業へ渡す引数は工程の定義の `metadata.ndf.inputs` の鍵だけで、値は渡す前にスプリントの文脈の
スキーマ（`contracts/workflow/context.schema.json`）で形を確かめる（3364-7）。作業の出力のうち工程の
定義の `outputs` に無い鍵は書かずに捨て、結果の `warnings` に載せる。プランの実行は進行表を読まず、書かない。

雛形は `ndf.core.route` から引き、雛形の差し込み（`{{名前}}`）を入力の値と `ndf route render` と同じ省いた値の
決め方（`route.derived`）で埋めたプランを実行の置き場へ書いて流す。プランへの作業場所（文脈の `worktree`）は
差し込みの値（`--set`）でなく、埋めたプランの作業場所（プランの `worktree`）で渡す。差し込みの `worktree` はプランの
実行が決まった名前として持ち、上書きを断るためである。止まったプランを打ち直すときは、前の回の結果の打ち直し（`next` の `--from`）と
同じく止まったステップから新しい回を始め、差し込み `progress_attempt` に打ち直しの数を載せて回の鍵を変える。同じ引数で
打ち直すと、プランは前の回の控えの結果を返すだけだからである。skill と script は入力の作業場所で打つ。

工程の入力に無い差し込みは、値の性質ごとの出所（`FILL_SOURCES`）から埋める（#3092）。課題の題と本文は課題から写し、
テストのコマンド・必須のチェック・レビューと組み直しの起動は `.ndf/project.json` の宣言から読み、worker と承認ゲートの
起動は `params.json` の既定を使う。どこからも決まらない差し込みは、名前と出所を挙げて invalid で止める。
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
from ndf.adapters import claude, git, github
from ndf.adapters.git import GitError
from ndf.contracts import facts, limits, result
from ndf.core import durable, route
from ndf.core.plan import cli as plan_cli
from ndf.core.plan import files as plan_files
from ndf.core.plan import runner as plan_runner
from ndf.progress import definition, model, stages
from ndf.progress.command import PROJECT_FILE, declaration
from ndf.testing import weight

# 作業種別と、工程の定義の call → 作業種別（plan は副命令 `ndf plan` の名前）
PLAN = plan_cli.TOOL
SKILL = "skill"
SCRIPT = "script"
LISTEN = "listen"
CALLS = {"ndf.plan": PLAN, "ndf.skill": SKILL, "ndf.script": SCRIPT, definition.LISTEN_CALL: LISTEN}
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
ACTOR = model.WORKFLOW_ACTOR
# 承認の答えの語（表 answers の行）。プランの承認ゲートの続きが無い承認は、プランが終わったとみなす
APPROVED = str(facts.row(result.ANSWERS, "approved")["id"])
# プランの実行の置き場に残す、回の打ち直しの数（`plan_attempt`）と、それを渡す差し込みの名前
ATTEMPT_FILE = "progress-attempt.json"
ATTEMPT_SET = "progress_attempt"
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


class TemplateValuesMissing(route.RouteError):
    """雛形の差し込みの値がどの出所からも決まらない。`missing` は足りない差し込みの名前 → 出所。"""

    def __init__(self, template_name: str, missing: Mapping[str, str]) -> None:
        listed = ", ".join(f"{n}（{s}）" for n, s in missing.items())
        super().__init__(f"雛形 {template_name} の差し込みの値が無い: {listed}")
        self.missing = dict(missing)

    @property
    def items(self) -> list[dict[str, str]]:
        return [{"key": n, "source": s} for n, s in self.missing.items()]


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
    # 行の打ち直し（conductor の retry）の回数と、方針による自動の打ち直しか。プランはどちらでも新しい回を始める（`plan_attempt`）
    retries: int = 0
    again: bool = False

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


# 雛形の差し込みのうち、工程の入力に無い値の出所（差し込みの名前 → 出所。#3092）。表に無い名前は工程の入力・
# 打った場所（`route.derived`）・`params.json` の既定で決まる
FROM_ISSUE = "課題の題と本文"
# 書き手を信用しない（`GitHub.trusts` が偽。#3364）課題の題と本文は、作業の入力（指示として読まれる）へ写さない
FROM_UNTRUSTED_ISSUE = "課題の題と本文（書き手に write 以上の権限が無く、指示として渡さない）"
FROM_PROJECT = f"{PROJECT_FILE} の宣言"
FROM_DEFAULT = "params.json の既定"
FROM_WORKTREE = "作業場所のブランチ"
FROM_INPUT = "工程の入力か打った場所"
FILL_SOURCES = {
    "title": FROM_ISSUE,
    "body_file": FROM_ISSUE,
    "test_command": FROM_PROJECT,
    "required": FROM_PROJECT,
    "review": FROM_PROJECT,
    "rebuild": FROM_PROJECT,
    "worker": FROM_DEFAULT,
    "gate": FROM_DEFAULT,
    "branch": FROM_WORKTREE,
}
# `.ndf/project.json` の宣言のうち、差し込みの値を持つ鍵の道（test_command は `test.suites` のテストの suite の command を宣言の順に ` && ` でつなぐ）
PROJECT_KEYS = {
    "required": ("ci", "required_checks"),
    "review": ("workflow", "launch", "review"),
    "rebuild": ("workflow", "launch", "rebuild"),
}
# 課題の本文を書き出す置き場のファイルと、題の写し（プランの実行の置き場の下）
ISSUE_BODY_FILE = "issue-{number}-body.md"
ISSUE_TITLE_FILE = "issue-{number}-title.json"


def issue_text(place: Path, number: int) -> Optional[tuple[str, str]]:
    """課題の題と本文（作業場所のリモートの GitHub から読む）。テストが差し替える。

    書き手を信用しない（`GitHub.trusts` が偽。#3364）なら None。本文は作業の入力として指示に読まれるためである。
    """
    hub = github.GitHub(git.Git(place).remote_repo(), github.client())
    title, authored = hub.issue_text(number)
    return (title, authored.body) if hub.trusts(authored) else None


def _strings(value: Any) -> Optional[list[str]]:
    """空でない文字列の並びならその並び、ほかは None。"""
    ok = isinstance(value, list) and value and all(isinstance(v, str) and v for v in value)
    return list(value) if ok else None


def _dig(node: Any, *keys: str) -> Any:
    for key in keys:
        node = node[key] if isinstance(node, dict) and key in node else None
    return node


def _project_value(name: str, project: Mapping[str, Any]) -> Optional[route.Value]:
    """宣言から読む差し込みの値。宣言に無いか形が違えば None。"""
    if name == "test_command":
        suites = _dig(project, "test", "suites")
        found = [s for s in suites if isinstance(s, dict) and weight.is_test_suite(s)] if isinstance(suites, list) else []
        cmds = [c for c in (s.get("command") for s in found) if isinstance(c, str) and c]
        return " && ".join(cmds) if cmds else None
    return _strings(_dig(project, *PROJECT_KEYS[name]))


def _project(place: Path) -> dict[str, Any]:
    """作業場所のリポジトリの `.ndf/project.json`。git の根が決まらなければ空。読めなければ `route.RouteError`。"""
    try:
        return declaration(place, PROJECT_FILE)
    except GitError:
        return {}
    except contracts.ContractError as exc:
        raise route.RouteError(str(exc)) from None


def _issue_values(run: StageRun, work_dir: Path, place: Path) -> Optional[dict[str, route.Value]]:
    """課題が 1 つなら、その題（title）と本文を書いたファイル（body_file）。課題が 1 つでなければ空。

    題と本文は置き場へ初回に 1 度だけ写し（`ISSUE_BODY_FILE`・`ISSUE_TITLE_FILE`）、以後の呼び出し（承認の答え・続き・
    打ち直し）はその写しを使う。読み直すと GitHub を毎回呼び、途中で題が直されるとプランが変わって、終わっていない回へ
    届かず開始点から流れ直すためである（PR 3469 の指摘）。
    書き手を信用しない課題は、本文を書き出さずに None（足りない値の出所を `FROM_UNTRUSTED_ISSUE` にする）。
    読めない失敗（`GitError`・`github.GitHubError`）は捕まえずに `_call_plan` へ上げる。空にすると、打ち直せば通る
    失敗が打ち直せない「値が無い」（invalid）に変わり、本当の理由も消えるためである。
    """
    issues = run.inputs["issues"] if "issues" in run.inputs else None
    if not isinstance(issues, list) or len(issues) != 1:
        return {}
    number = int(issues[0])
    out = work_dir / ISSUE_BODY_FILE.format(number=number)
    copy = work_dir / ISSUE_TITLE_FILE.format(number=number)
    held = plan_files.read_json(copy)
    if held is not None and isinstance(held.get("title"), str) and out.is_file():
        return {"title": held["title"], "body_file": str(out)}
    found = issue_text(place, number)
    if found is None:
        return None
    title, body = found
    out.write_text(body, encoding="utf-8")
    plan_files.write_json(copy, {"title": title})
    return {"title": title, "body_file": str(out)}


def _branch_of(run: StageRun) -> Optional[str]:
    """入力の作業場所が今いるブランチ。作業場所が入力に無いか、ブランチに居なければ None。"""
    held = run.inputs["worktree"] if "worktree" in run.inputs else None
    if not isinstance(held, str) or not Path(held).is_dir():
        return None
    try:
        branch = git.Git(Path(held)).run("symbolic-ref", "--short", "HEAD").strip()
    except GitError:  # noqa: NDF401 外部コマンド: ブランチに居ない作業場所は、足りない値として名前を挙げる
        return None
    return branch if branch else None


def template_values(t: route.Template, run: StageRun, work_dir: Path, cwd: Path) -> dict[str, route.Value]:
    """雛形の差し込みの値。工程の入力 → 出所の表（`FILL_SOURCES`）→ 打った場所（`route.derived`）の順に決める。

    既定も無く決まらない差し込みは、名前と出所を挙げて `TemplateValuesMissing`。
    """
    values = {k: _param_value(v) for k, v in run.inputs.items() if k in t.params}
    wanted = {n: FILL_SOURCES[n] for n in t.params if n not in values and n in FILL_SOURCES}
    place = work_place(run, cwd)
    sources = dict(FILL_SOURCES)
    if FROM_ISSUE in wanted.values():
        issued = _issue_values(run, work_dir, place)
        if issued is None:
            sources.update({n: FROM_UNTRUSTED_ISSUE for n, s in wanted.items() if s == FROM_ISSUE})
        else:
            values.update({k: v for k, v in issued.items() if k in wanted})
    if FROM_PROJECT in wanted.values():
        project = _project(place)
        for name in (n for n, s in wanted.items() if s == FROM_PROJECT):
            found = _project_value(name, project)
            if found is not None:
                values[name] = found
    if "branch" in wanted:
        branch = _branch_of(run)
        if branch is not None:
            values["branch"] = branch
    values = route.derived(t, values, cwd)
    defs = route.params()
    missing = {n: (sources[n] if n in sources else FROM_INPUT) for n in t.params if n not in values and defs[n].default is None}
    if missing:
        raise TemplateValuesMissing(t.name, missing)
    return values


def plan_path(run: StageRun, work_dir: Path, cwd: Path) -> Path:
    """雛形を入力の値で埋めたプランを実行の置き場へ書き、そのパスを返す。

    差し込みのうち入力に無いものは出所の表（`FILL_SOURCES`）と、`ndf route render` と同じく打った場所から決め
    （`template_values`）、それでも欠ければ `TemplateValuesMissing`（`route.RouteError`）。雛形が無ければ `model.Refused`。
    """
    t = template(run.name)
    if t is None:
        raise model.Refused(f"プランの雛形 {run.name} が無い")
    plan = route.render(t, template_values(t, run, work_dir, cwd))
    out = work_dir / f"{run.name}.json"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def _previous(work_dir: Path) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """置き場の前の回の記録（`run.json`）と、その回の結果の控え（終わっていなければ None）。"""
    record = plan_files.read_json(work_dir / plan_runner.RUN_FILE)
    if record is None:
        return None, None
    return record, plan_files.read_json(plan_runner.result_path(work_dir, durable.run_name(record["key"])))


def _from_of(command: Any) -> Optional[str]:
    """結果のコマンド（`next`・`routes` の値）の `--from` の値。無ければ None。"""
    argv = command["argv"] if isinstance(command, dict) and isinstance(command.get("argv"), list) else []
    return str(argv[argv.index("--from") + 1]) if "--from" in argv[:-1] else None


def plan_start(work_dir: Path) -> Optional[str]:
    """プランを打ち直す開始点。前の回が止まっていれば、その結果の打ち直し（`next`）の `--from`（止まったステップ）。

    止まっていなければ前の回の開始点（承認の答えを渡すときも同じ回に届く）。前の回が無ければ None（初めから）。
    """
    record, done = _previous(work_dir)
    if record is None:
        return None
    again = _from_of(done["next"]) if done is not None and done["status"] == result.STOPPED and "next" in done else None
    if again is not None:
        return again
    start = record["start"] if "start" in record else None
    return start if isinstance(start, str) else None


def stored_gate_routes(work_dir: Path) -> Optional[dict[str, Any]]:
    """前の回が承認ゲートで終わっていれば、その結果の routes（答え → 続きのコマンドか None）。ほかは None。

    終わった回へ `--answer` を渡しても、プランは控えの結果を返すだけである。続きは routes の開始点から新しい回で流す。
    """
    _, done = _previous(work_dir)
    if done is None or done["status"] != result.GATE or not isinstance(done.get("routes"), dict):
        return None
    return dict(done["routes"])


def plan_attempt(run: StageRun, work_dir: Path) -> int:
    """プランの回の打ち直しの数。打ち直し（`retries` が増えたか `again`）で前の回が終わっていれば 1 つ上げて置き場に残す。

    差し込み `progress_attempt` に載せて回の鍵を変える。開始点と head が前の回と同じでも、プランが終わった回の控えの結果を
    返さず、新しい回を流すためである。打ち直さない呼び出し（承認の答え・作業が落ちた後の続き）は残した数をそのまま使い、同じ回に届く。
    """
    path = work_dir / ATTEMPT_FILE
    held = plan_files.read_json(path)
    attempt = int(held["attempt"]) if held is not None else 0
    seen = int(held["retries"]) if held is not None else 0
    _, done = _previous(work_dir)
    if (run.again or run.retries > seen) and done is not None:
        attempt += 1
    if held is None or attempt != held["attempt"] or run.retries > seen:
        plan_files.write_json(path, {"attempt": attempt, "retries": max(seen, run.retries)})
    return attempt


def _set_value(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def plan_argv(run: StageRun, work_dir: Path, answer: Optional[str], cwd: Path) -> list[str]:
    """`ndf plan next <プラン> [--from <ステップ>] --state-dir <置き場> --set <鍵>=<値>...`（答えがあれば `--answer`）。

    作業場所（`worktree`）は `--set` に載せず、埋めたプランの作業場所で渡す（`plan_path`）。前の回が承認ゲートで終わって
    いれば、答えの routes の開始点から `--answer` 無しで新しい回を流す（`stored_gate_routes`）。
    """
    routes = stored_gate_routes(work_dir) if answer else None
    if routes is not None:
        start, answer = _from_of(routes[answer] if answer in routes else None), None
    else:
        start = plan_start(work_dir)
    attempt = plan_attempt(run, work_dir)
    argv = ndf_command(
        PLAN, "next", str(plan_path(run, work_dir, cwd)), *(["--from", start] if start else []), "--state-dir", str(work_dir)
    )
    for key, value in run.inputs.items():
        if key != "worktree":
            argv += ["--set", f"{key}={_set_value(value)}"]
    if attempt:
        argv += ["--set", f"{ATTEMPT_SET}={attempt}"]
    return argv + (["--answer", answer] if answer else [])


def _no_route(run: StageRun, work_dir: Path, answer: Optional[str]) -> Optional[StageResult]:
    """承認ゲートで終わった回への答えに続きが無いときの結果。承認ならプランは終わり、ほかは承認されなかった。続きがあれば None。"""
    routes = stored_gate_routes(work_dir) if answer else None
    if routes is None or _from_of(routes[answer] if answer in routes else None) is not None:
        return None
    if answer == APPROVED:
        _, done = _previous(work_dir)
        assert done is not None
        outputs = done["outputs"] if isinstance(done.get("outputs"), dict) else {}
        return finished(run, result.result(PLAN, result.OK, f"プラン {run.name} の最後の承認ゲートが承認された", outputs=outputs))
    return StageResult(result.stopped(PLAN, "refused", f"プラン {run.name} の承認ゲートで承認されなかった（答え {answer}）"), {})


def same_run(before: StageResult, after: StageResult) -> bool:
    """2 つの結果が同じプランの回の結果か（打ち直しても回が変わらず、プランが控えの結果を返した）。"""
    run_id = before.report["run_id"] if "run_id" in before.report else None
    return isinstance(run_id, str) and "run_id" in after.report and after.report["run_id"] == run_id


def _from_process(run: StageRun, done: subprocess.CompletedProcess[str]) -> StageResult:
    report = read_report(done.stdout)
    if report is None:
        return bad_report(run, f"終了コード {done.returncode}")
    return finished(run, report)


def _github_kind(exc: github.GitHubError) -> str:
    """GitHub の失敗の止まった理由。打ち直せば通りうるもの（不達・5xx・上限）は transient、ほかは precondition。

    分け方は課題の参照の読み手（`ndf.core.refs` の `_unreadable`）と同じ。状態が 0 でも届いて断られた失敗（GraphQL の誤り・
    認証が無い）は precondition。
    """
    passing = exc.unreached or exc.status >= 500 or exc.retry_after is not None or exc.status == 429
    return "transient" if passing else "precondition"


def _call_plan(run: StageRun, cwd: Path, work_dir: Path, answer: Optional[str], runner: Runner) -> StageResult:
    try:
        if template(run.name) is None:
            return StageResult(result.stopped(PLAN, "precondition", f"プランの雛形 {run.name} が無い"), {})
        work_dir.mkdir(parents=True, exist_ok=True)
        ended = _no_route(run, work_dir, answer)
        if ended is not None:
            return ended
        argv = plan_argv(run, work_dir, answer, cwd)
    except TemplateValuesMissing as exc:
        return StageResult(result.stopped(PLAN, "invalid", f"プランの雛形 {run.name} を埋められない: {exc}", items=exc.items), {})
    except route.RouteError as exc:
        return StageResult(result.stopped(PLAN, "invalid", f"プランの雛形 {run.name} を埋められない: {exc}"), {})
    except GitError as exc:
        return StageResult(result.stopped(PLAN, "precondition", f"プランの雛形 {run.name} の省いた値を決められない: {exc}"), {})
    except github.GitHubError as exc:
        detail = f"プランの雛形 {run.name} の差し込みを GitHub から読めない: {exc}"
        return StageResult(result.stopped(PLAN, _github_kind(exc), detail, retry_after=exc.retry_after), {})
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

    `runner` は plan・script の子プロセスを、`call_claude` は skill の `claude -p` を打つ。skill と script は、入力に
    作業場所（`worktree`）があればそこで打つ（プランは埋めたプランの作業場所で受ける）。
    """
    if run.kind == PLAN:
        return _call_plan(run, cwd, work_dir, answer, runner)
    if run.kind not in (SCRIPT, SKILL):
        raise model.Refused(f"作業種別 {run.kind} は呼ばない（答えを待つステップ）")
    place = work_place(run, cwd)
    if not place.is_dir():
        return StageResult(result.stopped(run.kind, "precondition", f"作業場所 {place} が無い"), {})
    if run.kind == SCRIPT:
        return _call_script(run, place, runner)
    return _call_skill(run, place, call_claude)


def work_place(run: StageRun, cwd: Path) -> Path:
    """skill・script を打つ作業ディレクトリ。入力の作業場所（`worktree`）があればそこ、無ければ打った場所。"""
    held = run.inputs["worktree"] if "worktree" in run.inputs else None
    return Path(held) if isinstance(held, str) and held else cwd
