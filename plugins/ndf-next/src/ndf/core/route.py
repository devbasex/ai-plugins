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
        宣言が無ければ `origin` の既定ブランチにする
    ndf route run <依頼の種類> --out <plan.json> [--set ...] [--add ...]
        render の後に同じプランを `ndf plan next` で流す（即時修正をテスト → PR → merge-when-green の 1 つの
        エントリポイントで流す）
    ndf route repeat --result <結果 JSON のファイル> --log <記録の JSONL>
        止まった結果の同じ原因の鍵（種類と落ちたテストの集まり）を記録へ足し、続いた回数が
        `supervise.step-rerun` の `same_cause` を超えたら `fix_limit` で止まる（人へ戻す）

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
from ndf.adapters import git
from ndf.contracts import causes, exit_codes, limits, result
from ndf.contracts.exit_codes import OK
from ndf.core.plan import cli as plan_cli
from ndf.core.plan import model, steps

TOOL = cli.tool_of(__name__)
# 雛形の置き場（contracts/ の下）と、雛形でない決まった名前のファイル
TEMPLATE_DIR = "templates"
PARAMS_FILE = "params.json"
PARTS_FILE = "parts/steps.json"
# 雛形のステップで共有のステップを引く鍵
USE = "use"
# 同じ原因の続きの上限の宣言
REPEAT_LIMIT = "supervise.step-rerun"
# 作業場所を省いたときの置き場（リポジトリの根の下）
WORKTREES = ".worktrees"
# base を省いたときに読むブランチの運用の宣言（リポジトリの根の下）と、その鍵
BRANCH_DECLARATION = ".ndf/worktree.json"
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


def expanded(t: Template) -> dict[str, Any]:
    """`use` を開いたプランの形（差し込みは埋めない）。"""
    plan = copy.deepcopy(dict(t.plan))
    plan["steps"] = [_expand_step(s) for s in plan["steps"]]
    return plan


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


def resolve(t: Template, given: Mapping[str, Value]) -> dict[str, Value]:
    """差し込みの値を決める（与えた値 → 既定）。雛形に無い名前・欠けた値・並びかの食い違いは `RouteError`。"""
    defs = params()
    _check_unknown(t, given)
    found = {name: _value_of(name, given, defs) for name in t.params}
    values = {name: value for name, value in found.items() if value is not None}
    missing = [name for name, value in found.items() if value is None]
    if missing:
        raise RouteError(f"雛形 {t.name} の差し込みの値が無い: {', '.join(missing)}")
    return values


def render(t: Template, given: Mapping[str, Value]) -> dict[str, Any]:
    """雛形を埋めたプラン（`model.parse` を通る形）。誤りは `RouteError`。"""
    plan = _fill(expanded(t), resolve(t, given))
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
    try:
        render(t, _sample(t.params))
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
    metrics = found.get("metrics") if isinstance(found.get("metrics"), Mapping) else {}
    tests = metrics.get("failed_tests") if isinstance(metrics.get("failed_tests"), list) else []
    return json.dumps([kind, sorted(str(t) for t in tests)], ensure_ascii=False)


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


def default_base(repo: Path) -> Optional[str]:
    """宛先のブランチの既定: ブランチの運用の宣言の `base_branch`、無ければ `origin` の既定ブランチ（決まらなければ None）。"""
    try:
        declared = json.loads((repo / BRANCH_DECLARATION).read_text(encoding="utf-8")).get(BASE_KEY)
    except (OSError, ValueError, AttributeError):
        declared = None
    if isinstance(declared, str) and declared:
        return declared
    try:
        ref = git.Git(repo).run("symbolic-ref", "--short", "refs/remotes/origin/HEAD").strip()
    except git.GitError:  # noqa: NDF401 外部コマンド: origin の既定ブランチを持たないリポジトリ
        return None
    branch = ref.removeprefix("origin/")
    return branch if branch else None


def derived(t: Template, given: Mapping[str, Value], cwd: Path) -> dict[str, Value]:
    """省いた `repo`・`worktree`・`base` を打った場所から決めて足す（`base` が決まらなければ足さない）。"""
    out = dict(given)
    if "repo" in t.params and "repo" not in out:
        out["repo"] = repo_root(cwd)
    if "worktree" in t.params and "worktree" not in out and isinstance(out.get("branch"), str):
        out["worktree"] = str(Path(str(out["repo"])) / WORKTREES / out["branch"])
    if "base" in t.params and "base" not in out and "repo" in out:
        base = default_base(Path(str(out["repo"])))
        if base is not None:
            out["base"] = base
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


def _write_plan(a: argparse.Namespace) -> tuple[Template, Path]:
    t = choose(a.purpose)
    given = _pairs(a.sets, a.adds)
    values = derived(t, given, Path.cwd())
    _check_worktree(t, given, values)
    plan = render(t, values)
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
    try:
        found = steps.last_json(Path(a.result).read_text(encoding="utf-8"))
    except OSError as exc:
        raise RouteError(f"結果 {a.result} を読めない: {exc.strerror or exc}") from None
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
