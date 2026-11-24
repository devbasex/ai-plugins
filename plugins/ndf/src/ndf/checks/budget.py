"""規則 budget: 量の件数を数え、リポジトリの設定（`.ndf/project.json` の `budget`）の上限と比べる（#3687 の受け入れ条件 6・7）。

数えるのは 4 つで、件数は違反の有無にかかわらず `ndf check` の結果 JSON の `metrics.budget` に出す。

- `tests`: テストのファイル（表 `test-files` の見分け）の、最上位とクラスの中の `def test_`・`async def test_` の定義
- `ci_jobs`: `toolchain.workflows` に当たるワークフローの `jobs` のキー（matrix の展開は数えない）
- `unused_deps`: `toolchain.plugin_pyprojects` の `[project].dependencies` のうち、隣の `uv.lock` にあり、同じディレクトリの
  `src/` に import が無く、`budget.dep_exempt` にも無いもの
- `replaceable`: 置き換えの規則（`contracts/replaceable/`）に当たった、製品のコードの箇所

設定のキーごとに独立して効く。`budget` の上限のキーが無い項目は上限と比べず、`toolchain` のキーが無ければその数えは 0 件である。
"""

from __future__ import annotations

import ast
import fnmatch
import re
import tomllib
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from ndf import contracts, identity
from ndf.checks.model import SRC_DIR, CheckError, Finding, Scope, Source, Workflow
from ndf.contracts import facts

RULE = __name__.rsplit(".", 1)[-1]
# 規則 budget のうち、ワークフローと設定だけで決まる CI のジョブの上限だけを当てる名前（`--only` で選ぶ）
JOBS_RULE = "budget-ci-jobs"
# 置き換えの規則の分割した表（`contracts/` からの相対）と、1 規則の形
REPLACEABLE = "replaceable"
REPLACEABLE_SCHEMA = contracts.schema_path("replaceable")
# 設定の `budget` の形（設定のスキーマの定義の 1 つ）
BUDGET_SCHEMA = "schemas/ndf-settings/project.schema.json#/$defs/Budget"
# 上限と比べる項目（数えた件数の名前と同じ）
LIMIT_KEYS = ("tests", "ci_jobs", "unused_deps", "replaceable")
# `detail` に並べる中身の件数
DETAIL_HEAD = 5
LOCK_NAME = "uv.lock"
TEST_PREFIX = "test_"
# PEP 503 の名前の正規化（`-`・`_`・`.` の続きを 1 つの `-` にして小文字へ）
NAME_RUN = re.compile(r"[-_.]+")
# 依存の指定の先頭の名前（PEP 508）
DEP_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


@dataclass(frozen=True)
class ReplaceableRule:
    """置き換えの規則 1 件。`find` は構文木で見つける形、`exempt` は数えない保存先（プラグインの根からの相対）。"""

    key: str
    find: dict[str, Any]
    replace_with: str
    exempt: frozenset[str]


@dataclass(frozen=True)
class Counts:
    """4 つの件数と、使われない依存の名前・置き換えられる自作の箇所。"""

    tests: int
    ci_jobs: int
    unused: list[str]
    hits: list[Finding]

    @property
    def unused_deps(self) -> int:
        return len(self.unused)

    @property
    def replaceable(self) -> int:
        return len(self.hits)

    def as_metrics(self) -> dict[str, int]:
        """結果 JSON の `metrics.budget` の値。"""
        return {k: getattr(self, k) for k in LIMIT_KEYS}

    def items(self, key: str) -> list[str]:
        """上限を超えたときに `detail` に並べる中身。"""
        if key == "unused_deps":
            return self.unused
        if key == "replaceable":
            return [f"{h.path}:{h.line}" for h in self.hits]
        return []


def normalize(name: str) -> str:
    """依存の名前を PEP 503 の形（小文字・区切りは `-`）へ。"""
    return NAME_RUN.sub("-", name).lower()


def _section(scope: Scope, key: str) -> dict[str, Any]:
    value = scope.settings[key] if key in scope.settings else {}
    return value if isinstance(value, dict) else {}


def _budget_settings(scope: Scope) -> dict[str, Any]:
    """設定の `budget`。無ければ空、形が違えば（上限が整数でないなど）`CheckError`。"""
    if "budget" not in scope.settings:
        return {}
    data = scope.settings["budget"]
    try:
        errs = contracts.schema_errors(data, BUDGET_SCHEMA)
    except contracts.ContractError as exc:
        raise CheckError(str(exc)) from exc
    if errs:
        raise CheckError(f"リポジトリの設定の budget の形が違う: {'; '.join(errs)}")
    return data


def _tests_in(body: list[ast.stmt]) -> int:
    return sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith(TEST_PREFIX) for n in body)


def is_test_file(rel: str) -> bool:
    """表 `test-files` の役割がテストの行（列 `role` を省いた行）のどれかに当たるか。名前はファイル名全体、保存先はディレクトリの名前と比べる。"""
    p = PurePosixPath(rel)
    # 表の名前と、テストとして数えない役割（`conftest.py` などテストを支えるファイル）は `crossrefactor.tests_decide` の定数と同じ。
    # 検査は領域を読まないため、最上位の定数として持たない（const-fact）
    for row in facts.rows("test-files"):
        if "role" in row and row["role"] == "support":
            continue
        if fnmatch.fnmatchcase(p.name, row["pattern"]) if row["where"] == "name" else row["pattern"] in p.parts[:-1]:
            return True
    return False


def count_tests(scope: Scope) -> int:
    """テストのファイルの、最上位とクラスの中の `def test_` の定義の数（入れ子の関数は数えない）。"""
    total = 0
    for src in scope.python():
        if not is_test_file(scope.plugin_rel(src)):
            continue
        body = src.tree.body
        total += _tests_in(body) + sum(_tests_in(n.body) for n in body if isinstance(n, ast.ClassDef))
    return total


def count_jobs(workflows: list[Workflow]) -> int:
    """ワークフローの `jobs` のキーの数の合計。`jobs` が無いファイルは 0 件。"""
    return sum(len(wf.jobs) for wf in workflows)


def _read_toml(path: Path, label: str) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise CheckError(f"{label}を読めない: {path}: {exc}") from exc


def _declared(pyproject: Path) -> list[str]:
    """`[project].dependencies` の名前（正規化した形）。"""
    data = _read_toml(pyproject, "依存の定義")
    project = data["project"] if "project" in data else {}
    deps = project["dependencies"] if isinstance(project, dict) and "dependencies" in project else []
    return [normalize(m[1]) for d in deps if isinstance(d, str) and (m := DEP_NAME.match(d))]


def _locked(lock: Path) -> set[str]:
    data = _read_toml(lock, "依存の lock ")
    packages = data["package"] if "package" in data else []
    return {normalize(p["name"]) for p in packages if isinstance(p, dict) and "name" in p}


def _imported(sources: list[Source]) -> set[str]:
    """絶対の import の最上位の名前（関数の中の import も含む）。"""
    found: set[str] = set()
    for src in sources:
        for node in ast.walk(src.tree):
            if isinstance(node, ast.Import):
                found |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                found.add(node.module.split(".")[0])
    return found


def _dep_settings(scope: Scope) -> tuple[list[str], dict[str, str], set[str]]:
    """調べる依存の定義のパス・依存の名前から import の名前への対応・数えない依存の名前（名前は正規化した形）。"""
    toolchain = _section(scope, "toolchain")
    rels = toolchain["plugin_pyprojects"] if "plugin_pyprojects" in toolchain else []
    settings = _budget_settings(scope)
    modules = {normalize(k): v for k, v in (settings["dep_modules"] if "dep_modules" in settings else {}).items()}
    exempt = {normalize(k) for k in (settings["dep_exempt"] if "dep_exempt" in settings else {})}
    return rels, modules, exempt


def _unused_in(scope: Scope, rel: str, modules: dict[str, str], exempt: set[str]) -> set[str]:
    """依存の定義 1 つの、使われない依存の名前。"""
    pyproject = scope.repo_file(rel, "依存の定義")
    locked = _locked(scope.repo_file(scope.rel(pyproject.with_name(LOCK_NAME)), "依存の lock "))
    used = _imported(scope.python_under(pyproject.parent / SRC_DIR))
    unused: set[str] = set()
    for name in _declared(pyproject):
        module = modules[name] if name in modules else name.replace("-", "_")
        if name in locked and name not in exempt and module not in used:
            unused.add(name)
    return unused


def unused_deps(scope: Scope) -> list[str]:
    """`toolchain.plugin_pyprojects` の直接の実行時の依存のうち、使われないものの名前（昇順）。"""
    rels, modules, exempt = _dep_settings(scope)
    unused: set[str] = set()
    for rel in rels:
        unused |= _unused_in(scope, rel, modules, exempt)
    return sorted(unused)


def _dotted(node: ast.expr) -> str:
    """`a.b.c` の呼び先の名前（名前と属性の続きでなければ空）。"""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    return ".".join([node.id, *reversed(parts)])


def _calls(find: dict[str, Any]) -> Callable[[Any], bool]:
    return lambda n: _dotted(n.func) == find["name"]


def _joins(find: dict[str, Any]) -> Callable[[Any], bool]:
    def hit(n: ast.Call) -> bool:
        if not (isinstance(n.func, ast.Attribute) and n.func.attr == "join"):
            return False
        return isinstance(n.func.value, ast.Constant) and n.func.value.value == find["separator"]

    return hit


def _loops(find: dict[str, Any]) -> Callable[[Any], bool]:
    """本体に `try` と、名前の最後が `callee` の呼び出しを持つ繰り返し（自作の打ち直し）。"""

    def hit(n: ast.For | ast.While) -> bool:
        inner = [x for stmt in n.body for x in ast.walk(stmt)]
        waits = any(isinstance(x, ast.Call) and _dotted(x.func).rsplit(".", 1)[-1] == find["callee"] for x in inner)
        return waits and any(isinstance(x, ast.Try) for x in inner)

    return hit


# `find.kind` → 見る構文木のノードの型と、その型の 1 ノードが当たるかを決める関数を作る関数
FINDERS: dict[str, tuple[tuple[type[ast.AST], ...], Callable[[dict[str, Any]], Callable[[Any], bool]]]] = {
    "call": ((ast.Call,), _calls),
    "join": ((ast.Call,), _joins),
    "loop": ((ast.For, ast.While), _loops),
}


def replaceable_rules() -> list[ReplaceableRule]:
    """置き換えの規則の表。読めない・形が違う・`find` の種類を知らなければ `CheckError`。"""
    try:
        table = contracts.load_split(REPLACEABLE)
        for key, item in table.items():
            contracts.require_schema(item, REPLACEABLE_SCHEMA, contracts.ContractError, f"置き換えの規則 {key}")
    except contracts.ContractError as exc:
        raise CheckError(str(exc)) from exc
    unknown = [k for k, item in table.items() if item["find"]["kind"] not in FINDERS]
    if unknown:
        raise CheckError(f"置き換えの規則の find が知らない種類: {', '.join(unknown)}")
    return [ReplaceableRule(k, v["find"], v["replace_with"], frozenset(v["exempt"])) for k, v in table.items()]


def replaceable_hits(scope: Scope) -> list[Finding]:
    """置き換えの規則に当たった製品のコードの箇所（パスと行の順）。規則の `exempt` の保存先は数えない。

    構文木は規則の数によらず 1 ファイル 1 回だけ走査し、ノードの型に合う規則だけを当てる。
    """
    by_type: dict[type[ast.AST], list[tuple[ReplaceableRule, Callable[[Any], bool]]]] = defaultdict(list)
    for rule in replaceable_rules():
        types, make = FINDERS[rule.find["kind"]]
        match = make(rule.find)
        for node_type in types:
            by_type[node_type].append((rule, match))
    found = []
    for src in scope.code():
        rel = scope.plugin_rel(src)
        for node in ast.walk(src.tree):
            found += [
                Finding(RULE, src.rel, node.lineno, f"{rule.key}: {rule.replace_with} で置き換えられる")
                for rule, match in by_type[type(node)]
                if rel not in rule.exempt and match(node)
            ]
    return sorted(found, key=lambda f: (f.path, f.line, f.detail))


def measure(scope: Scope) -> Counts:
    """4 つの件数と中身。`run.py` が 1 回だけ呼び、`check` と結果 JSON の `metrics.budget` へ渡す。"""
    return Counts(count_tests(scope), count_jobs(scope.workflows), unused_deps(scope), replaceable_hits(scope))


def _settings_line(scope: Scope, key: str) -> tuple[str, int]:
    """設定のファイル（リポジトリの根からの相対）と、`budget` の中の `key` の行（見つからなければ 1）。"""
    path = scope.repo_root / identity.FIXED_WORDS["ndf_settings_dir"] / "project.json"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CheckError(f"リポジトリの設定を読めない: {path}: {exc}") from exc
    start = text.find('"budget"')
    at = text.find(f'"{key}"', max(start, 0))
    return scope.rel(path), (text[:at].count("\n") + 1 if start >= 0 and at >= 0 else 1)


def _over(scope: Scope, rules: dict[str, Any], key: str, count: int, head: list[str]) -> Finding:
    """上限を超えた項目 1 つの指摘。"""
    rel, line = _settings_line(scope, key)
    detail = f"{key} {count} 件（上限 {scope.settings['budget'][key]}）。{rules[RULE]['summary']}"
    return Finding(RULE, rel, line, detail + (f": {', '.join(head)}" if head else ""))


def check(scope: Scope, rules: dict[str, Any], counts: Counts | None = None) -> list[Finding]:
    """件数（渡されなければ数える）を設定の上限と比べ、超えた項目ごとに 1 件の指摘にする。`budget` が無ければ空。"""
    limits = _budget_settings(scope)
    if counts is None:
        counts = measure(scope)
    return [
        _over(scope, rules, key, getattr(counts, key), counts.items(key)[:DETAIL_HEAD])
        for key in LIMIT_KEYS
        if key in limits and getattr(counts, key) > limits[key]
    ]


def check_jobs(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    """CI のジョブの数だけを設定の上限と比べる（プラグインのファイルを読まない）。上限が無ければワークフローも読まない。"""
    limits = _budget_settings(scope)
    if "ci_jobs" not in limits or (count := count_jobs(scope.workflows)) <= limits["ci_jobs"]:
        return []
    return [replace(_over(scope, rules, "ci_jobs", count, []), rule=JOBS_RULE)]
