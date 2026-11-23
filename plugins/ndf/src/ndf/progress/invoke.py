"""工程から作業を呼ぶ層（#1142 の設計の「工程から作業を呼ぶ層」）。

工程の定義のステップ 1 つを、呼び出しの種類（`call`）ごとの作業として呼び、作業の結果 JSON を読む。

| `call` | 種類 | 呼ぶもの |
| --- | --- | --- |
| `ndf.plan` | `plan` | プランの実行（`ndf plan next <プラン>`）。プランはテンプレート `<with.plan>`（`ndf.core.route.templates()` のキー）を埋めたもの |
| `ndf.skill` | `skill` | LLM の起動（`claude -p`。Skill `<前置き><with.skill>` を回す） |
| `ndf.script` | `script` | 子プロセス（`with.argv`。先頭がこのプラグインの名前なら同じ Python でサブコマンドを打つ）。入力は環境変数 `NDF_STAGE_INPUT` の JSON で渡す |
| `ndf.listen`（工程の定義の `listen` の要素） | `listen` | 作業を呼ばない。承認ゲートの回答を待つ（`ndf.progress.run` が受ける） |

作業へ渡す引数は工程の定義の `metadata.ndf.inputs` のキーだけで、値は渡す前にスプリントの文脈の
スキーマ（`contracts/workflow/context.schema.json`）で形を確かめる（3364-7）。作業の出力のうち工程の
定義の `outputs` に無いキーは書かずに捨て、結果の `warnings` に載せる。プランの実行はステートマシンを読まず、書かない。

テンプレートは `ndf.core.route` から引き、テンプレートの差し込み（`{{名前}}`）を入力の値と `ndf route render` と同じ省いた値の
決め方（`route.derived`）で埋めたプランを実行の保存先へ書いて流す。プランへの作業場所（文脈の `worktree`）は
差し込みの値（`--set`）でなく、埋めたプランの作業場所（プランの `worktree`）で渡す。差し込みの `worktree` はプランの
実行が決まった名前として持ち、上書きを断るためである。止まったプランを打ち直すときは、前の回の結果の打ち直し（`next` の `--from`）と
同じく止まったステップから新しい回を始め、差し込み `progress_attempt` に打ち直しの数を載せて回のキーを変える。同じ引数で
打ち直すと、プランは前の回のバックアップの結果を返すだけだからである。skill と script は入力の作業場所で打つ。

工程の入力に無い差し込みは、値の性質ごとの出所（`FILL_SOURCES`）から埋める（#3092）。課題の題と本文は課題から写し、
テストのコマンド・必須のチェック・レビューと組み直しとリファクタリングの起動は `.ndf/project.json` の設定から読み（レビューは設定が
無ければ `ndf cross-review run` を、打つ時点のブランチの PR の番号で組む。`route.review_launch`）、worker と承認ゲートの
起動は `params.json` の既定を使う。どこからも決まらない差し込みは、名前と出所を挙げて invalid で止める。
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import contracts, identity
from ndf.adapters import claude, git, github
from ndf.adapters.git import GitError
from ndf.cli import PACKAGE, child_encoding, ndf_command
from ndf.contracts import facts, limits, result
from ndf.core import durable, route
from ndf.core.plan import cli as plan_cli
from ndf.core.plan import files as plan_files
from ndf.core.plan import inputs_decide as plan_inputs
from ndf.core.plan import runner as plan_runner
from ndf.progress import definition, model, stages, worker_kinds
from ndf.progress.command import PROJECT_FILE, FactUnknown, ndf_settings, unreviewed_range
from ndf.testing import scoped, weight

# 作業種別と、工程の定義の call → 作業種別（plan はサブコマンド `ndf plan` の名前）
PLAN = plan_cli.TOOL
SKILL = "skill"
SCRIPT = "script"
LISTEN = "listen"
CALLS = {"ndf.plan": PLAN, "ndf.skill": SKILL, "ndf.script": SCRIPT, definition.LISTEN_CALL: LISTEN}
# 作業種別 → 呼ぶものの名前を持つ with のキー（listen は作業を呼ばないので無い）
NAME_ARG = {PLAN: "plan", SKILL: "skill", SCRIPT: "argv"}
# script の作業へ入力を渡す環境変数
INPUT_ENV = "NDF_STAGE_INPUT"
NOTES_ENV = "NDF_STAGE_NOTES"
# skill の作業の上限（Skill 1 本を回す起動は実装の工程と同じ桁）
SKILL_LIMIT = "monitor.phase.implement"
SKILL_SYSTEM = "工程の作業を 1 つ行い、最後に結果 JSON（統一 JSON レポート）だけを回答する。"
# 打ち直しても直らない `claude -p` の失敗 → 停止理由（表に無い失敗は前提の欠け）
SKILL_FAILURES = {claude.Failure.USAGE_LIMIT: "usage_limit", claude.Failure.TIMEOUT: "interrupted"}
ACTOR = model.WORKFLOW_ACTOR
# 承認の回答の語（表 answers の行）。プランの承認ゲートの続きが無い承認は、プランが終わったとみなす
APPROVED = str(facts.row(result.ANSWERS, "approved")["id"])
# プランの実行の保存先に残す、回の打ち直しの数（`plan_attempt`）と、それを渡す差し込みの名前
ATTEMPT_FILE = "progress-attempt.json"
ATTEMPT_SET = "progress_attempt"
# プランを組んだ差し込みの値を、プランの実行の保存先に残すファイル（#3908 の決定 2）
VALUES_FILE = "values.json"
# values.json のキー → 値の型（形が違えば読まなかったものとして扱う）
VALUES_SHAPE = {"plan": str, "values": dict, "inputs": dict, "local_tests": bool, "at": str}


class StageInputError(Exception):
    """工程の入力を文脈から渡せない。`kind` は停止理由の語、`items` は結果 JSON の項目。"""

    kind = "invalid"

    @property
    def items(self) -> list[dict[str, str]]:
        return []


class StageInputMissing(StageInputError):
    """工程の入力のキーがスプリントの文脈に無い。`keys` は足りないキー。"""

    kind = "input_missing"

    def __init__(self, keys: Sequence[str]) -> None:
        super().__init__(f"工程の入力のキーが文脈に無い: {', '.join(keys)}")
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
    """テンプレートの差し込みの値がどの出所からも決まらない。`missing` は足りない差し込みの名前 → 出所。"""

    def __init__(self, template_name: str, missing: Mapping[str, str]) -> None:
        listed = ", ".join(f"{n}（{s}）" for n, s in missing.items())
        super().__init__(f"テンプレート {template_name} の差し込みの値が無い: {listed}")
        self.missing = dict(missing)

    @property
    def items(self) -> list[dict[str, str]]:
        return [{"key": n, "source": s} for n, s in self.missing.items()]


class KindInputsMissing(route.RouteError):
    """プランの作業（`{{worker}}` の後ろの名前）が読む入力か作業場所が決まらない。`missing` は名前 → 出所。"""

    def __init__(self, template_name: str, missing: Mapping[str, str]) -> None:
        listed = ", ".join(f"{n}（{s}）" for n, s in missing.items())
        super().__init__(f"テンプレート {template_name} の作業の入力が無い: {listed}")
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
    # 変更要求の指摘の本文（承認ゲートの request_changes の回答）。作業へは環境変数 NDF_STAGE_NOTES か質問の末尾で渡す
    notes: Optional[str] = None
    # 行の打ち直し（conductor の retry）の回数と、方針による自動の打ち直しか。プランはどちらでも新しい回を始める（`plan_attempt`）
    retries: int = 0
    again: bool = False

    def env(self, extra: Mapping[str, str]) -> dict[str, str]:
        out = {**os.environ, **extra}
        if self.notes is not None:
            out[NOTES_ENV] = self.notes
        return out


@dataclass(frozen=True)
class StageResult:
    """作業の結果。`report` は結果 JSON、`outputs` は工程の定義が書くと定義したキーだけの値。"""

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


# 外部のコマンドを打つ関数（argv・作業ディレクトリ・環境変数 → 終了した子プロセス）。テストが差し替える
Runner = Callable[[list[str], Path, dict[str, str]], "subprocess.CompletedProcess[str]"]


# Skill を回す `claude -p` を打つ関数（argv・質問・上限の秒・作業ディレクトリ → 回答）。テストが差し替える
SkillCaller = Callable[[Sequence[str], str, float, Optional[Path]], claude.ClaudeReply]


def stage_inputs(environ: Mapping[str, str]) -> Optional[dict[str, Any]]:
    """script の作業が受けた工程の入力（`INPUT_ENV` の JSON object）。無い・読めない・object でなければ None。"""
    raw = environ[INPUT_ENV] if INPUT_ENV in environ else ""
    try:
        inputs = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        inputs = None
    return inputs if isinstance(inputs, dict) else None


def run_command(argv: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """子を打ち、出力を読む。`ndf_command` の子は決めた文字コード（UTF-8）で、ほかのコマンド（script の外部スクリプト）は
    従来どおりロケールの文字コードで読む（#4029）。"""
    encoding = child_encoding(argv)
    decode: dict[str, Any] = {"text": True} if encoding is None else {"encoding": encoding, "errors": "replace"}
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, check=False, **decode)


# --- 引数と値の形 --------------------------------------------------------------------------


def value_errors(values: Mapping[str, Any]) -> list[str]:
    """文脈のキーと値の組を、スプリントの文脈のスキーマで確かめた誤り（3364-7）。"""
    return [f"文脈の値: {e}" for e in contracts.schema_errors(dict(values), definition.CONTEXT)]


def resolve_inputs(stage: stages.StageDef, context: Mapping[str, Any]) -> dict[str, Any]:
    """工程の定義の `inputs` のキーと、`optional_inputs` のうち文脈にあるキーだけを文脈から取り、形を確かめて返す
    （余分なキーは渡さない。任意の入力が文脈に無くても断らない）。"""
    missing = [k for k in stage.inputs if k not in context]
    if missing:
        raise StageInputMissing(missing)
    picked = {k: context[k] for k in (*stage.inputs, *stage.optional_inputs) if k in context}
    errs = value_errors(picked)
    if errs:
        raise StageInputInvalid(errs)
    return picked


def kind_of(step: stages.StepDef) -> str:
    """ステップの作業種別（`call` から引く）。"""
    return CALLS[step.call]


def is_listen(step: stages.StepDef) -> bool:
    """作業を呼ばず、承認ゲートの回答を待つステップか。"""
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
    """結果 JSON から、定義したキーだけの出力を取る。設定に無いキーは捨てて `warnings` に載せる。"""
    given = report["outputs"] if isinstance(report.get("outputs"), dict) else {}
    kept, dropped = stages.keep_declared(run.outputs, given)
    warnings = tuple(f"工程の定義の outputs に無いキーを捨てた: {k}" for k in dropped)
    errs = value_errors(kept)
    if errs:
        return bad_report(run, "; ".join(errs))
    return StageResult(report, kept, warnings)


# --- 種類ごとの呼び出し ---------------------------------------------------------------------


def template(name: str) -> Optional[route.Template]:
    """プランのテンプレート（`route.templates()` のキー `name`）。無ければ None。"""
    found = route.templates()
    return found[name] if name in found else None


def _param_value(value: Any) -> route.Value:
    """入力の値をテンプレートの差し込みの値（文字列か文字列の並び）にする。"""
    if isinstance(value, list):
        return [_set_value(v) for v in value]
    return _set_value(value)


# テンプレートの差し込みのうち、工程の入力に無い値の出所（差し込みの名前 → 出所。#3092）。表に無い名前は工程の入力・
# 打った場所（`route.derived`）・`params.json` の既定で決まる
FROM_ISSUE = "課題の題と本文"
# 書き手を信用しない（`GitHub.trusts` が偽。#3364）課題の題と本文は、作業の入力（指示として読まれる）へ写さない
FROM_UNTRUSTED_ISSUE = "課題の題と本文（書き手に write 以上の権限が無く、指示として渡さない）"
FROM_PROJECT = f"{PROJECT_FILE} の設定"
FROM_DEFAULT = "params.json の既定"
FROM_WORKTREE = "作業場所のブランチ"
FROM_INPUT = "工程の入力か打った場所"
FROM_STAGE = "工程の承認ゲート（モードが置く listen のステップ）"
# 工程から呼ぶプランの中の承認ゲート（テンプレートの `{{gate}}`）に入れる起動。承認の要否と回答は工程の側（モードが置く
# listen のステップ）が持つため、プランの中では待たずに通す（終了コード 0 で結果 JSON を出さない手は ok）。
# `params.json` の既定（`ndf answer ask`）は工程の外でテンプレートを使うときの値で、工程から使うと承認待ちを足してしまう
STAGE_GATE = ["true"]
FILL_SOURCES = {
    "title": FROM_ISSUE,
    "body_file": FROM_ISSUE,
    "issue": FROM_ISSUE,
    "test_command": FROM_PROJECT,
    "required": FROM_PROJECT,
    "review": FROM_PROJECT,
    "rebuild": FROM_PROJECT,
    "refactor": FROM_PROJECT,
    "worker": FROM_DEFAULT,
    "gate": FROM_STAGE,
    "branch": FROM_WORKTREE,
}
# `.ndf/project.json` の設定のうち、差し込みの値を持つキーの道（test_command は `test.suites` のテストの suite の command を設定の順に ` && ` でつなぐ）
PROJECT_KEYS = {
    "required": ("ci", "required_checks"),
    "review": ("workflow", "launch", "review"),
    "rebuild": ("workflow", "launch", "rebuild"),
    "refactor": ("workflow", "launch", "refactor"),
}
# 設定の `workflow`（launch・stages）の形。旧いプラグインの設定のスキーマへ足さず、読む側のここに置く（#3473）
WORKFLOW_SCHEMA = contracts.schema_path("project-workflow")
# 課題の本文を書き出す保存先のファイルと、題の写し（プランの実行の保存先の下）
ISSUE_BODY_FILE = "issue-{number}-body.md"
ISSUE_TITLE_FILE = "issue-{number}-title.json"


def issue_text(location: Path, number: int) -> Optional[tuple[str, str]]:
    """課題の題と本文（作業場所のリモートの GitHub から読む）。テストが差し替える。

    書き手を信用しない（`GitHub.trusts` が偽。#3364）なら None。本文は作業の入力として指示に読まれるためである。
    """
    hub = github.GitHub(git.Git(location).remote_repo(), github.client())
    title, authored = hub.issue_text(number)
    return (title, authored.body) if hub.trusts(authored) else None


def _strings(value: Any) -> Optional[list[str]]:
    """空でない文字列の並びならその並び、ほかは None。"""
    ok = isinstance(value, list) and value and all(isinstance(v, str) and v for v in value)
    return list(value) if ok else None


def _test_command(project: Mapping[str, Any]) -> Optional[str]:
    """設定の `test.suites` のうちテストの suite の command を、設定の順に ` && ` でつなぐ。無ければ None。"""
    suites = definition.dig(project, "test", "suites")
    found = [s for s in suites if isinstance(s, dict) and weight.is_test_suite(s)] if isinstance(suites, list) else []
    cmds = [c for c in (s.get("command") for s in found) if isinstance(c, str) and c]
    return " && ".join(cmds) if cmds else None


def _project_value(name: str, project: Mapping[str, Any]) -> Optional[route.Value]:
    """設定から読む差し込みの値。設定に無いか形が違えば None。"""
    if name == "test_command":
        return _test_command(project)
    return _strings(definition.dig(project, *PROJECT_KEYS[name]))


def _project(location: Path) -> dict[str, Any]:
    """作業場所のリポジトリの `.ndf/project.json`。git の根が決まらなければ空。

    読めないか、`workflow` が形（`WORKFLOW_SCHEMA`）に合わなければ `route.RouteError`。
    """
    try:
        project = ndf_settings(location, PROJECT_FILE)
        if "workflow" in project:
            contracts.require_schema(project["workflow"], WORKFLOW_SCHEMA, contracts.ContractError, f"{PROJECT_FILE} の workflow")
    except GitError:
        return {}
    except contracts.ContractError as exc:
        raise route.RouteError(str(exc)) from None
    return project


def _issue_values(run: StageRun, work_dir: Path, location: Path) -> Optional[dict[str, route.Value]]:
    """課題が 1 つなら、その題（title）・本文を書いたファイル（body_file）・番号（issue）。課題が 1 つでなければ空。

    題と本文は試行ごとに保存先へ初回に 1 度だけ写し（`ISSUE_BODY_FILE`・`ISSUE_TITLE_FILE`）、同じ試行の以後の呼び出し
    （承認の回答・続き・プランの打ち直し）はその写しを使う。工程の新しい試行は `clear_values` が写しを消し、読み直す。読み直すと GitHub を毎回呼び、途中で題が直されるとプランが変わって、終わっていない回へ
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
        return {"title": held["title"], "body_file": str(out), "issue": str(number)}
    found = issue_text(location, number)
    if found is None:
        return None
    title, body = found
    out.write_text(body, encoding="utf-8")
    plan_files.write_json(copy, {"title": title})
    return {"title": title, "body_file": str(out), "issue": str(number)}


def _branch_of(run: StageRun) -> Optional[str]:
    """入力の作業場所が今いるブランチ。作業場所が入力に無いか、ブランチに居なければ None。"""
    held = run.inputs["worktree"] if "worktree" in run.inputs else None
    if not isinstance(held, str) or not Path(held).is_dir():
        return None
    try:
        branch = git.Git(Path(held)).run("symbolic-ref", "--short", "HEAD").strip()
    except GitError:  # noqa: NDF401 外部コマンド: ブランチに居ない作業場所は、足りない値として名前を挙げる
        return None
    return branch if branch else None  # noqa: NDF501 外部コマンド: git が空のブランチ名を返せば足りない値として扱う


def ci_only(project: Mapping[str, Any]) -> bool:
    """設定（`_project` が読んだもの）がテストを CI に寄せる（`test.ci.only` が真）か（#3473）。"""
    test = definition.dig(project, "test")
    return scoped.ci_only(test if isinstance(test, Mapping) else {})


def template_values(
    t: route.Template, run: StageRun, work_dir: Path, cwd: Path, *, project: Mapping[str, Any], local_tests: bool
) -> dict[str, route.Value]:
    """テンプレートの差し込みの値。工程の入力と出所の表（`FILL_SOURCES`）から埋め（`_fill_from_sources`）→ 打った場所
    （`route.derived`）→ 課題の題と本文（`_fill_from_issue`）→ 起動の補いと受け入れ条件の検査（`route.launched`。単発の
    経路と共有）の順に決め、最後に工程から組むときだけの補い（`_fill_review`・`_fill_refactor`）を通す。

    課題は、課題に依存しない差し込みがそろってから読む（`_require_before_issue`）。試行の始まりに写しを消すので、先に読むと
    手元の設定が足りずに止まるたび、打ち直しで GitHub の課題と書き手の権限を読み直すためである（PR 4289 の指摘）。

    既定も無く決まらない差し込みは、名前と出所を挙げて `TemplateValuesMissing`。手元のテストのステップを外したプランを
    組むとき（`local_tests` が偽。テストを CI に寄せる設定 `ci_only` がある）は、そこだけが使う差し込み
    （`test_command` など）は求めない。`project` は呼び手が 1 度だけ読んだ設定（`_project`）で、CI に寄せるかの判定と
    設定の出所の差し込みが同じものを使う（読み直すと git・ファイル・形の検査が 2 回ずつ走る。PR 3525 の指摘）。
    """
    names, values, wanted = _fill_from_sources(t, run, project=project, local_tests=local_tests)
    values = route.derived(t, values, cwd)
    route.check_acceptance(values)  # 渡した acceptance の誤りは、欠けより先に断る
    _require_before_issue(t.name, names, values)
    issued, untrusted = _fill_from_issue(run, work_dir, work_location(run, cwd), wanted)
    values, launched = route.launched(t, {**values, **issued})
    values = {**values, **_fill_review(t, values, run, cwd, by_default="review" in launched)}
    values = {**values, **_fill_refactor(t, values, run, work_dir)}
    missing = _missing(names, values, {**FILL_SOURCES, **untrusted})
    if missing:
        raise TemplateValuesMissing(t.name, missing)
    return values


def _fill_from_sources(
    t: route.Template, run: StageRun, *, project: Mapping[str, Any], local_tests: bool
) -> tuple[tuple[str, ...], dict[str, route.Value], dict[str, str]]:
    """工程の入力と、出所の表（`FILL_SOURCES`）のうち課題を除く出所から埋めた値。プランが使う差し込みの名前・その値・
    入力に無く出所の表から求める差し込み → 出所（課題の出所は呼び手が `_fill_from_issue` で後から埋める）を返す。"""
    values = {k: _param_value(v) for k, v in run.inputs.items() if k in t.params}
    names = route.needed(t, local_tests=local_tests, given=values)
    wanted = {n: FILL_SOURCES[n] for n in names if n not in values and n in FILL_SOURCES}
    values = {**values, **_fill_from_project(project, wanted), **_fill_branch(run, wanted), **_fill_gate(wanted)}
    return names, values, wanted


# 課題を読んだ後に決まりうる差し込み（課題の出所と、`route.launched` が補う `review`・`acceptance`）
AFTER_ISSUE = frozenset({*(n for n, s in FILL_SOURCES.items() if s == FROM_ISSUE), "review", *route.LAUNCH_SOURCES})


def _require_before_issue(name: str, names: Sequence[str], values: Mapping[str, route.Value]) -> None:
    """課題を読む前に、課題を読んでも決まらない差し込み（`AFTER_ISSUE` の外）が欠けていれば `TemplateValuesMissing`。"""
    missing = {n: s for n, s in _missing(names, values, FILL_SOURCES).items() if n not in AFTER_ISSUE}
    if missing:
        raise TemplateValuesMissing(name, missing)


# つなぎ目のレビューの起点にする文脈のキー（#3879 構成要素 5）
GATHERED = "gathered"


def _fill_review(
    t: route.Template, values: Mapping[str, route.Value], run: StageRun, cwd: Path, *, by_default: bool
) -> dict[str, route.Value]:
    """範囲がつなぎ目（`unreviewed_diff`）のステップの `{{review}}` の起点を、`unreviewed_range` で確かめた `gathered` にする。

    既定の起動（`by_default`。`route.launch_values` が組んだもの）なら `review_launch` を `base=gathered` で組み直し、設定
    （`workflow.launch.review`）の起動なら並びを書き換えずに後ろへ `--base <gathered>` を足す。`gathered` が入力に無ければ
    今のまま（空）。判定できなければ `FactUnknown`（工程の条件と同じ理由で止まる）。
    """
    review, branch = values.get("review"), values.get("branch")
    if run.range != stages.UNREVIEWED_DIFF or GATHERED not in run.inputs or not isinstance(review, list):
        return {}
    _, _, gathered = unreviewed_range(run.inputs, cwd)
    if by_default and isinstance(branch, str):
        return {"review": route.review_launch(t.name, branch, gathered)}
    return {"review": [*review, "--base", gathered]}


REFACTOR_CANDIDATES = "refactor-candidates.md"
PLAN_FILE = "issues/issue-{number}-plan.md"
# implementation-plan の「リファクタリングの時機」の表（時機 | 対象 | 理由）の見出しと、スプリントの終わりの行の時機
TIMING_HEADING = "リファクタリングの時機"
SPRINT_END = "スプリントの終わり"
NO_TARGET = "しない"


def _cells(line: str) -> list[str]:
    """Markdown の表の行の列。エスケープしたパイプ（`\\|`）は列を区切らず、列の中では `|` に戻す。"""
    return plan_inputs.split_row(line)


def _sprint_end_rows(text: str) -> list[tuple[str, str]]:
    """実装計画の「リファクタリングの時機」の表（時機 | 対象 | 理由）の、スプリントの終わりの行の (対象, 理由)。

    見出しが無ければ空。対象が「しない」の行は含めない。
    """
    lines = text.splitlines()
    heading = next((i for i, line in enumerate(lines) if line.startswith("#") and line.lstrip("#").strip() == TIMING_HEADING), None)
    if heading is None:
        return []
    found = []
    for line in lines[heading + 1 :]:
        if line.startswith("#"):
            break
        cells = _cells(line) if line.startswith("|") else []
        if len(cells) == 3 and cells[0].startswith(SPRINT_END) and cells[1] != NO_TARGET:
            found.append((cells[1], cells[2]))
    return found


def _fill_refactor(t: route.Template, values: Mapping[str, route.Value], run: StageRun, state_dir: Path) -> dict[str, route.Value]:
    """cross-refactoring なら、課題の実装計画のスプリントの終わりの候補を `state_dir` へ書き、`refactor` の後ろに `--candidates` を足す。

    実装計画か表の無い課題は飛ばす。候補が 1 行も無ければ何も書かず、`refactor` は今のまま。
    """
    launch, worktree = values.get("refactor"), values.get("worktree")
    issues = run.inputs["issues"] if "issues" in run.inputs else []
    if t.name != "cross-refactoring" or not isinstance(launch, list) or not isinstance(worktree, str):
        return {}
    lines = []
    for number in issues:
        plan = Path(worktree) / PLAN_FILE.format(number=number)
        if plan.is_file():
            lines += [
                f"- #{number}: {target}" + (f"（理由: {why}）" if why else "")
                for target, why in _sprint_end_rows(plan.read_text(encoding="utf-8"))
            ]
    if not lines:
        return {}
    out = state_dir / REFACTOR_CANDIDATES
    out.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return {"refactor": [*launch, "--candidates", str(out)]}


def _fill_from_issue(
    run: StageRun, work_dir: Path, location: Path, wanted: Mapping[str, str]
) -> tuple[dict[str, route.Value], dict[str, str]]:
    """課題の出所の差し込みの値と、出所の上書き（書き手を信用しない課題なら値を返さず、出所を FROM_UNTRUSTED_ISSUE にする）。"""
    if FROM_ISSUE not in wanted.values():
        return {}, {}
    issued = _issue_values(run, work_dir, location)
    if issued is None:
        return {}, {n: FROM_UNTRUSTED_ISSUE for n, s in wanted.items() if s == FROM_ISSUE}
    return {k: v for k, v in issued.items() if k in wanted}, {}


def _fill_from_project(project: Mapping[str, Any], wanted: Mapping[str, str]) -> dict[str, route.Value]:
    """設定の出所の差し込みの値（設定に無いか形が違う差し込みは含めない）。"""
    if FROM_PROJECT not in wanted.values():
        return {}
    found = {name: _project_value(name, project) for name in (n for n, s in wanted.items() if s == FROM_PROJECT)}
    return {name: value for name, value in found.items() if value is not None}


def _fill_branch(run: StageRun, wanted: Mapping[str, str]) -> dict[str, route.Value]:
    """ブランチの出所の差し込みの値（決まらなければ含めない）。"""
    branch = _branch_of(run) if "branch" in wanted else None
    return {"branch": branch} if branch is not None else {}


def _fill_gate(wanted: Mapping[str, str]) -> dict[str, route.Value]:
    """承認ゲートの差し込みの値（工程の承認ゲートが決めるので、プランの中では待たずに通す）。"""
    return {"gate": list(STAGE_GATE)} if "gate" in wanted else {}


def _missing(names: Sequence[str], values: Mapping[str, route.Value], sources: Mapping[str, str]) -> dict[str, str]:
    """プランが使う差し込み `names` のうち、既定も無く決まらないもの → 出所（出所の表に無ければ工程の入力）。"""
    defs = route.params()
    return {n: (sources[n] if n in sources else FROM_INPUT) for n in names if n not in values and defs[n].default is None}


def write_values(state_dir: Path, plan: str, values: Mapping[str, Any], inputs: Mapping[str, Any], local_tests: bool) -> None:
    """プランの名前・差し込みの値・工程の入力・手元のテストを含めるか・書いた時刻を `<保存先>/values.json` へ書く。

    書けなければ `OSError` をそのまま上げる（呼び手の `plan_failure` が precondition にする）。
    """
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    held = {"plan": plan, "values": dict(values), "inputs": dict(inputs), "local_tests": local_tests, "at": at}
    plan_files.write_json(state_dir / VALUES_FILE, held)


def read_values(state_dir: Path) -> Optional[dict[str, Any]]:
    """`<保存先>/values.json`。無い・壊れている・キーか型が `VALUES_SHAPE` と違えば None。"""
    held = plan_files.read_json(state_dir / VALUES_FILE)
    if held is None or any(not isinstance(held.get(k), t) for k, t in VALUES_SHAPE.items()):
        return None
    return held


# 試行の始まりに消す、前の試行が保存先に残した値（差し込みの値と、課題の題・本文の写し）
TRIAL_FILES = (VALUES_FILE, ISSUE_BODY_FILE.format(number="*"), ISSUE_TITLE_FILE.format(number="*"))


def clear_values(state_dirs: Iterable[Path]) -> None:
    """渡された保存先から前の試行の値（`TRIAL_FILES`）を消す（無ければ何もしない）。保存先は NDF の状態の保存先の下である。

    課題の写しも消すのは、残すと `_issue_values` が写しを優先し、新しい試行が GitHub で直した題・本文を取り込まないためである。
    消せなければ `OSError` をそのまま上げる（呼び手の `progress.run._prepare` が precondition にする）。
    """
    for state_dir in state_dirs:
        if not state_dir.is_dir():
            continue
        for pattern in TRIAL_FILES:
            for found in state_dir.glob(pattern):
                found.unlink(missing_ok=True)


class PreparedPlan(NamedTuple):
    """組んだ差し込みと描画の条件（手元のテストのステップを含めるか）。"""

    template: route.Template
    values: dict[str, route.Value]
    local_tests: bool


# テンプレートの中で、作業の名前を後ろに置く worker の起動の差し込み
WORKER_SLOT = "{{worker}}"


def _work_names(plan: Any) -> list[str]:
    """プランの形の中で `{{worker}}` の後ろに置かれた作業の名前（現れた順。重なりを含む）。"""
    if isinstance(plan, dict):
        return [n for v in plan.values() for n in _work_names(v)]
    if not isinstance(plan, list):
        return []
    named = [str(plan[i + 1]) for i, v in enumerate(plan[:-1]) if v == WORKER_SLOT]
    return named + [n for v in plan for n in _work_names(v)]


def prepare_plan(run: StageRun, work_dir: Path, cwd: Path) -> PreparedPlan:
    """プランの差し込みを 1 回だけ組み、作業ごとの入力と作業場所を確かめて `values.json` に残す（#3908 の決定 2・4）。

    設定は 1 回だけ読み、差し込みは `template_values` で組む。決まらなければ `TemplateValuesMissing`、`{{worker}}` の作業が読む
    入力か作業場所が決まらなければ `KindInputsMissing` を上げ、どちらも `values.json` を書かない。テンプレートが無ければ `model.Refused`。
    """
    t = template(run.name)
    if t is None:
        raise model.Refused(f"プランのテンプレート {run.name} が無い")
    work_dir.mkdir(parents=True, exist_ok=True)
    project = _project(work_location(run, cwd))
    local_tests = not ci_only(project)
    values = template_values(t, run, work_dir, cwd, project=project, local_tests=local_tests)
    gaps: dict[str, str] = {}
    for kind in dict.fromkeys(_work_names(route.expanded(t, local_tests=local_tests))):
        gaps.update(worker_kinds.kind_gaps(kind, values, run.inputs, sources=FILL_SOURCES, covers=route.LAUNCH_SOURCES))
    if gaps:
        raise KindInputsMissing(t.name, gaps)
    write_values(work_dir, run.name, values, run.inputs, local_tests)
    return PreparedPlan(t, values, local_tests)


def _prepared(run: StageRun, work_dir: Path, cwd: Path) -> PreparedPlan:
    """同じ保存先に同じプランの `values.json` があればその値、無い（か別のプランのもの）なら `prepare_plan` で組んだ値。"""
    t, held = template(run.name), read_values(work_dir)
    if t is not None and held is not None and held["plan"] == run.name:
        return PreparedPlan(t, held["values"], held["local_tests"])
    return prepare_plan(run, work_dir, cwd)


def plan_path(run: StageRun, work_dir: Path, cwd: Path) -> Path:
    """テンプレートを差し込みの値で埋めたプランを実行の保存先へ書き、そのパスを返す。

    差し込みは同じ試行で組んだ `values.json` の値を使い、無ければ `prepare_plan` で 1 回だけ組む（入力に無い値は出所の表
    `FILL_SOURCES` と打った場所から決め、欠ければ `TemplateValuesMissing`）。テンプレートが無ければ `model.Refused`。
    """
    t, values, local_tests = _prepared(run, work_dir, cwd)
    plan = route.render(t, values, local_tests=local_tests)
    out = work_dir / f"{run.name}.json"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def _previous(work_dir: Path) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """保存先の前の回の記録（`run.json`）と、その回の結果のバックアップ（終わっていなければ None）。"""
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

    止まっていなければ前の回の開始点（承認の回答を渡すときも同じ回に届く）。前の回が無ければ None（初めから）。
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
    """前の回が承認ゲートで終わっていれば、その結果の routes（回答 → 続きのコマンドか None）。ほかは None。

    終わった回へ `--answer` を渡しても、プランはバックアップの結果を返すだけである。続きは routes の開始点から新しい回で流す。
    """
    _, done = _previous(work_dir)
    if done is None or done["status"] != result.GATE or not isinstance(done.get("routes"), dict):
        return None
    return dict(done["routes"])


def plan_attempt(run: StageRun, work_dir: Path) -> int:
    """プランの回の打ち直しの数。打ち直し（`retries` が増えたか `again`）で前の回が終わっていれば 1 つ上げて保存先に残す。

    差し込み `progress_attempt` に載せて回のキーを変える。開始点と head が前の回と同じでも、プランが終わった回のバックアップの結果を
    返さず、新しい回を流すためである。打ち直さない呼び出し（承認の回答・作業が落ちた後の続き）は残した数をそのまま使い、同じ回に届く。
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
    """`ndf plan next <プラン> [--from <ステップ>] --state-dir <保存先> --set <キー>=<値>...`（回答があれば `--answer`）。

    作業場所（`worktree`）は `--set` に載せず、埋めたプランの作業場所で渡す（`plan_path`）。前の回が承認ゲートで終わって
    いれば、回答の routes の開始点から `--answer` 無しで新しい回を流す（`stored_gate_routes`）。
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
    """承認ゲートで終わった回への回答に続きが無いときの結果。承認ならプランは終わり、ほかは承認されなかった。続きがあれば None。"""
    routes = stored_gate_routes(work_dir) if answer else None
    if routes is None or _from_of(routes[answer] if answer in routes else None) is not None:
        return None
    if answer == APPROVED:
        _, done = _previous(work_dir)
        assert done is not None
        outputs = done["outputs"] if isinstance(done.get("outputs"), dict) else {}
        return finished(run, result.result(PLAN, result.OK, f"プラン {run.name} の最後の承認ゲートが承認された", outputs=outputs))
    return StageResult(result.stopped(PLAN, "refused", f"プラン {run.name} の承認ゲートで承認されなかった（回答 {answer}）"), {})


def same_run(before: StageResult, after: StageResult) -> bool:
    """2 つの結果が同じプランの回の結果か（打ち直しても回が変わらず、プランがバックアップの結果を返した）。"""
    run_id = before.report["run_id"] if "run_id" in before.report else None
    return isinstance(run_id, str) and "run_id" in after.report and after.report["run_id"] == run_id


def _from_process(run: StageRun, done: subprocess.CompletedProcess[str]) -> StageResult:
    report = read_report(done.stdout)
    if report is None:
        return bad_report(run, f"終了コード {done.returncode}")
    return finished(run, report)


def _github_kind(exc: github.GitHubError) -> str:
    """GitHub の失敗の停止理由。打ち直せば通りうるもの（不達・5xx・上限）は transient、ほかは precondition。

    分け方は課題の参照の読み手（`ndf.core.refs` の `_unreadable`）と同じ。状態が 0 でも届いて断られた失敗（GraphQL の誤り・
    認証が無い）は precondition。
    """
    passing = exc.unreached or exc.status >= 500 or exc.retry_after is not None or exc.status == 429
    return "transient" if passing else "precondition"


# プランを組むときに上がりうる例外（`plan_failure` が停止理由へ分ける型。`_call_plan` と `progress.run._start` が捕まえる）
PLAN_FAILURES = (route.RouteError, GitError, FactUnknown, github.GitHubError, OSError)


def plan_failure(name: str, exc: Exception) -> Optional[dict[str, Any]]:
    """プラン `name` を組めなかった例外の `stopped` の結果 JSON。分類に無い例外は None（呼び手が上げ直す）。

    差し込みと作業の入力の欠け（`TemplateValuesMissing`・`KindInputsMissing`）は値を足して打ち直せば通るので precondition に
    し、名前と出所を `items` に並べる（決定 4）。ほかの分類は工程から呼ぶ経路（`_call_plan`）と起動の前の確かめ
    （`progress.run._start`）で同じである。
    """
    if isinstance(exc, (TemplateValuesMissing, KindInputsMissing)):
        detail = f"プラン {name} の差し込み {', '.join(exc.missing)} を決められない"
        return result.stopped(PLAN, "precondition", detail, items=exc.items)
    if isinstance(exc, route.RouteError):
        return result.stopped(PLAN, "invalid", f"プランのテンプレート {name} を埋められない: {exc}")
    if isinstance(exc, GitError):
        return result.stopped(PLAN, "precondition", f"プランのテンプレート {name} の省いた値を決められない: {exc}")
    if isinstance(exc, FactUnknown):
        return result.stopped(PLAN, "precondition", f"プランのテンプレート {name} のレビューの起点を決められない: {exc}")
    if isinstance(exc, github.GitHubError):
        detail = f"プランのテンプレート {name} の差し込みを GitHub から読めない: {exc}"
        return result.stopped(PLAN, _github_kind(exc), detail, retry_after=exc.retry_after)
    if isinstance(exc, OSError):
        return result.stopped(PLAN, "precondition", f"プラン {name} の保存先へ書けない: {exc}")
    return None


def _call_plan(run: StageRun, cwd: Path, work_dir: Path, answer: Optional[str], runner: Runner) -> StageResult:
    try:
        if template(run.name) is None:
            return StageResult(result.stopped(PLAN, "precondition", f"プランのテンプレート {run.name} が無い"), {})
        ended = _no_route(run, work_dir, answer)
        if ended is not None:
            return ended
        argv = plan_argv(run, work_dir, answer, cwd)
    except PLAN_FAILURES as exc:
        failed = plan_failure(run.name, exc)
        if failed is None:
            raise
        return StageResult(failed, {})
    return _from_process(run, runner(argv, cwd, run.env({})))


def script_argv(argv: Sequence[Any]) -> list[str]:
    """script の作業の argv。先頭がこのプラグインの名前なら、そのサブコマンドを同じ Python で打つ（`ndf_command`）。"""
    words = [str(a) for a in argv]
    return ndf_command(*words[1:]) if words and words[0] == PACKAGE else words


def _call_script(run: StageRun, cwd: Path, runner: Runner) -> StageResult:
    env = run.env({INPUT_ENV: json.dumps(run.inputs, ensure_ascii=False)})
    return _from_process(run, runner(script_argv(run.args["argv"]), cwd, env))


def skill_prompt(run: StageRun) -> str:
    """Skill を回す質問。入力の値は文脈の形を通した JSON だけを渡す。"""
    prompt = f"/{identity.load().prefix}{run.name}\n\n入力（JSON）: {json.dumps(run.inputs, ensure_ascii=False)}"
    return prompt if run.notes is None else f"{prompt}\n\n変更要求の指摘:\n{run.notes}"


def _skill_cause(failure: Optional[claude.Failure]) -> str:
    """`claude -p` が回答を返せなかった理由 → 停止理由。理由の無い失敗は打ち直せば直りうる。"""
    if failure is None:
        return "transient"
    return SKILL_FAILURES[failure] if failure in SKILL_FAILURES else "precondition"


def _call_skill(run: StageRun, cwd: Path, call_claude: SkillCaller) -> StageResult:
    schema = contracts.load_json(contracts.schema_path("result"))
    call = claude.ClaudeCall(system=SKILL_SYSTEM, permission=claude.Permission.FULL, json_schema=schema)
    argv, notice = claude.claude_launch(call, cwd=cwd)
    reply = call_claude(argv, skill_prompt(run), limits.seconds(SKILL_LIMIT), cwd)
    if not reply.ok:
        kind = _skill_cause(reply.failure)
        got = StageResult(result.stopped(SKILL, kind, f"Skill {run.name} を回せない: {reply.error}"), {})
    elif not isinstance(reply.answer, dict) or result.errors(reply.answer):
        got = bad_report(run, "回答が結果 JSON の形に合わない")
    else:
        got = finished(run, reply.answer)
    return got if notice is None else replace(got, warnings=(*got.warnings, notice))


def invoke(
    run: StageRun,
    cwd: Path,
    work_dir: Path,
    answer: Optional[str] = None,
    runner: Runner = run_command,
    *,
    call_claude: SkillCaller = claude.call_claude,
) -> StageResult:
    """作業を種類ごとに呼ぶ。`work_dir` はプランの実行の保存先、`answer` はプランの承認ゲートの回答。

    `runner` は plan・script の子プロセスを、`call_claude` は skill の `claude -p` を打つ。skill と script は、入力に
    作業場所（`worktree`）があればそこで打つ（プランは埋めたプランの作業場所で受ける）。
    """
    if run.kind == PLAN:
        return _call_plan(run, cwd, work_dir, answer, runner)
    if run.kind not in (SCRIPT, SKILL):
        raise model.Refused(f"作業種別 {run.kind} は呼ばない（回答を待つステップ）")
    location = work_location(run, cwd)
    if not location.is_dir():
        return StageResult(result.stopped(run.kind, "precondition", f"作業場所 {location} が無い"), {})
    if run.kind == SCRIPT:
        return _call_script(run, location, runner)
    return _call_skill(run, location, call_claude)


def work_location(run: StageRun, cwd: Path) -> Path:
    """skill・script を打つ作業ディレクトリ。入力の作業場所（`worktree`）があればそこ、無ければ打った場所。"""
    held = run.inputs["worktree"] if "worktree" in run.inputs else None
    return Path(held) if isinstance(held, str) and held else cwd
