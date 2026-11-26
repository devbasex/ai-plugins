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
打ち直すと、プランは前の回のバックアップの結果を返すだけだからである。PR の番号を書くステップより後から始めるプランに
番号の記録が無ければ、開始点は変えずに、先に開いた PR の番号を書き戻す（`restore_pr_number`）。skill と script は入力の
作業場所で打つ。

工程の入力に無い差し込みは、値の性質ごとの出所（`FILL_SOURCES`）から埋める（#3092）。課題の題と本文は課題から写し、
テストのコマンド・必須のチェック・レビューと組み直しとリファクタリングの起動は `.ndf/project.json` の設定から読み（レビューは設定が
無ければ `ndf cross-review run` を、打つ時点の PR の番号で組む。`route.review_launch`）、worker と承認ゲートの
起動は `params.json` の既定を使う。どこからも決まらない差し込みは、名前と出所を挙げて invalid で止める。
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import contracts, identity
from ndf.adapters import claude, git, github
from ndf.adapters.git import GitError
from ndf.cli import PACKAGE, child_encoding, ndf_command
from ndf.contracts import facts, internal, limits, result, worker_paths
from ndf.contracts.worker_kinds import VALUES_FILE, kind_gaps, read_values, write_values
from ndf.core import durable, production_merge, route
from ndf.core.plan import cli as plan_cli
from ndf.core.plan import files as plan_files
from ndf.core.plan import model as plan_model
from ndf.core.plan import runner as plan_runner
from ndf.core.plan import steps as plan_steps
from ndf.progress import definition, issue_values, merge_pending, model, plan_runs, rebuild_values, refactor_values, stages
from ndf.progress.command import PROJECT_FILE, FactUnknown, ndf_settings, unreviewed_range
from ndf.progress.plan_runs import StageResult, StageRun
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
# skill の作業の上限（Skill 1 本を回す起動は実装の工程と同じ桁）
SKILL_LIMIT = "monitor.phase.implement"
SKILL_SYSTEM = "工程の作業を 1 つ行い、最後に結果 JSON（統一 JSON レポート）だけを回答する。"
ACTOR = model.WORKFLOW_ACTOR
# 承認の回答の語（表 answers の行）。プランの承認ゲートの続きが無い承認は、プランが終わったとみなす
APPROVED = str(facts.row(result.ANSWERS, "approved")["id"])
# プランの実行の保存先に残す、回の打ち直しの数（`plan_attempt`）と、それを渡す差し込みの名前
ATTEMPT_FILE = "progress-attempt.json"
ATTEMPT_SET = "progress_attempt"


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
    """テンプレートの差し込みの値がどの出所からも決まらない。`missing` は足りない差し込みの名前 → 出所、`details` は名前 →
    行へ足すキー（#4337 の決定 7。`rebuild` の既定を決められない理由・設定のキー・値の形）。"""

    def __init__(self, template_name: str, missing: Mapping[str, str], details: Optional[Mapping[str, Mapping[str, str]]] = None) -> None:
        listed = ", ".join(f"{n}（{s}）" for n, s in missing.items())
        super().__init__(f"テンプレート {template_name} の差し込みの値が無い: {listed}")
        self.missing = dict(missing)
        given = details if details is not None else {}
        self.details = {n: dict(given[n]) for n in self.missing if n in given}

    @property
    def items(self) -> list[dict[str, str]]:
        return [
            {"key": n, "source": s, **self.details[n]} if n in self.details else {"key": n, "source": s} for n, s in self.missing.items()
        ]


class KindInputsMissing(route.RouteError):
    """プランの作業（`{{worker}}` の後ろの名前）が読む入力か作業場所が決まらない。`missing` は名前 → 出所。"""

    def __init__(self, template_name: str, missing: Mapping[str, str]) -> None:
        listed = ", ".join(f"{n}（{s}）" for n, s in missing.items())
        super().__init__(f"テンプレート {template_name} の作業の入力が無い: {listed}")
        self.missing = dict(missing)

    @property
    def items(self) -> list[dict[str, str]]:
        return [{"key": n, "source": s} for n, s in self.missing.items()]


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
    # 規則は工程の入力の規約（`stage-in`）の 1 つで、`ndf records mvv gate` の環境変数の経路も同じ規則で確かめる（#3959 の決定 10）
    errs = definition.validate_stage_input(stage, picked)
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
    gated = bool(stage.gates)
    return StageRun(kind, name, dict(inputs), stage.outputs, step.range, step.id, dict(step.args), gated=gated, per_unit=stage.per_unit)


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


def _template_of(run: StageRun) -> Optional[route.Template]:
    """呼び出しが描画するテンプレート。呼び出しの上書き（`run.template`）が無ければ、名前で引く。"""
    return run.template if run.template is not None else template(run.name)


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
# 工程がプランへ回答を渡すときの回答した者（`ndf plan next --by`）
PLAN_ANSWER_BY = "service:progress"
# 工程から組むプランの `ndf ci merge --production-gate`（#4409 の決定 4・決定 5）。承認ゲートの無い工程（Pull Request・
# ドキュメントレビュー・確定仕様化）は承認の無いマージをデプロイへ先送りし、承認ゲートを持つ工程（設計・デプロイ）は
# 承認の後に対象が変わっていれば approval_stale で止めて質問し直す。表に無いテンプレートは既定の stop のまま組む
_DEFER, _STALE = production_merge.DEFER, production_merge.STALE
PRODUCTION_GATE = {"merge": _DEFER, "plan-to-spec": _DEFER, "design-merge": _STALE, "release": _STALE}
FILL_SOURCES = {
    "title": FROM_ISSUE,
    "body_file": FROM_ISSUE,
    "issue": FROM_ISSUE,
    "test_command": FROM_PROJECT,
    "required": FROM_PROJECT,
    "release_required": FROM_PROJECT,
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
    "release_required": ("ci", "required_checks"),
    "review": ("workflow", "launch", "review"),
    "rebuild": rebuild_values.SETTING_PATH,
    "refactor": ("workflow", "launch", "refactor"),
}
# 設定の `workflow`（launch・stages）の形。読む側が確かめる（#3473）
WORKFLOW_SCHEMA = contracts.schema_path("project-workflow")


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
    （`route.derived_locally`）→ 課題の題と本文（`_fill_from_issue`）→ マージの方式（`route.derived_merge_method`）→
    起動の補いと受け入れ条件の検査（`route.launched`。単発の経路と共有）の順に決め、最後に工程から組むときだけの補い
    （`_fill_review`）を通す。リファクタリングの起動（`_fill_refactor`）は、設定が無いときの既定を課題を読む前の欠けの検査
    （`_require_before_issue`）に見せるため、打った場所の値のすぐ後に決める（#3908 の決定 7）。
    組み直しの起動（`rebuild_values.fill`）は、決めた `base`・`remote` を打った場所の値にも使うため、その前に決める（#4337 の決定 7）。

    課題は、課題に依存しない差し込みがそろってから読む（`_require_before_issue`）。試行の始まりにコピーを消すので、先に読むと
    手元の設定が足りずに止まるたび、打ち直しで GitHub の課題と作成者の権限を読み直すためである（PR 4289 の指摘）。
    同じ理由で、課題の前には手元で決まる打った場所の値（`route.derived_locally`）だけを決め、GitHub に問うマージの方式
    （`route.derived_merge_method`）は課題を読んだ後に決める（課題を読めずに止まる試行で問い合わせを増やさない。PR 4289 の指摘）。
    ただしマージの方式を決められずに止まった試行の打ち直し（目印 `merge_pending.PENDING_FILE` がある）では、方式を課題より
    先に問うて、決まらなければ課題を読まずに止める（`_merge_method_first`）。方式を問う GitHub の失敗のうち打ち直せば
    通りうるものは、方式が無いとせずに `github.GitHubError` を上げる（`merge_pending.ask`。PR 4289 の指摘）。

    既定も無く決まらない差し込みは、名前と出所を挙げて `TemplateValuesMissing`。手元のテストのステップを外したプランを
    組むとき（`local_tests` が偽。テストを CI に寄せる設定 `ci_only` がある）は、そこだけが使う差し込み
    （`test_command` など）は求めない。`project` は呼び手が 1 度だけ読んだ設定（`_project`）で、CI に寄せるかの判定と
    設定の出所の差し込みが同じものを使う（読み直すと git・ファイル・形の検査が 2 回ずつ走る。PR 3525 の指摘）。
    """
    names, values, wanted = _fill_from_sources(t, run, project=project, local_tests=local_tests)
    values = {**values, **_fill_production_gate(t, run)}
    filled = rebuild_values.fill(t, values, issue_values.issue_numbers(run.inputs), run.inputs.get("mode"), cwd, project=project)
    if filled.undecided is not None:
        raise TemplateValuesMissing(t.name, {"rebuild": FROM_PROJECT}, {"rebuild": filled.undecided.fields()})
    values = route.derived_locally(t, {**values, **filled.values}, cwd)
    route.check_acceptance(values)  # 渡した acceptance の誤りは、欠けより先に断る
    values = {**values, **_fill_refactor(t, values, run, work_dir, project=project)}
    _require_before_issue(t.name, names, values)
    pending = work_dir / merge_pending.PENDING_FILE
    values = _merge_method_first(t, names, values, pending)
    issued, untrusted = _fill_from_issue(run, work_dir, work_location(run, cwd), wanted)
    values = merge_pending.ask(t, {**values, **issued}, pending)
    values, launched = route.launched(t, values, issues=issue_values.issue_numbers(run.inputs))
    values = {**values, **_fill_review(t, values, run, cwd, by_default="review" in launched)}
    missing = _missing(names, values, {**FILL_SOURCES, **untrusted})
    if "merge_method" in missing:
        merge_pending.mark(pending)
    if missing:
        raise TemplateValuesMissing(t.name, missing)
    pending.unlink(missing_ok=True)
    return values


def _merge_method_first(
    t: route.Template, names: Sequence[str], values: Mapping[str, route.Value], pending: Path
) -> dict[str, route.Value]:
    """前の試行がマージの方式を決められずに止まっていれば（目印 `pending` がある）、課題を読む前に方式を問う。

    目印に同じ値で決めた方式が記録してあれば問わずに使う（`merge_pending.held_method`。方式が決まった後で課題を読めずに
    止まった打ち直し）。止まっている間に GitHub の許す方式が変わってもその場では問わず、GitHub がその方式でのマージを断った
    ときに組み直す（`merge_pending.refused`。PR 4289 の指摘）。問うて決まらなければ課題を読まずに `TemplateValuesMissing`
    （目印は残す）、決まれば方式を目印へ記録しておいて足した値を返す。目印が無ければ今の値のまま（方式は課題の後に問う）。
    """
    mark = merge_pending.read(pending)
    if mark is None:
        return dict(values)
    held = merge_pending.held_method(mark, values)
    if held is not None and "merge_method" in t.params and "merge_method" not in values:
        return {**values, "merge_method": held}
    out = merge_pending.ask(t, values, pending)
    missing = _missing(names, out, FILL_SOURCES)
    if "merge_method" in missing:
        raise TemplateValuesMissing(t.name, {"merge_method": missing["merge_method"]})
    merge_pending.remember(pending, values, out.get("merge_method"))
    return out


def _fill_from_sources(
    t: route.Template, run: StageRun, *, project: Mapping[str, Any], local_tests: bool
) -> tuple[tuple[str, ...], dict[str, route.Value], dict[str, str]]:
    """工程の入力と呼び出しの上書き（`run.overrides`）、出所の表（`FILL_SOURCES`）のうち課題を除く出所から埋めた値。プランが
    使う差し込みの名前・その値・入力にも上書きにも無く出所の表から求める差し込み → 出所（課題の出所は呼び手が
    `_fill_from_issue` で後から埋める）を返す。"""
    values = {**{k: _param_value(v) for k, v in run.inputs.items() if k in t.params}, **run.overrides}
    names = route.needed(t, local_tests=local_tests, given=values)
    wanted = {n: FILL_SOURCES[n] for n in names if n not in values and n in FILL_SOURCES}
    values = {**values, **_fill_from_project(project, wanted), **_fill_branch(run, wanted), **_fill_gate(wanted)}
    return names, {**values, **_fill_refs(t, values, run)}, wanted


def _fill_refs(t: route.Template, values: Mapping[str, route.Value], run: StageRun) -> dict[str, route.Value]:
    """PR の本文に `Refs #<番号>` で載せる課題（`{{refs}}`）を、工程の入力のすべての課題からコピーする（#3908 の r2-2）。

    スプリントの PR の本文はコミットから作る（`gh pr create --fill`）ため、まとめた課題のうちコミットに出ない課題を
    「スプリントを閉じる」が本文から拾えない。設計の PR と共有する課題の本文のコピー（`issue_values.issue_values`）には足さない。
    """
    if "refs" not in t.params or "refs" in values:
        return {}
    issues = issue_values.issue_numbers(run.inputs)
    return {"refs": issues} if issues else {}


# 課題を読んだ後に決まりうる差し込み（課題の出所と、`route.launched` が補う `review`・`acceptance`、課題の後に GitHub に問う
# `merge_method`）
AFTER_ISSUE = frozenset({*(n for n, s in FILL_SOURCES.items() if s == FROM_ISSUE), "review", "merge_method", *route.LAUNCH_SOURCES})


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


def _fill_refactor(
    t: route.Template, values: Mapping[str, route.Value], run: StageRun, state_dir: Path, *, project: Mapping[str, Any]
) -> dict[str, route.Value]:
    """工程「リファクタリング」の `{{refactor}}`（既定の起動と候補。`refactor_values.fill`）。"""
    issues = run.inputs["issues"] if "issues" in run.inputs else []
    return refactor_values.fill(t, values, issues, state_dir, project=project)


def _fill_from_issue(
    run: StageRun, work_dir: Path, location: Path, wanted: Mapping[str, str]
) -> tuple[dict[str, route.Value], dict[str, str]]:
    """課題の出所の差し込みの値と、出所の上書き（書き手を信用しない課題なら値を返さず、出所を FROM_UNTRUSTED_ISSUE にする）。"""
    if FROM_ISSUE not in wanted.values():
        return {}, {}
    issued = issue_values.issue_values(run.inputs, work_dir, location)
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


def _fill_production_gate(t: route.Template, run: StageRun) -> dict[str, route.Value]:
    """工程から組むプランの `production_gate`（`PRODUCTION_GATE`）と、デプロイの `approved_head`・`approved_base`。

    `production_gate` を使うテンプレートが表に無ければ値を入れず、既定の stop（承認待ちで止まる）のまま組む。
    本番チャネルへのマージを承認の前に進めない側へ倒すためである。
    """
    out: dict[str, route.Value] = {}
    if "production_gate" in t.params and t.name in PRODUCTION_GATE:
        # 承認ゲートを持たない工程（light の設計）は質問し直す承認ゲートが無いので、承認をデプロイへ先送りする
        held = PRODUCTION_GATE[t.name]
        out["production_gate"] = held if held != _STALE or run.gated else _DEFER
    if "approved_head" in t.params:
        out.update(_approved_target(run))
    return out


def _approved_target(run: StageRun) -> dict[str, route.Value]:
    """同じ工程の承認ゲートの最新の承認の回答が見た HEAD とマージ先。旧版の回答（マージ先が無い）は空文字で、
    `ndf ci merge` がマージ先の違いとして approval_stale で止め、承認ゲートが質問し直す（#4409 の「影響と危険」）。"""
    seen = run.approved if run.approved is not None else {}
    head, base = seen.get("head"), seen.get("base")
    return {"approved_head": head if isinstance(head, str) else "", "approved_base": base if isinstance(base, str) else ""}


def _fill_gate(wanted: Mapping[str, str]) -> dict[str, route.Value]:
    """承認ゲートの差し込みの値（工程の承認ゲートが決めるので、プランの中では待たずに通す）。"""
    return {"gate": list(STAGE_GATE)} if "gate" in wanted else {}


def _missing(names: Sequence[str], values: Mapping[str, route.Value], sources: Mapping[str, str]) -> dict[str, str]:
    """プランが使う差し込み `names` のうち、既定も無く決まらないもの → 出所（出所の表に無ければ工程の入力）。"""
    defs = route.params()
    return {n: (sources[n] if n in sources else FROM_INPUT) for n in names if n not in values and defs[n].default is None}


class PreparedPlan(NamedTuple):
    """組んだ差し込みと描画の条件（手元のテストのステップを含めるか）。"""

    template: route.Template
    values: dict[str, route.Value]
    local_tests: bool


def prepare_plan(run: StageRun, work_dir: Path, cwd: Path) -> PreparedPlan:
    """プランの差し込みを 1 回だけ組み、作業ごとの入力と作業場所を確かめて `values.json` に残す（#3908 の決定 2・4）。

    設定は 1 回だけ読み、差し込みは `template_values` で組む。決まらなければ `TemplateValuesMissing`、`{{worker}}` の作業が読む
    入力か作業場所が決まらなければ `KindInputsMissing` を上げ、どちらも `values.json` を書かない。テンプレートが無ければ `model.Refused`。
    """
    t = _template_of(run)
    if t is None:
        raise model.Refused(f"プランのテンプレート {run.name} が無い")
    work_dir.mkdir(parents=True, exist_ok=True)
    project = _project(work_location(run, cwd))
    local_tests = not ci_only(project)
    values = template_values(t, run, work_dir, cwd, project=project, local_tests=local_tests)
    gaps: dict[str, str] = {}
    for kind in route.kinds_of(t, local_tests):
        gaps.update(kind_gaps(kind, values, run.inputs, sources=FILL_SOURCES, covers=route.LAUNCH_SOURCES))
    if gaps:
        raise KindInputsMissing(t.name, gaps)
    write_values(work_dir, run.name, values, run.inputs, local_tests)
    return PreparedPlan(t, values, local_tests)


def _prepared(run: StageRun, work_dir: Path, cwd: Path) -> PreparedPlan:
    """同じ保存先に同じプランの `values.json` があればその値、無い（か別のプランのもの）なら `prepare_plan` で組んだ値。

    テンプレートはどちらも `_template_of` のもの（名前で引き直すと、上書きで外したステップが打ち直しで戻る）。
    """
    t, held = _template_of(run), read_values(work_dir)
    if t is not None and held is not None and held["plan"] == run.name:
        return PreparedPlan(t, held["values"], held["local_tests"])
    return prepare_plan(run, work_dir, cwd)


def plan_path(run: StageRun, work_dir: Path, cwd: Path) -> Path:
    """テンプレートを差し込みの値で埋めたプランを実行の保存先へ書き、そのパスを返す。

    差し込みは同じ試行で組んだ `values.json` の値を使い、無ければ `prepare_plan` で 1 回だけ組む（入力に無い値は出所の表
    `FILL_SOURCES` と打った場所から決め、欠ければ `TemplateValuesMissing`）。テンプレートが無ければ `model.Refused`。

    プランは `values.json` と同じく、開いた保存先の記述子から、ほかの利用者が書き込めないファイルとして公開する（リンクを
    たどって別のファイルを上書きしない）。`ndf plan next` はこのパスを開き直すので、返す前に、名前と保存先から根までのどの
    項目もほかの利用者が差し替えられないことを確かめる（`worker_paths.check_unshared`。書いたプランと別のプランを流さない）。
    """
    t, values, local_tests = _prepared(run, work_dir, cwd)
    plan = route.render(t, values, local_tests=local_tests)
    name = f"{run.name}.json"
    with worker_paths.open_or_create(work_dir) as wd:
        worker_paths.write_file(wd, name, json.dumps(plan, ensure_ascii=False, indent=2) + "\n", private=True)
        worker_paths.check_unshared(wd, name)
        return wd.path / name


def plan_start(work_dir: Path) -> Optional[str]:
    """プランを打ち直す開始点。前の回が止まっていれば、その結果の打ち直し（`next`）の `--from`（止まったステップ）。

    止まっていなければ前の回の開始点（承認の回答を渡すときも同じ回に届く）。前の回が無ければ None（初めから）。
    """
    record, done = plan_runs.previous(work_dir)
    if record is None:
        return None
    again = plan_runs.from_of(done["next"]) if done is not None and done["status"] == result.STOPPED and "next" in done else None
    if again is not None:
        return again
    return record["start"] if "start" in record and isinstance(record["start"], str) else None


def restore_pr_number(plan_file: Path, work_dir: Path, start: str) -> None:
    """開始点 `start` から流すプランが読む PR の番号の記録が保存先に無ければ、開いた PR の番号を書き戻す（#4369）。

    番号を保存する前のバージョンで始めた実装は、PR を作った後で止まっていても番号を持たない。照会は番号を書くステップ自身の
    `probe` を打つだけで、開始点は変えない（レビューや CI の修正をやり直さない）。対象は、番号を `probe` が書くステップがあり、
    ほかのステップの `run` が読むプランだけである。開始点から番号を書くステップを通るなら、番号はそのステップに任せる。
    照会と書き込みはプランの実行と同じロックの内側で行う（同時の打ち直しが、書きかけの記録を読まない）。
    開いた PR が無いか、照会か保存に失敗すれば、理由を持つ `PrNumberUnrestored`。
    """
    plan = plan_model.load(plan_file)
    writers = [s for s in plan.steps if s.probe is not None and route.PR_NUMBER_FILE in s.probe]
    read = any(route.PR_NUMBER_FILE in s.run for s in plan.steps if s not in writers)
    if not writers or not read or _passes_writer(plan, writers, start):
        return
    saved = Path(route.PR_NUMBER_FILE.replace("{state_dir}", str(work_dir)))
    ctx = plan_steps.StepContext(plan, work_dir, {"state_dir": str(work_dir)})
    with plan_runner.plan_lock(work_dir):
        if saved.is_file() and saved.stat().st_size:
            return
        got = plan_steps.probe(ctx, writers[0], plan_steps.timeout_of(writers[0]))
    if got is None:
        raise plan_runs.PrNumberUnrestored("precondition", "このブランチからマージ先へ出した開いた PR が無い")
    if got["status"] != result.OK:
        raise plan_runs.PrNumberUnrestored(str(got["kind"]), str(got["summary"]))


def _passes_writer(plan: plan_model.Plan, writers: Sequence[plan_model.Step], start: str) -> bool:
    """`start` から通った場合の経路（`next`、無ければ並びの次）が、番号を読むステップより先に番号を書くステップに着くか。"""
    seen: set[str] = set()
    step = plan.step(start)
    while step is not None and step.id not in seen:
        if step in writers:
            return True
        if route.PR_NUMBER_FILE in step.run:
            return False
        seen.add(step.id)
        target = step.next if step.next is not None else plan.following(step.id)
        step = plan.step(target) if target is not None else None
    return False


def stored_gate_routes(work_dir: Path) -> Optional[dict[str, Any]]:
    """前の回が承認ゲートで終わっていれば、その結果の routes（回答 → 続きのコマンドか None）。ほかは None。

    終わった回へ `--answer` を渡しても、プランはバックアップの結果を返すだけである。続きは routes の開始点から新しい回で流す。
    """
    _, done = plan_runs.previous(work_dir)
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
    _, done = plan_runs.previous(work_dir)
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
        start, answer = plan_runs.from_of(routes[answer] if answer in routes else None), None
    else:
        start = plan_start(work_dir)
        start = start if start is not None else run.start
    plan_file = plan_path(run, work_dir, cwd)
    if start:
        restore_pr_number(plan_file, work_dir, start)
    attempt = plan_attempt(run, work_dir)
    argv = ndf_command(PLAN, "next", str(plan_file), *(["--from", start] if start else []), "--state-dir", str(work_dir))
    for key, value in run.inputs.items():
        if key != "worktree":
            argv += ["--set", f"{key}={_set_value(value)}"]
    if attempt:
        argv += ["--set", f"{ATTEMPT_SET}={attempt}"]
    # 工程の承認ゲートが受けた回答をプランへ渡す（回答した者は工程の進行。プランの承認待ちの証跡は書かれない。#4409）
    return argv + (["--answer", answer, "--by", PLAN_ANSWER_BY] if answer else [])


def _no_route(run: StageRun, work_dir: Path, answer: Optional[str]) -> Optional[StageResult]:
    """承認ゲートで終わった回への回答に続きが無いときの結果。承認ならプランは終わり、ほかは承認されなかった。続きがあれば None。"""
    routes = stored_gate_routes(work_dir) if answer else None
    if routes is None or plan_runs.from_of(routes[answer] if answer in routes else None) is not None:
        return None
    if answer == APPROVED:
        _, done = plan_runs.previous(work_dir)
        assert done is not None
        outputs = done["outputs"] if isinstance(done.get("outputs"), dict) else {}
        return finished(run, result.result(PLAN, result.OK, f"プラン {run.name} の最後の承認ゲートが承認された", outputs=outputs))
    return StageResult(result.stopped(PLAN, "refused", f"プラン {run.name} の承認ゲートで承認されなかった（回答 {answer}）"), {})


def same_run(before: StageResult, after: StageResult) -> bool:
    """2 つの結果が同じプランの回の結果か（打ち直しても回が変わらず、プランがバックアップの結果を返した）。"""
    run_id = before.report["run_id"] if "run_id" in before.report else None
    return isinstance(run_id, str) and "run_id" in after.report and after.report["run_id"] == run_id


def _from_process(run: StageRun, done: subprocess.CompletedProcess[str]) -> StageResult:
    report = plan_runs.read_report(done.stdout)
    if report is None:
        return bad_report(run, f"終了コード {done.returncode}")
    return finished(run, report)


def _github_kind(exc: github.GitHubError) -> str:
    """GitHub の失敗の停止理由。打ち直せば通りうるもの（不達・5xx・上限）は transient、ほかは precondition。

    分け方は課題の参照の読み手（`ndf.core.refs` の `_unreadable`）と同じ。状態が 0 でも届いて断られた失敗（GraphQL の誤り・
    認証が無い）は precondition。
    """
    return "transient" if exc.passing else "precondition"


# プランを組むときに上がりうる例外（`plan_failure` が停止理由へ分ける型。`_call_plan` と `progress.run._start` が捕まえる）
PLAN_FAILURES = (
    route.RouteError,
    GitError,
    FactUnknown,
    github.GitHubError,
    OSError,
    worker_paths.EntryError,
    internal.InternalError,
    plan_runs.UnitsStopped,
    plan_runs.PrNumberUnrestored,
    durable.StoreStopped,
)


def _carried_failure(name: str, exc: Exception) -> dict[str, Any]:
    """停止理由の語を例外が持つ失敗の `stopped` の結果 JSON。"""
    if isinstance(exc, plan_runs.UnitsStopped):
        # 工程のステートマシンは `cause` だけを残すので、理由は `detail` の文に持たせる（#4369）
        return result.stopped(PLAN, exc.kind, f"プラン {name} を単位ごとに流せない: {exc.detail}", items=exc.items)
    return exc.stopped(PLAN)


def plan_failure(name: str, exc: Exception) -> Optional[dict[str, Any]]:
    """プラン `name` を組めなかった例外の `stopped` の結果 JSON。分類に無い例外は None（呼び手が上げ直す）。

    差し込みと作業の入力の欠け（`TemplateValuesMissing`・`KindInputsMissing`）は値を足して打ち直せば通るので precondition に
    し、名前と出所を `items` に並べる（決定 4）。ほかの分類は工程から呼ぶ経路（`_call_plan`）と起動の前の確かめ
    （`progress.run._start`）で同じである。
    """
    if isinstance(exc, (plan_runs.UnitsStopped, plan_runs.PrNumberUnrestored, durable.StoreStopped)):
        return _carried_failure(name, exc)
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
    if isinstance(exc, worker_paths.EntryError):
        return result.stopped(
            PLAN, "precondition", f"プラン {name} の保存先のファイル（{VALUES_FILE} など）を安全に読み書きできない: {exc}"
        )
    if isinstance(exc, internal.InternalError):
        return result.stopped(PLAN, exc.kind, f"プラン {name} の前の回の結果を読めない: {exc}")
    return None


def _checked_dir(work_dir: Path) -> Path:
    """プランの実行の保存先を開いて（無ければ作って）解いた、ほかの利用者が差し替えられないパス。

    プランの実行は保存先をパスから開き直すので、リンクを経る元のパスでなくこのパスを渡す（リンクを置いた祖先は、解いた
    パスの連鎖を確かめる `worker_paths.check_unshared` に入らず、後で別のディレクトリへ向けられうる）。保存先の中も、ほかの
    利用者が名前を作れず、回の判定に読む状態（回の記録・打ち直しの数・結果のバックアップ）が私有のものであることを確かめる
    （差し替えた前の結果で、流すべきプランを飛ばさない）。
    """
    with worker_paths.open_or_create(work_dir) as wd:
        worker_paths.check_unshared(wd)
        worker_paths.check_owned(wd, (plan_runner.RUN_FILE, ATTEMPT_FILE, plan_runner.RESULTS_DIR))
        record = plan_files.read_json(wd.path / plan_runner.RUN_FILE)
        if record is not None and (wd.path / plan_runner.RESULTS_DIR).is_dir():
            with worker_paths.open_subdir(wd, plan_runner.RESULTS_DIR) as results:
                worker_paths.check_owned(results, (plan_runner.result_path(wd.path, durable.run_name(record["key"])).name,))
        return wd.path


def _call_plan(run: StageRun, cwd: Path, work_dir: Path, answer: Optional[str], runner: Runner) -> StageResult:
    try:
        if _template_of(run) is None:
            return StageResult(result.stopped(PLAN, "precondition", f"プランのテンプレート {run.name} が無い"), {})
        work_dir = _checked_dir(work_dir)  # 以後の読み書きと `--state-dir` は、プランと同じ確かめたディレクトリで行う
        ended = _no_route(run, work_dir, answer)
        if ended is not None:
            return ended
        argv = plan_argv(run, work_dir, answer, cwd)
    except PLAN_FAILURES as exc:
        failed = plan_failure(run.name, exc)
        if failed is None:
            raise
        return StageResult(failed, {})
    got = _from_process(run, runner(argv, cwd, run.env(run.env_extra)))
    if merge_pending.refused(got.report, run.inputs):
        (work_dir / VALUES_FILE).unlink(missing_ok=True)  # 次の打ち直しが方式をもう一度問う
    return got


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
    return str(facts.value(claude.FAILURES, failure, "skill_cause"))


def _call_skill(run: StageRun, cwd: Path, call_claude: SkillCaller) -> StageResult:
    schema = contracts.load_json(contracts.schema_path("result"))
    call = claude.ClaudeCall(system=SKILL_SYSTEM, permission=claude.Permission.FULL, json_schema=schema)
    argv, notice = claude.claude_launch(call, cwd=cwd)
    reply = call_claude(argv, skill_prompt(run), limits.seconds(SKILL_LIMIT), cwd)
    if not reply.ok:
        kind = _skill_cause(reply.failure)
        got = StageResult(result.stopped(SKILL, kind, f"Skill {run.name} を回せない: {reply.error}"), {})
    else:
        report = result.received(reply.answer)
        got = finished(run, report) if report is not None else bad_report(run, "回答が結果 JSON の形に合わない")
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
