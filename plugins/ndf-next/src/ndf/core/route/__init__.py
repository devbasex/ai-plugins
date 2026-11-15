"""行き先の選び方: 依頼の種類からプランの雛形を選んで埋め、止まった後の CI の落ちが同じ原因で続くかを数える。

雛形は `contracts/templates/` にある。

- `<名前>.json`: 雛形 1 本。`purpose`（依頼の種類。雛形どうしで重ならない）・`params`（使う差し込みの名前）・`plan`
  （プランの形。`ndf.core.plan.model` が読む形に `{{名前}}` と `{"use": ...}` を足したもの）を持つ
- `params.json`: 差し込みの名前ごとの意味・既定・並びか（`list`）。意味と既定はここだけが持つ
- `parts/steps.json`: 雛形どうしで共有するステップ。雛形のステップは `{"use": "<名前>", ...}` で 1 つを引き、
  並べた鍵で鍵ごとに上書きする。値が null の鍵は消す

差し込み `{{名前}}` はここで埋める。実行のときの差し込み `{名前}`（`{state_dir}` など）はプランの実行（`runner`）が
埋めるので触らない。並びの名前は引数の要素ごとに展開する: 要素がその名前だけなら並びを差し込み、ほかの字を含めば
値ごとに要素を作る（空の並びなら要素ごと消える）。1 つの要素に並びの名前を 2 つ置くと誤り。

副命令 `ndf route`:

    ndf route templates
        雛形の一覧（名前・依頼の種類・使う差し込み）
    ndf route render <依頼の種類> --out <plan.json> [--set <名前>=<値>]... [--add <名前>=<値>]...
        雛形を選んで埋め、プランのファイルを書く。`--add` は並びの名前へ値を 1 つ足す。`repo` を省けば打った場所の
        git の根、`worktree` を省けば根の `.worktrees/<branch>` にする（サブモジュールでもそのworktreeの根。#2054）。
        省いた作業場所が使えなければ書かずに止まる。`base` を省けば根の `.ndf/worktree.json` の `base_branch`、
        宣言が無ければリモート名（`.ndf/worktree.json` の `remote`）のリモートの既定ブランチにする。`merge_method` を省けば `base` のブランチで許された方式
        （ruleset とリポジトリの設定）の先頭にし、読めなければ無い差し込みとして止まる。`repo` の宣言
        （`.ndf/project.json`）がテストを CI に寄せる（`test.ci.only`）なら、手元のテストのステップを外す
    ndf route run <依頼の種類> --out <plan.json> [--set ...] [--add ...]
        render の後に同じプランを `ndf plan next` で流す（即時修正をテスト → PR → merge-when-green の 1 つの
        エントリポイントで流す）
    ndf route repeat --result <結果 JSON のファイル> --log <記録の JSONL>
        止まった結果の同じ原因の鍵（種類と落ちたテストの集まり）を記録へ足し、続いた回数が
        `supervise.step-rerun` の `same_cause` を超えたら `fix_limit` で止まる（人へ戻す）
    ndf route test [--result <結果 JSON のファイル>] -- <コマンド>
        手元のテストのコマンドを `ndf test-slot run` で打つ。シグナル（128 以上）を除く 0 以外の終了コードは
        どれも `test_failed` の止まった結果にし（`--result` があればそこへも書く。`ndf route repeat` が数える）、
        `test-slot` 自身が止まった結果はそのまま返す
    ndf route test --failed-from <CI の結果 JSON のファイル> [--result <結果 JSON のファイル>]
        CI の修正の後の確かめ（#3188）。CI で落ちたテストのファイルだけを、打った場所の宣言（`.ndf/project.json`）の
        テストの suite の `scope_command` の `{paths}` へ入れて打つ。落ちたテストを持つ suite だけを打ち、全体テストは
        打たない。落ちたテストが無い・どの suite にも当たらないファイルがあれば打たずに `precondition`、
        CI の結果や宣言を読めなければ `invalid`。通ったときも落ちたときも、打ったコマンドと合否を結果 JSON の
        `items` に並べる（`--result` があればそこへも書く）

結果 JSON を標準出力へ 1 行で出す。
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

from ndf import cli, contracts
from ndf.adapters import git, github
from ndf.contracts import causes, exit_codes, limits, result
from ndf.contracts.exit_codes import OK
from ndf.contracts.routing import END
from ndf.core.route import local_tests
from ndf.core.plan import cli as plan_cli
from ndf.core.plan import model, steps
from ndf.testing import declared, scoped

TOOL = cli.tool_of(__name__)
# 雛形の置き場（contracts/ の下）と、雛形でない決まった名前のファイル
TEMPLATE_DIR = "templates"
PARAMS_FILE = "params.json"
PARTS_FILE = "parts/steps.json"
# 雛形のステップで共有のステップを引く鍵
USE = "use"
# 手元でテストを打つステップの id。CI テストの宣言（`test.ci.only`）があるリポジトリのプランからは外す（#3473）
LOCAL_TEST_STEPS = ("test", "test-all", "ci-local")
# ステップの行き先の鍵: 1 つの行き先を持つ鍵と、場合 → 行き先の対応表を持つ鍵（ステップを外すときに付け替える）
TARGET_KEYS = ("next", "on_fail")
TARGET_TABLE_KEYS = ("on_cause", "on_answer")
# 同じ原因の続きの上限の宣言
REPEAT_LIMIT = "supervise.step-rerun"
# 作業場所を省いたときの置き場（リポジトリの根の下）
WORKTREES = ".worktrees"
# base を省いたときに読むブランチの運用の宣言（リポジトリの根の下）と、その鍵
BRANCH_DECLARATION = git.WORKTREE_DECLARATION
BASE_KEY = "base_branch"
_PARAM = re.compile(r"\{\{([a-z_][a-z0-9_]*)\}\}")

Value = Union[str, list[str]]


class RouteError(contracts.ContractError):
    """雛形が読めない・形が違う・差し込みの値が欠ける。"""


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


# --- 雛形の読み込み ---------------------------------------------------------------------


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
    """雛形の一覧（名前 → 雛形）。形の誤りは `RouteError`。"""
    found: dict[str, Template] = {}
    for rel in _template_files():
        raw = _load(rel)
        t = Template(raw["template"], raw["purpose"], raw["summary"], tuple(raw["params"]), raw["plan"])
        found[t.name] = t
    errs = template_errors(found.values())
    if errs:
        raise RouteError(f"雛形の形が違う: {'; '.join(errs)}")
    return found


def choose(purpose: str) -> Template:
    """依頼の種類から雛形を 1 つ選ぶ。無ければ `RouteError`。"""
    for t in templates().values():
        if t.purpose == purpose:
            return t
    known = ", ".join(sorted(t.purpose for t in templates().values()))
    raise RouteError(f"依頼の種類 {purpose} の雛形が無い（{known}）")


# --- 埋める ---------------------------------------------------------------------------


def _expand_step(raw: Mapping[str, Any]) -> dict[str, Any]:
    """`use` の共有のステップに上書きを重ね、値が null の鍵を消す。"""
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
    """外すステップの id → 代わりの行き先（そのステップの `next`、無ければ並びの次。外すステップが続けば辿る）。"""
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
    """行き先 `target` が外したステップなら代わりの行き先、そうでなければそのまま。"""
    return moved[target] if target in moved else target


def _retarget(step: Mapping[str, Any], moved: Mapping[str, str]) -> dict[str, Any]:
    """ステップの行き先（`next`・`on_fail`・`on_cause`・`on_answer`）のうち外したステップを代わりの行き先へ置き換える。"""
    out = dict(step)
    for key in TARGET_KEYS:
        if key in out:
            out[key] = _moved(out[key], moved)
    for key in TARGET_TABLE_KEYS:
        if isinstance(out.get(key), Mapping):
            out[key] = {k: _moved(v, moved) for k, v in out[key].items()}
    return out


def without_steps(plan: Mapping[str, Any], ids: Iterable[str]) -> dict[str, Any]:
    """プランから `ids` のステップを外し、そこへ向かう行き先を外したステップの続きへ付け替える。"""
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
        raise RouteError(f"雛形 {t.name} に無い差し込み: {', '.join(unknown)}")


def _value_of(name: str, given: Mapping[str, Value], defs: Mapping[str, Param]) -> Optional[Value]:
    """差し込み 1 つの値（与えた値 → 既定）。欠けていれば None、並びかの食い違いは `RouteError`。"""
    p = defs[name]
    value = given[name] if name in given else p.default
    if value is not None and isinstance(value, list) != p.is_list:
        raise RouteError(f"差し込み {name} は{'並び' if p.is_list else '文字列'}で渡す")
    return value


def needed(t: Template, *, local_tests: bool = True) -> tuple[str, ...]:
    """組むプランが使う差し込みの名前（手元のテストのステップを外せば、そこだけが使う名前は要らない）。"""
    used = used_names(expanded(t, local_tests=local_tests))
    return tuple(n for n in t.params if n in used)


def resolve(t: Template, given: Mapping[str, Value], *, local_tests: bool = True) -> dict[str, Value]:
    """差し込みの値を決める（与えた値 → 既定）。雛形に無い名前・欠けた値・並びかの食い違いは `RouteError`。"""
    defs = params()
    _check_unknown(t, given)
    found = {name: _value_of(name, given, defs) for name in needed(t, local_tests=local_tests)}
    values = {name: value for name, value in found.items() if value is not None}
    missing = [name for name, value in found.items() if value is None]
    if missing:
        raise RouteError(f"雛形 {t.name} の差し込みの値が無い: {', '.join(missing)}")
    return values


def render(t: Template, given: Mapping[str, Value], *, local_tests: bool = True) -> dict[str, Any]:
    """雛形を埋めたプラン（`model.parse` を通る形）。誤りは `RouteError`。

    `local_tests` が偽（CI テストの宣言 `test.ci.only` があるリポジトリ）なら、手元でテストを打つステップ
    （`LOCAL_TEST_STEPS`）を外して組む。テストは CI の結果で確かめる。
    """
    plan = _fill(expanded(t, local_tests=local_tests), resolve(t, given, local_tests=local_tests))
    try:
        model.parse(plan)
    except model.PlanError as exc:
        raise RouteError(f"雛形 {t.name} から組んだプランが誤り: {exc}") from None
    return plan


# --- 雛形の検査 -----------------------------------------------------------------------


def _sample(names: Sequence[str]) -> dict[str, Value]:
    defs = params()
    return {n: ([f"<{n}>"] if defs[n].is_list else f"<{n}>") for n in names}


def _param_name_errors(t: Template, used: set[str]) -> list[str]:
    errs = [f"雛形 {t.name} が params に無い差し込みを使う: {n}" for n in sorted(used - set(t.params))]
    errs += [f"雛形 {t.name} が使わない差し込みを params に持つ: {n}" for n in sorted(set(t.params) - used)]
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
    errs = [f"雛形 {t.name} の params に定義の無い名前: {n}" for n in t.params if n not in defs]
    if errs:
        return errs
    try:
        used = used_names(expanded(t))
    except RouteError as exc:
        return [f"雛形 {t.name}: {exc}"]
    errs = _param_name_errors(t, used)
    if errs:
        return errs
    return _sample_render_errors(t)


def template_errors(found: Iterable[Template]) -> list[str]:
    """雛形の誤り: 定義の無い差し込み・使わない差し込み・依頼の種類の重なり・組んだプランの誤り。"""
    found = list(found)
    defs = params()
    purposes = [t.purpose for t in found]
    errs = [f"依頼の種類が重なる: {p}" for p in contracts.duplicates(purposes)]
    for t in found:
        errs += _one_template_errors(t, defs)
    return errs


# --- 同じ原因の続き ---------------------------------------------------------------------


def cause_key(found: Mapping[str, Any]) -> Optional[str]:
    """止まった結果の同じ原因の鍵（種類と落ちたテストの集まり）。止まっていなければ None。文（summary）を読まない。"""
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


# --- 副命令 ---------------------------------------------------------------------------


def repo_root(cwd: Path) -> str:
    """打った場所の git のworktreeの根（サブモジュールならそのworktree。共通の git ディレクトリから求めない）。"""
    return git.Git(cwd).run("rev-parse", "--show-toplevel").strip()


def default_base(repo: Path, remote: Optional[str] = None) -> Optional[str]:
    """宛先のブランチの既定: ブランチの運用の宣言の `base_branch`、無ければリモートの既定ブランチ（決まらなければ None）。

    `remote` は既定ブランチを読むリモート名（`--set remote=` で渡した値）。省けばこの作業場所のリモート名。
    """
    try:
        declared = json.loads((repo / BRANCH_DECLARATION).read_text(encoding="utf-8")).get(BASE_KEY)
    except (OSError, ValueError, AttributeError):
        declared = None
    if isinstance(declared, str) and declared:
        return declared
    try:
        return git.Git(repo).remote_default_branch(remote)
    except git.GitError:  # noqa: NDF401 外部コマンド: 宣言の remote が誤り
        return None


def default_merge_method(repo: Path, base: str, remote: Optional[str] = None) -> Optional[str]:
    """マージの方式の既定: 基底のブランチ `base` で許された方式の先頭（`github.pick_merge_method`）。読めなければ None。

    `remote` はリポジトリを読むリモート名（`--set remote=` で渡した値）。省けばこの作業場所のリモート名。
    """
    try:
        hub = github.GitHub(git.Git(repo).remote_repo(remote), github.client())
        allowed = hub.allowed_merge_methods(base)
    except (git.GitError, github.GitHubError):  # noqa: NDF401 外部コマンド: リモートか GitHub の設定を読めない
        return None
    return github.pick_merge_method(None, allowed)


def _given_remote(out: Mapping[str, Value]) -> Optional[str]:
    """決めた（渡したか求めた）リモート名。base と merge_method もこのリモートから決める（push の宛先とそろえる）。"""
    remote = out.get("remote")
    return remote if isinstance(remote, str) and remote else None


def _remote_of_repo(repo: Path) -> str:
    """省いた `remote` の値（I7: 宣言の `remote`、無ければスキーマの既定）。誤りは `RouteError`。

    git の作業場所では、linked worktree から打ってもメインディレクトリの宣言を読む（`Git.remote`。取得・レビューの経路と
    同じ宛先）。git の作業場所でない場所（`.git` が無い）にはメインディレクトリが無いため、git を起動せず `repo` の宣言を読む。
    """
    try:
        if not (repo / ".git").exists():
            return git.remote_of(repo)
        return git.Git(repo).remote()
    except git.GitError as exc:
        raise RouteError(str(exc)) from None


def _omitted(t: Template, out: Mapping[str, Value], name: str) -> bool:
    """雛形が差し込み `name` を使い、まだ値が無いか。"""
    return name in t.params and name not in out


def derived(t: Template, given: Mapping[str, Value], cwd: Path) -> dict[str, Value]:
    """省いた `repo`・`worktree`・`remote`・`base`・`merge_method` を打った場所から決めて足す（決まらなければ足さない）。"""
    out = dict(given)
    if _omitted(t, out, "repo"):
        out["repo"] = repo_root(cwd)
    if _omitted(t, out, "remote") and "repo" in out:
        out["remote"] = _remote_of_repo(Path(str(out["repo"])))
    if _omitted(t, out, "worktree") and isinstance(out.get("branch"), str):
        out["worktree"] = str(Path(str(out["repo"])) / WORKTREES / out["branch"])
    if _omitted(t, out, "base") and "repo" in out:
        base = default_base(Path(str(out["repo"])), _given_remote(out))
        if base is not None:
            out["base"] = base
    if _omitted(t, out, "merge_method") and "repo" in out and isinstance(out.get("base"), str):
        method = default_merge_method(Path(str(out["repo"])), str(out["base"]), _given_remote(out))
        if method is not None:
            out["merge_method"] = method
    return out


def _check_worktree(t: Template, given: Mapping[str, Value], values: Mapping[str, Value]) -> None:
    """省いて決めた作業場所が使えなければ `RouteError`（ランナーがリポジトリの根へ倒れて別のチェックアウトを変えないため）。"""
    if "worktree" in t.params and "worktree" not in given and "worktree" in values:
        place = str(values["worktree"])
        if not steps.usable_worktree(place):
            raise RouteError(f"省いた作業場所 {place} が使える worktree でない。作るか --set worktree=<path> で渡す")


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
    """埋める先のリポジトリ（`repo`、無ければ打った場所）の宣言がテストを CI に寄せる（`test.ci.only` が真）か。

    工程から組むプラン（`ndf.progress.invoke.plan_path`）と同じく、宣言があれば手元のテストのステップを外す（PR 3590 の指摘）。
    """
    repo = values.get("repo")
    place = Path(repo) if isinstance(repo, str) and repo else Path.cwd()
    return scoped.ci_only(declared.test_section(place))


def _write_plan(a: argparse.Namespace) -> tuple[Template, Path]:
    t = choose(a.purpose)
    given = _pairs(a.sets, a.adds)
    values = derived(t, given, Path.cwd())
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
    return result.emit(result.result(TOOL, OK, f"雛形 {t.name} からプラン {out} を書いた", [item], next=nxt))


def _cmd_run(a: argparse.Namespace) -> int:
    _, out = _write_plan(a)
    return plan_cli.main(["next", str(out)])


def _cmd_templates(_: argparse.Namespace) -> int:
    found = templates().values()
    items = [{"kind": "template", "name": t.name, "purpose": t.purpose, "params": list(t.params)} for t in found]
    return result.emit(result.result(TOOL, OK, f"雛形が {len(items)} 本ある", items, {"templates": len(items)}))


def _read_lines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return [json.loads(line)["key"] for line in text.splitlines() if line.strip()]


def _invalid(detail: str) -> int:
    return result.emit(result.stopped(TOOL, "invalid", detail))


def _stopped_key(a: argparse.Namespace) -> str:
    """結果ファイルの同じ原因の鍵。読めない・止まった結果でなければ `RouteError`。"""
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
    p = argparse.ArgumentParser(prog="ndf route", description="プランの雛形を選んで埋める・同じ原因の続きを数える")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("templates", help="雛形の一覧").set_defaults(fn=_cmd_templates)
    for name, fn, text in (("render", _cmd_render, "雛形を選んで埋める"), ("run", _cmd_run, "埋めたプランを流す")):
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
