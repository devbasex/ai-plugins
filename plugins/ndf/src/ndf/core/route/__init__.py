"""ルートの選び方: リクエストの種類からプランのテンプレートを選んで埋め、止まった後の CI の落ちが同じ原因で続くかを数える。

テンプレートは `contracts/templates/` にある。

- `<名前>.json`: テンプレート 1 本。`purpose`（リクエストの種類。テンプレートどうしで重ならない）・`params`（使う差し込みの名前）・`plan`
  （プランの形。`ndf.core.plan.model` が読む形に `{{名前}}` と `{"use": ...}` を足したもの）を持つ
- `params.json`: 差し込みの名前ごとの意味・既定・並びか（`list`）。意味と既定はここだけが持つ
- `parts/steps.json`: テンプレートどうしで共有するステップ。テンプレートのステップは `{"use": "<名前>", ...}` で 1 つを引き、
  並べたキーでキーごとに上書きする。値が null のキーは消す

差し込み `{{名前}}` はここで埋める。実行のときの差し込み `{名前}`（`{state_dir}` など）はプランの実行（`runner`）が
埋めるので触らない。並びの名前は引数の要素ごとに展開する: 要素がその名前だけなら並びを差し込み、ほかの字を含めば
値ごとに要素を作る（空の並びなら要素ごと消える）。1 つの要素に並びの名前を 2 つ置くと誤り。

サブコマンド `ndf route`:

    ndf route templates
        テンプレートの一覧（名前・リクエストの種類・使う差し込み）
    ndf route render <リクエストの種類> --out <plan.json> [--set <名前>=<値>]... [--add <名前>=<値>]...
        テンプレートを選んで埋め、プランのファイルを書く。`--add` は並びの名前へ値を 1 つ足す。`repo` を省けば打った場所の
        git の根、`worktree` を省けば根の `.worktrees/<branch>` にする（サブモジュールでもそのworktreeの根。#2054）。
        省いた作業場所が使えなければ書かずに止まる。`base` を省けば根のブランチ戦略の設定（`.ndf/project.json` の
        `branch_strategy`）のスプリントの PR のマージ先（`Strategy.pr_target`）にし、設定が無い・旧いキーが残る・スキーマに
        落ちるときは既定ブランチへ倒さずに止まる。`merge_method` を省けば `base` のブランチで許された方式
        （ruleset とリポジトリの設定）の先頭にし、読めなければ無い差し込みとして止まる。`repo` の設定
        （`.ndf/project.json`）がテストを CI に寄せる（`test.ci.only`）なら、手元のテストのステップを外す
    ndf route run <リクエストの種類> --out <plan.json> [--set ...] [--add ...]
        render の後に同じプランを `ndf plan next` で流す（即時修正をテスト → PR → merge-when-green の 1 つの
        エントリポイントで流す）
    ndf route repeat --result <結果 JSON のファイル> --log <記録の JSONL>
        止まった結果の同じ原因のキー（種類と落ちたテストの集まり）を記録へ足し、続いた回数が
        `supervise.step-rerun` の `same_cause` を超えたら `fix_limit` で止まる（人へ戻す）
    ndf route test [--result <結果 JSON のファイル>] -- <コマンド>
        手元のテストのコマンドを `ndf test-slot run` で打つ。シグナル（128 以上）を除く 0 以外の終了コードは
        どれも `test_failed` の止まった結果にし（`--result` があればそこへも書く。`ndf route repeat` が数える）、
        `test-slot` 自身が止まった結果はそのまま返す
    ndf route test --failed-from <CI の結果 JSON のファイル> [--result <結果 JSON のファイル>]
        CI の修正の後の確かめ（#3188）。CI で落ちたテストのファイルだけを、打った場所の設定（`.ndf/project.json`）の
        テストの suite の `scope_command` の `{paths}` へ入れて打つ。落ちたテストを持つ suite だけを打ち、全体テストは
        打たない。落ちたテストが無い・どの suite にも当たらないファイルがあれば打たずに `precondition`、
        CI の結果や設定を読めなければ `invalid`。通ったときも落ちたときも、打ったコマンドと合否を結果 JSON の
        `items` に並べる（`--result` があればそこへも書く）

結果 JSON を標準出力へ 1 行で出す。
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

from ndf import cli, contracts, identity
from ndf.adapters import git, github
from ndf.contracts import causes, exit_codes, limits, result
from ndf.contracts.exit_codes import OK
from ndf.contracts.routing import END
from ndf.core import branch_strategy
from ndf.core.route import local_tests
from ndf.core.plan import cli as plan_cli
from ndf.core.plan import model, steps
from ndf.testing import declared, scoped

TOOL = cli.tool_of(__name__)
# テンプレートの保存先（contracts/ の下）と、テンプレートでない決まった名前のファイル
TEMPLATE_DIR = "templates"
PARAMS_FILE = "params.json"
PARTS_FILE = "parts/steps.json"
# テンプレートのステップで共有のステップを引くキー
USE = "use"
# 手元でテストを打つステップの id。CI テストの設定（`test.ci.only`）があるリポジトリのプランからは外す（#3473）
LOCAL_TEST_STEPS = ("test", "test-all", "ci-local")
# ステップのルートのキー: 1 つのルートを持つキーと、場合 → ルートの対応表を持つキー（ステップを外すときに付け替える）
TARGET_KEYS = ("next", "on_fail")
TARGET_TABLE_KEYS = ("on_cause", "on_answer")
# 同じ原因の続きの上限の設定
REPEAT_LIMIT = "supervise.step-rerun"
# 作業場所を省いたときの保存先（リポジトリの根の下）
WORKTREES = ".worktrees"
_PARAM = re.compile(r"\{\{([a-z_][a-z0-9_]*)\}\}")
# 受け入れ条件の確かめ（`ndf loop acceptance check`）の引数の旗。`--issue` は必ず含め、どちらも後に値を 1 つ持つ
ACCEPTANCE_ISSUE = "--issue"
ACCEPTANCE_FLAGS = (ACCEPTANCE_ISSUE, "--criteria")
# 補い（`launch_values`）が値を作る差し込み → 作るのに読む差し込み。ステップで使わなくても、補いの元としてテンプレートが受ける
LAUNCH_SOURCES = {"acceptance": ("issue",)}
# 設定（`workflow.launch.review`）が無いときの `{{review}}`: 設計のテンプレートは purpose design、ほかは code の
# `ndf cross-review run` を、ステップを打つ時点でブランチの PR の番号を読んで `--pr` に足して打つ。設計のプランは PR を
# プランの中（pr のステップ）で作るため、組む時点に番号が無い。打つ時点で読めば、PR を作った後の番号で組める
REVIEW_STATE = "{state_dir}/cross-review"
REVIEW_WITH_PR = 'n=$(gh pr view "$0" --json number --jq .number) && [ -n "$n" ] || exit 1; exec "$@" --pr "$n"'

Value = Union[str, list[str]]


class RouteError(contracts.ContractError):
    """テンプレートが読めない・形が違う・差し込みの値が欠ける。"""


@dataclass(frozen=True)
class Param:
    name: str
    summary: str
    is_list: bool
    default: Optional[Value]


@dataclass(frozen=True)
class Template:
    name: str
    purpose: str
    summary: str
    params: tuple[str, ...]
    plan: Mapping[str, Any]


# --- テンプレートの読み込み ---------------------------------------------------------------------


def _load(rel: str) -> Any:
    return contracts.load_as(f"{TEMPLATE_DIR}/{rel}", RouteError)


@contracts.cached_reader
def params() -> dict[str, Param]:
    """差し込みの定義（`params.json`）。"""
    raw = _load(PARAMS_FILE)["params"]
    return {n: Param(n, p["summary"], bool(p.get("list")), p.get("default")) for n, p in raw.items()}


@contracts.cached_reader
def parts() -> dict[str, dict[str, Any]]:
    """共有のステップ（`parts/steps.json`）。"""
    return dict(_load(PARTS_FILE)["steps"])


def _template_files() -> list[str]:
    root = contracts.contracts_dir() / TEMPLATE_DIR
    return sorted(p.name for p in root.glob("*.json") if p.name != PARAMS_FILE)


@contracts.cached_reader
def templates() -> dict[str, Template]:
    """テンプレートの一覧（名前 → テンプレート）。形の誤りは `RouteError`。"""
    found: dict[str, Template] = {}
    for rel in _template_files():
        raw = _load(rel)
        t = Template(raw["template"], raw["purpose"], raw["summary"], tuple(raw["params"]), raw["plan"])
        found[t.name] = t
    errs = template_errors(found.values())
    if errs:
        raise RouteError(f"テンプレートの形が違う: {'; '.join(errs)}")
    return found


def choose(purpose: str) -> Template:
    """リクエストの種類からテンプレートを 1 つ選ぶ。無ければ `RouteError`。"""
    for t in templates().values():
        if t.purpose == purpose:
            return t
    known = ", ".join(sorted(t.purpose for t in templates().values()))
    raise RouteError(f"リクエストの種類 {purpose} のテンプレートが無い（{known}）")


# --- 埋める ---------------------------------------------------------------------------


def _expand_step(raw: Mapping[str, Any]) -> dict[str, Any]:
    """`use` の共有のステップに上書きを重ね、値が null のキーを消す。"""
    if USE not in raw:
        return dict(raw)
    shared = parts()
    if raw[USE] not in shared:
        raise RouteError(f"共有のステップに {raw[USE]} が無い")
    merged = {**copy.deepcopy(shared[raw[USE]]), **{k: v for k, v in raw.items() if k != USE}}
    return {k: v for k, v in merged.items() if v is not None}


def expanded(t: Template, *, local_tests: bool = True) -> dict[str, Any]:
    """`use` を開いたプランの形（差し込みは埋めない）。`local_tests` が偽なら手元のテストのステップを外す。"""
    plan = copy.deepcopy(dict(t.plan))
    plan["steps"] = [_expand_step(s) for s in plan["steps"]]
    return plan if local_tests else without_steps(plan, LOCAL_TEST_STEPS)


def _successors(found: Sequence[Mapping[str, Any]], drop: frozenset[str]) -> dict[str, str]:
    """外すステップの id → 代わりのルート（そのステップの `next`、無ければ並びの次。外すステップが続けば辿る）。"""
    ids = [str(s["id"]) for s in found]
    following = [*ids[1:], END]
    plain = {i: str(s["next"]) if "next" in s else after for i, s, after in zip(ids, found, following)}
    out: dict[str, str] = {}
    for i in (x for x in ids if x in drop):
        target = plain[i]
        while target in drop and target != i:
            target = plain[target]
        out[i] = target
    return out


def _moved(target: Any, moved: Mapping[str, str]) -> Any:
    """ルート `target` が外したステップなら代わりのルート、そうでなければそのまま。"""
    return moved[target] if target in moved else target


def _retarget(step: Mapping[str, Any], moved: Mapping[str, str]) -> dict[str, Any]:
    """ステップのルート（`next`・`on_fail`・`on_cause`・`on_answer`）のうち外したステップを代わりのルートへ置き換える。"""
    out = dict(step)
    for key in TARGET_KEYS:
        if key in out:
            out[key] = _moved(out[key], moved)
    for key in TARGET_TABLE_KEYS:
        if isinstance(out.get(key), Mapping):
            out[key] = {k: _moved(v, moved) for k, v in out[key].items()}
    return out


def without_steps(plan: Mapping[str, Any], ids: Iterable[str]) -> dict[str, Any]:
    """プランから `ids` のステップを外し、そこへ向かうルートを外したステップの続きへ付け替える。"""
    drop = frozenset(ids)
    found = list(plan["steps"])
    moved = _successors(found, drop)
    return {**plan, "steps": [_retarget(s, moved) for s in found if s["id"] not in drop]}


def used_names(value: Any) -> set[str]:
    """値の中の `{{名前}}` の名前。"""
    if isinstance(value, str):
        return set(_PARAM.findall(value))
    if isinstance(value, (Mapping, list)):
        children = value.values() if isinstance(value, Mapping) else value
        return set().union(*(used_names(v) for v in children))
    return set()


def _fill_text(text: str, values: Mapping[str, Value]) -> str:
    def one(m: re.Match) -> str:
        value = values[m.group(1)]
        if isinstance(value, list):
            raise RouteError(f"並びの差し込み {m.group(1)} は引数の並びの中にだけ置ける: {text}")
        return value

    return _PARAM.sub(one, text)


def _expand_list_item(item: str, name: str, values: Mapping[str, Value]) -> list[str]:
    """並びの名前 `name` を含む要素を展開する。要素がその名前だけなら並びを差し込み、ほかの字を含めば値ごとに作る。"""
    if item == "{{" + name + "}}":
        return list(values[name])
    return [_fill_text(item.replace("{{" + name + "}}", v), values) for v in values[name]]


def _fill_item(item: Any, values: Mapping[str, Value]) -> list[Any]:
    """並びの要素 1 つを埋め、展開した要素の並びを返す。"""
    if not isinstance(item, str):
        return [_fill(item, values)]
    lists = [n for n in dict.fromkeys(_PARAM.findall(item)) if isinstance(values[n], list)]
    if not lists:
        return [_fill_text(item, values)]
    if len(lists) > 1:
        raise RouteError(f"1 つの要素に並びの差し込みが 2 つある: {item}")
    return _expand_list_item(item, lists[0], values)


def _fill(value: Any, values: Mapping[str, Value]) -> Any:
    if isinstance(value, str):
        return _fill_text(value, values)
    if isinstance(value, Mapping):
        return {k: _fill(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [x for item in value for x in _fill_item(item, values)]
    return value


def _check_unknown(t: Template, given: Mapping[str, Value]) -> None:
    unknown = sorted(set(given) - set(t.params))
    if unknown:
        raise RouteError(f"テンプレート {t.name} に無い差し込み: {', '.join(unknown)}")


def _value_of(name: str, given: Mapping[str, Value], defs: Mapping[str, Param]) -> Optional[Value]:
    """差し込み 1 つの値（与えた値 → 既定）。欠けていれば None、並びかの食い違いは `RouteError`。"""
    p = defs[name]
    value = given[name] if name in given else p.default
    if value is not None and isinstance(value, list) != p.is_list:
        raise RouteError(f"差し込み {name} は{'並び' if p.is_list else '文字列'}で渡す")
    return value


def with_sources(used: set[str], given: Collection[str] = ()) -> set[str]:
    """使う差し込みに、補い（`LAUNCH_SOURCES`）の元の差し込みを足す。値を与えた差し込みは補わないので、その元は足さない。"""
    return used.union(*(LAUNCH_SOURCES[n] for n in used if n in LAUNCH_SOURCES and n not in given))


def needed(t: Template, *, local_tests: bool = True, given: Collection[str] = ()) -> tuple[str, ...]:
    """組むプランが使う差し込みの名前と補いの元（手元のテストのステップを外せば、そこだけが使う名前は要らない）。

    補いの元は、補う差し込みの値を `given` に持たないときだけ求める（`acceptance` を渡した課題を束ねた単位は、単独の
    `issue` を持たなくても組める。PR 3888 の指摘）。
    """
    used = with_sources(used_names(expanded(t, local_tests=local_tests)), given)
    return tuple(n for n in t.params if n in used)


def resolve(t: Template, given: Mapping[str, Value], *, local_tests: bool = True) -> dict[str, Value]:
    """差し込みの値を決める（与えた値 → 既定）。テンプレートに無い名前・欠けた値・並びかの食い違いは `RouteError`。"""
    defs = params()
    _check_unknown(t, given)
    found = {name: _value_of(name, given, defs) for name in needed(t, local_tests=local_tests, given=given)}
    values = {name: value for name, value in found.items() if value is not None}
    missing = [name for name, value in found.items() if value is None]
    if missing:
        raise RouteError(f"テンプレート {t.name} の差し込みの値が無い: {', '.join(missing)}")
    return values


def render(t: Template, given: Mapping[str, Value], *, local_tests: bool = True) -> dict[str, Any]:
    """テンプレートを埋めたプラン（`model.parse` を通る形）。誤りは `RouteError`。

    `local_tests` が偽（CI テストの設定 `test.ci.only` があるリポジトリ）なら、手元でテストを打つステップ
    （`LOCAL_TEST_STEPS`）を外して組む。テストは CI の結果で確かめる。
    """
    plan = _fill(expanded(t, local_tests=local_tests), resolve(t, given, local_tests=local_tests))
    try:
        model.parse(plan)
    except model.PlanError as exc:
        raise RouteError(f"テンプレート {t.name} から組んだプランが誤り: {exc}") from None
    return plan


# --- テンプレートの検査 -----------------------------------------------------------------------


def _sample(names: Sequence[str]) -> dict[str, Value]:
    defs = params()
    return {n: ([f"<{n}>"] if defs[n].is_list else f"<{n}>") for n in names}


def _param_name_errors(t: Template, used: set[str]) -> list[str]:
    errs = [f"テンプレート {t.name} が params に無い差し込みを使う: {n}" for n in sorted(used - set(t.params))]
    errs += [f"テンプレート {t.name} が使わない差し込みを params に持つ: {n}" for n in sorted(set(t.params) - used)]
    return errs


def _sample_render_errors(t: Template) -> list[str]:
    """試しの値で、手元のテストのステップを持つ形と外した形の両方を組む。"""
    try:
        render(t, _sample(t.params))
        render(t, _sample(t.params), local_tests=False)
    except RouteError as exc:
        return [str(exc)]
    return []


def _one_template_errors(t: Template, defs: Mapping[str, Param]) -> list[str]:
    errs = [f"テンプレート {t.name} の params に定義の無い名前: {n}" for n in t.params if n not in defs]
    if errs:
        return errs
    try:
        used = with_sources(used_names(expanded(t)))
    except RouteError as exc:
        return [f"テンプレート {t.name}: {exc}"]
    errs = _param_name_errors(t, used)
    if errs:
        return errs
    return _sample_render_errors(t)


def template_errors(found: Iterable[Template]) -> list[str]:
    """テンプレートの誤り: 定義の無い差し込み・使わない差し込み・リクエストの種類の重なり・組んだプランの誤り。"""
    found = list(found)
    defs = params()
    purposes = [t.purpose for t in found]
    errs = [f"リクエストの種類が重なる: {p}" for p in contracts.duplicates(purposes)]
    for t in found:
        errs += _one_template_errors(t, defs)
    return errs


# --- 同じ原因の続き ---------------------------------------------------------------------


def cause_key(found: Mapping[str, Any]) -> Optional[str]:
    """止まった結果の同じ原因のキー（種類と落ちたテストの集まり）。止まっていなければ None。文（summary）を読まない。"""
    kind = causes.kind_of(found.get("cause"))
    if found.get("status") != exit_codes.STOPPED or kind is None:
        return None
    return json.dumps([kind, sorted(local_tests.failed_tests_in(found))], ensure_ascii=False)


def streak(history: Sequence[str], key: str) -> int:
    """記録の末尾から `key` が続いた回数。"""
    count = 0
    for past in reversed(history):
        if past != key:
            break
        count += 1
    return count


def repeated(history: Sequence[str], key: str, same_cause: int) -> bool:
    """`key` を足すと続いた回数が `same_cause` を超えるか。"""
    return streak(history, key) + 1 > same_cause


# --- サブコマンド ---------------------------------------------------------------------------


def repo_root(cwd: Path) -> str:
    """打った場所の git のworktreeの根（サブモジュールならそのworktree。共通の git ディレクトリから求めない）。"""
    return git.Git(cwd).run("rev-parse", "--show-toplevel").strip()


def default_base(repo: Path) -> str:
    """マージ先のブランチの既定: ブランチ戦略の設定（`repo` の `.ndf/project.json` の `branch_strategy`）のスプリントの PR のマージ先。

    設定が無い・旧いキーが残る・スキーマに落ちるときは `RouteError`（理由に設定の位置と打つコマンドを載せる）。
    """
    try:
        return branch_strategy.load(repo).pr_target("sprint")
    except branch_strategy.StrategyError as exc:
        raise RouteError(f"base を決められない: {exc.reason}") from None


def default_merge_method(repo: Path, base: str, remote: Optional[str] = None, *, raise_passing: bool = False) -> Optional[str]:
    """マージの方式の既定: 基底のブランチ `base` で許された方式の先頭（`github.pick_merge_method`）。読めなければ None。

    `remote` はリポジトリを読むリモート名（`--set remote=` で渡した値）。省けばこの作業場所のリモート名。
    `raise_passing` が真なら、打ち直せば通りうる GitHub の失敗（`GitHubError.passing`。上限・不達・5xx）は None にせず
    そのまま上げる（呼び手が待ち時間付きの一時的な停止にし、方式が無い場合と分ける）。
    """
    try:
        hub = github.GitHub(git.Git(repo).remote_repo(remote), github.client())
        allowed = hub.allowed_merge_methods(base)
    except github.GitHubError as exc:
        if raise_passing and exc.passing:
            raise
        return None
    except git.GitError:  # noqa: NDF401 外部コマンド: リモートを読めない
        return None
    return github.pick_merge_method(None, allowed)


def _given_remote(out: Mapping[str, Value]) -> Optional[str]:
    """決めた（渡したか求めた）リモート名。base と merge_method もこのリモートから決める（push 先とそろえる）。"""
    remote = out.get("remote")
    return remote if isinstance(remote, str) and remote else None


def _remote_of_repo(repo: Path) -> str:
    """省いた `remote` の値（I7: 設定の `remote`、無ければスキーマの既定）。誤りは `RouteError`。

    git の作業場所では、linked worktree から打ってもメインディレクトリの設定を読む（`Git.remote`。取得・レビューの経路と
    同じリモート）。git の作業場所でない場所（`.git` が無い）にはメインディレクトリが無いため、git を起動せず `repo` の設定を読む。
    """
    try:
        if not (repo / ".git").exists():
            return git.remote_of(repo)
        return git.Git(repo).remote()
    except git.GitError as exc:
        raise RouteError(str(exc)) from None


def review_launch(template_name: str, branch: str, base: str) -> list[str]:
    """設定が無いときの `{{review}}` の値（`REVIEW_WITH_PR`）。PR の番号は打つ時点にブランチから読んで `--pr` に足す。"""
    purpose = "design" if template_name == "design" else "code"
    entry = identity.FIXED_WORDS["entrypoint"]
    review = [entry, "cross-review", "run", "--state-dir", REVIEW_STATE, "--base", base, "--purpose", purpose]
    return ["sh", "-c", REVIEW_WITH_PR, branch, *review]


def _review_value(t: Template, values: Mapping[str, Value]) -> Optional[list[str]]:
    """設定が無いときの `{{review}}` の値（ブランチとマージ先が決まらなければ None）。

    テンプレートが `base` を持たない（`cross-review` など）ときは、マージ先を `default_base` から決める（設定を読めなければ
    `RouteError`）。`derived` はテンプレートの params にある値しか補わないため（PR 3537 の指摘）。実装のテンプレートは単位の
    PR をレビューするので、起点を取得し直したリモートのマージ先（`<remote>/<base>`）にする（#3879 決定 2）。
    """
    branch, base, repo = values.get("branch"), values.get("base"), values.get("repo")
    if base is None and isinstance(repo, str) and repo:
        base = default_base(Path(repo))
    if not isinstance(branch, str) or not isinstance(base, str) or not branch or not base:
        return None
    if t.name == "impl":
        base = f"{_given_remote(values) or git.default_remote()}/{base}"
    return review_launch(t.name, branch, base)


def launch_values(t: Template, values: Mapping[str, Value]) -> dict[str, Value]:
    """テンプレートが使う差し込みのうち値の無い `review`・`acceptance` の補い（単発の経路と工程から組む経路で共有する）。

    `review` は `review_launch` で組み、`acceptance` は `issue` があれば `--issue <issue>`（課題の条件をすべて照らす）。
    決まらない差し込みは含めない。
    """
    out: dict[str, Value] = {}
    if _omitted(t, values, "review"):
        review = _review_value(t, values)
        if review is not None:
            out["review"] = review
    issue = values.get("issue")
    if _omitted(t, values, "acceptance") and isinstance(issue, str) and issue:
        out["acceptance"] = [ACCEPTANCE_ISSUE, issue]
    return out


def check_acceptance(values: Mapping[str, Value]) -> None:
    """渡した `acceptance` の並びに `--issue` が無いか、`--issue`・`--criteria` の後に値が無ければ `RouteError`。

    既定の補いは値を渡さなかったときだけ働くため、`--issue` の欠けた並びはプランを組む時点に断る（#3879 決定 6）。
    """
    found = values.get("acceptance")
    if not isinstance(found, list):
        return
    if ACCEPTANCE_ISSUE not in found:
        raise RouteError(f"acceptance に {ACCEPTANCE_ISSUE} が無い: {' '.join(found)}")
    for i, arg in enumerate(found):
        if arg in ACCEPTANCE_FLAGS and (i + 1 == len(found) or found[i + 1].startswith("--")):
            raise RouteError(f"acceptance の {arg} の後に値が無い: {' '.join(found)}")


def launched(t: Template, values: Mapping[str, Value]) -> tuple[dict[str, Value], dict[str, Value]]:
    """`values` に起動の補い（`launch_values`）を足して `acceptance` を検査（`check_acceptance`）した値と、補った値。

    単発の経路（`_write_plan`）と工程から組む経路（`ndf.progress.invoke.template_values`）で共有する。補った値は、
    `{{review}}` が既定の起動かを呼び手が見分けるために返す。検査に通らなければ `RouteError`。
    """
    filled = launch_values(t, values)
    out = {**values, **filled}
    check_acceptance(out)
    return out, filled


def _omitted(t: Template, out: Mapping[str, Value], name: str) -> bool:
    """テンプレートが差し込み `name` を使い、まだ値が無いか。"""
    return name in t.params and name not in out


def derived(t: Template, given: Mapping[str, Value], cwd: Path) -> dict[str, Value]:
    """省いた `repo`・`worktree`・`remote`・`base`・`merge_method` を打った場所から決めて足す（決まらなければ足さない。
    `base` はブランチ戦略の設定から決め、決まらなければ `RouteError`）。"""
    return derived_merge_method(t, derived_locally(t, given, cwd))


def derived_locally(t: Template, given: Mapping[str, Value], cwd: Path) -> dict[str, Value]:
    """`derived` のうち手元（git と設定ファイル）だけで決まる `repo`・`remote`・`worktree`・`base` を足す。GitHub を呼ばない。"""
    out = dict(given)
    if _omitted(t, out, "repo"):
        out["repo"] = repo_root(cwd)
    if _omitted(t, out, "remote") and "repo" in out:
        out["remote"] = _remote_of_repo(Path(str(out["repo"])))
    if _omitted(t, out, "worktree") and isinstance(out.get("branch"), str):
        out["worktree"] = str(Path(str(out["repo"])) / WORKTREES / out["branch"])
    if _omitted(t, out, "base") and "repo" in out:
        out["base"] = default_base(Path(str(out["repo"])))
    return out


def derived_merge_method(t: Template, given: Mapping[str, Value], *, raise_passing: bool = False) -> dict[str, Value]:
    """省いた `merge_method` を `base` のブランチで許された方式から決めて足す（GitHub を呼ぶ。読めなければ足さない）。

    `raise_passing` は `default_merge_method` へ渡す（真なら打ち直せば通りうる GitHub の失敗を上げる）。
    """
    out = dict(given)
    if _omitted(t, out, "merge_method") and "repo" in out and isinstance(out.get("base"), str):
        method = default_merge_method(Path(str(out["repo"])), str(out["base"]), _given_remote(out), raise_passing=raise_passing)
        if method is not None:
            out["merge_method"] = method
    return out


def _check_worktree(t: Template, given: Mapping[str, Value], values: Mapping[str, Value]) -> None:
    """省いて決めた作業場所が使えなければ `RouteError`（ランナーがリポジトリの根へ倒れて別のチェックアウトを変えないため）。"""
    if "worktree" in t.params and "worktree" not in given and "worktree" in values:
        location = str(values["worktree"])
        if not steps.usable_worktree(location):
            raise RouteError(f"省いた作業場所 {location} が使える worktree でない。作るか --set worktree=<path> で渡す")


def _split(flag: str, pair: str) -> tuple[str, str]:
    name, sep, value = pair.partition("=")
    if not sep or not name:
        raise RouteError(f"{flag} は <名前>=<値> で渡す: {pair}")
    return name, value


def _pairs(sets: Optional[list[str]], adds: Optional[list[str]]) -> dict[str, Value]:
    """`--set`（文字列）と `--add`（並びへ 1 つ足す）の値。同じ名前を両方で渡せば `RouteError`。"""
    values: dict[str, Value] = dict(_split("--set", pair) for pair in (sets if sets is not None else []))
    lists: dict[str, list[str]] = {}
    for pair in adds if adds is not None else []:
        name, value = _split("--add", pair)
        lists.setdefault(name, []).append(value)
    both = sorted(set(values) & set(lists))
    if both:
        raise RouteError(f"--set と --add の両方で渡した: {', '.join(both)}")
    return {**values, **lists}


def _declared_ci_only(values: Mapping[str, Value]) -> bool:
    """埋める先のリポジトリ（`repo`、無ければ打った場所）の設定がテストを CI に寄せる（`test.ci.only` が真）か。

    工程から組むプラン（`ndf.progress.invoke.plan_path`）と同じく、設定があれば手元のテストのステップを外す（PR 3590 の指摘）。
    """
    repo = values.get("repo")
    location = Path(repo) if isinstance(repo, str) and repo else Path.cwd()
    return scoped.ci_only(declared.test_section(location))


def _write_plan(a: argparse.Namespace) -> tuple[Template, Path]:
    t = choose(a.purpose)
    given = _pairs(a.sets, a.adds)
    values, _ = launched(t, derived(t, given, Path.cwd()))
    _check_worktree(t, given, values)
    plan = render(t, values, local_tests=not _declared_ci_only(values))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return t, out


def _cmd_render(a: argparse.Namespace) -> int:
    t, out = _write_plan(a)
    item = {"kind": "path", "path": str(out)}
    nxt = result.command("plan", "next", str(out))
    return result.emit(result.result(TOOL, OK, f"テンプレート {t.name} からプラン {out} を書いた", [item], next=nxt))


def _cmd_run(a: argparse.Namespace) -> int:
    _, out = _write_plan(a)
    return plan_cli.main(["next", str(out)])


def _cmd_templates(_: argparse.Namespace) -> int:
    found = templates().values()
    items = [{"kind": "template", "name": t.name, "purpose": t.purpose, "params": list(t.params)} for t in found]
    return result.emit(result.result(TOOL, OK, f"テンプレートが {len(items)} 本ある", items, {"templates": len(items)}))


def _read_lines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return [json.loads(line)["key"] for line in text.splitlines() if line.strip()]


def _invalid(detail: str) -> int:
    return result.emit(result.stopped(TOOL, "invalid", detail))


def _stopped_key(a: argparse.Namespace) -> str:
    """結果ファイルの同じ原因のキー。読めない・止まった結果でなければ `RouteError`。"""
    found = local_tests.read_result(a.result, "結果", RouteError)
    key = cause_key(found) if found is not None else None
    if key is None:
        raise RouteError(f"結果 {a.result} は止まった結果でない")
    return key


def _append_key(log: Path, key: str) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"key": key}, ensure_ascii=False) + "\n")


def _cmd_repeat(a: argparse.Namespace) -> int:
    try:
        key = _stopped_key(a)
    except RouteError as exc:
        return _invalid(str(exc))
    log = Path(a.log)
    history = _read_lines(log)
    same_cause = int(limits.value(REPEAT_LIMIT, "same_cause"))
    count = streak(history, key) + 1
    _append_key(log, key)
    metrics = {"streak": count, "same_cause": same_cause}
    if repeated(history, key, same_cause):
        detail = f"同じ原因が {count} 回続いた（上限 {same_cause}）: {key}"
        return result.emit(result.stopped(TOOL, "fix_limit", detail, instance=key, metrics=metrics))
    return result.emit(result.result(TOOL, OK, f"同じ原因は {count} 回目（上限 {same_cause}）", metrics=metrics))


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ndf route", description="プランのテンプレートを選んで埋める・同じ原因の続きを数える")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("templates", help="テンプレートの一覧").set_defaults(fn=_cmd_templates)
    for name, fn, text in (("render", _cmd_render, "テンプレートを選んで埋める"), ("run", _cmd_run, "埋めたプランを流す")):
        r = sub.add_parser(name, help=text)
        r.add_argument("purpose")
        r.add_argument("--out", required=True)
        r.add_argument("--set", dest="sets", action="append")
        r.add_argument("--add", dest="adds", action="append")
        r.set_defaults(fn=fn)
    rp = sub.add_parser("repeat", help="同じ原因の続きを数える")
    rp.add_argument("--result", required=True)
    rp.add_argument("--log", required=True)
    rp.set_defaults(fn=_cmd_repeat)
    tp = sub.add_parser("test", help="手元のテストを打ち、落ちを test_failed にする")
    tp.add_argument("--result")
    tp.add_argument("--failed-from")
    tp.add_argument("command", nargs="?")
    tp.set_defaults(fn=local_tests.cmd_test)
    return p


def main(argv: list[str]) -> int:
    try:
        a = _parser().parse_args(argv)
    except SystemExit as exc:
        return causes.exit_code("invalid") if exc.code else exit_codes.code_of("ok")
    try:
        return a.fn(a)
    except RouteError as exc:
        return _invalid(str(exc))
    except git.GitError as exc:
        return result.emit(result.stopped(TOOL, "precondition", str(exc)))
