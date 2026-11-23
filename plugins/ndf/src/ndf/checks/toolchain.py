"""規則 toolchain: 配る構成の設定と、リポジトリが Python と uv を指定する箇所の一致を確かめる（#3687 の受け入れ条件 3・4）。

配る構成の設定（`ndf.contracts.toolchain`）が NDF の動く条件を持ち、リポジトリの設定（`.ndf/project.json` の `toolchain`）が
突き合わせる対象を挙げる。設定のキーごとに独立して効く。

- `workflows`: ワークフローの `setup-python` の `python-version` が `python_ci`（下限を確かめるワークフローでは
  `python_compat.versions` も）に在り、`setup-uv` の `version` が uv のバージョンと同じで、`run` が `setup-uv` 以外で uv を入れない
- `plugin_pyprojects`: `requires-python` の下限が Python の下限と同じ
- `launchers`: ランチャーの `UV_VERSION=`・`UV_INSTALLER_SHA256=` の行が uv のバージョンとインストーラの sha256と同じ

設定に `toolchain` が無ければ何も読まない（利用先のリポジトリは新しいキーを置くまで今までどおり）。
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import LiteralScalarString

from ndf.checks.model import CheckError, Finding, Scope
from ndf.contracts import toolchain as contract

RULE = __name__.rsplit(".", 1)[-1]
SETUP_UV = "astral-sh/setup-uv"
SETUP_PYTHON = "actions/setup-python"
# `run` の本文で uv の導入とみなす形（pip・pip3 の引数の uv・pipx install uv・astral.sh/uv の取得）
UV_INSTALL_PATTERNS = (
    re.compile(r"\bpip3?\s+install\b[^\n;&|]*?(?<![\w./=-])uv(?![\w./-])"),
    re.compile(r"\bpipx\s+install\b[^\n;&|]*?(?<![\w./=-])uv(?![\w./-])"),
    re.compile(r"\bastral\.sh/uv\b"),
)
# ランチャーの行（`<名前>="<値>"`）
LAUNCHER_LINE = re.compile(r'^(UV_VERSION|UV_INSTALLER_SHA256)=["\']?([^"\'\s]*)')
# `requires-python` の下限を決める条件（`>=3.11`・`~=3.11`・`==3.11.*`）
FLOOR_CLAUSE = re.compile(r"^\s*(?:>=|~=|==)\s*([0-9]+)\.([0-9]+)")
REQUIRES_PYTHON = re.compile(r"^\s*requires-python\s*=")


@dataclass(frozen=True)
class ToolUse:
    """ワークフローの 1 ステップの `setup-uv` か `setup-python`。`line` は `uses` の行、`values` は `with` のキー → (値の文字, 行)。"""

    action: str
    line: int
    values: dict[str, tuple[str, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class _Workflow:
    path: Path
    lines: list[str]
    steps: list[CommentedMap]


def _read_workflow(path: Path) -> _Workflow:
    """ワークフローを読み、すべてのジョブのステップを返す。YAML として読めなければ `CheckError`。"""
    try:
        text = path.read_text(encoding="utf-8")
        doc = YAML(typ="rt").load(text)
    except (OSError, UnicodeDecodeError, YAMLError) as exc:
        raise CheckError(f"ワークフローを読めない: {path}: {exc}") from exc
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    steps = [
        step
        for job in (jobs.values() if isinstance(jobs, dict) else [])
        if isinstance(job, dict) and isinstance(job.get("steps"), list)
        for step in job["steps"]
        if isinstance(step, CommentedMap)
    ]
    return _Workflow(path, text.splitlines(), steps)


def _scalar(mapping: CommentedMap, key: str, lines: list[str]) -> tuple[str, int]:
    """値の文字と行（1 始まり）。数として読める値（`3.10` は 3.1 になる）は書いたとおりの文字を本文から取る。"""
    value = mapping[key]
    line, col = mapping.lc.value(key)
    if not isinstance(value, str):
        value = lines[line][col:].split(" #", 1)[0].strip().strip("'\"")
    return str(value), line + 1


def _tools(wf: _Workflow) -> list[ToolUse]:
    found = []
    for step in wf.steps:
        uses = step.get("uses")
        action = uses.split("@", 1)[0].strip() if isinstance(uses, str) else ""
        if action not in (SETUP_UV, SETUP_PYTHON):
            continue
        given = step.get("with")
        values = {str(k): _scalar(given, k, wf.lines) for k in given} if isinstance(given, CommentedMap) else {}
        found.append(ToolUse(action, step.lc.key("uses")[0] + 1, values))
    return found


def workflow_tools(path: Path) -> list[ToolUse]:
    """1 本のワークフローの `setup-uv`・`setup-python` のステップ（`uses` の `@` の後ろと注記を除いた名前で拾う）。"""
    return _tools(_read_workflow(path))


def _run_hits(wf: _Workflow) -> Iterator[int]:
    """`run` の本文が `setup-uv` 以外で uv を入れる行。"""
    for step in wf.steps:
        body = step.get("run")
        if not isinstance(body, str):
            continue
        key_line = step.lc.key("run")[0] + 1
        for pattern in UV_INSTALL_PATTERNS:
            m = pattern.search(body)
            if m:
                yield key_line + 1 + body[: m.start()].count("\n") if isinstance(body, LiteralScalarString) else key_line
                break


def pyproject_floor(path: Path) -> tuple[tuple[int, int] | None, int]:
    """`requires-python` の下限とその行。無い・下限の条件が無ければ下限は `None`（行は無ければ 1）。"""
    try:
        text = path.read_text(encoding="utf-8")
        project = tomllib.loads(text).get("project")
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise CheckError(f"pyproject.toml を読めない: {path}: {exc}") from exc
    line = next((n for n, s in enumerate(text.splitlines(), 1) if REQUIRES_PYTHON.match(s)), 1)
    spec = project.get("requires-python") if isinstance(project, dict) else None
    if not isinstance(spec, str):
        return None, line
    floors = [(int(m[1]), int(m[2])) for clause in spec.split(",") if (m := FLOOR_CLAUSE.match(clause))]
    return (max(floors) if floors else None), line


def _items(mapping: dict[str, Any], key: str) -> list[Any]:
    """設定の一覧の値（キーが無ければ空。キーごとに独立して効く）。"""
    return list(mapping[key]) if key in mapping else []


class _Checker:
    """1 回の検査の文脈（見る範囲・規則の要約・リポジトリの設定の toolchain・配る構成の設定）。"""

    def __init__(self, scope: Scope, rules: dict[str, Any], settings: dict[str, Any]) -> None:
        self.scope = scope
        self.summary = rules[RULE]["summary"]
        self.settings = settings
        try:
            self.uv = contract.uv_version()
            self.digest = contract.uv_installer_sha256()
            self.floor = contract.python_floor()
        except contract.ToolchainError as exc:
            raise CheckError(str(exc)) from exc

    def finding(self, rel: str, line: int, detail: str) -> Finding:
        return Finding(RULE, rel, line, f"{self.summary}: {detail}")

    def files(self, rels: list[str]) -> Iterator[tuple[str, Path | None]]:
        """設定が挙げたパスごとに、当たるファイル。当たるものが無ければ `(パス, None)` を 1 つ返す。"""
        for rel in rels:
            paths = self.scope.repo_files(rel)
            yield from ((self.scope.rel(p), p) for p in paths) if paths else [(rel, None)]

    def workflows(self, glob: str) -> list[Finding]:
        allowed = set(_items(self.settings, "python_ci"))
        compat = self.settings["python_compat"] if "python_compat" in self.settings else {}
        compat_files = {p for _, p in self.files(_items(compat, "workflows")) if p is not None}
        found = []
        for path in self.scope.repo_files(glob):
            wf = _read_workflow(path)
            rel = self.scope.rel(path)
            ok = allowed | set(_items(compat, "versions")) if path in compat_files else allowed
            for tool in _tools(wf):
                found += self._tool(rel, tool, ok)
            found += [self.finding(rel, n, "uv は setup-uv だけで入れる") for n in _run_hits(wf)]
        return found

    def _tool(self, rel: str, tool: ToolUse, ok: set[str]) -> list[Finding]:
        if tool.action == SETUP_UV:
            if "version" not in tool.values:
                return [self.finding(rel, tool.line, f"setup-uv に version（{self.uv}）が無い")]
            value, line = tool.values["version"]
            return [] if value == self.uv else [self.finding(rel, line, f"setup-uv の version {value} が {self.uv} と違う")]
        if "python-version" not in tool.values:
            return []
        value, line = tool.values["python-version"]
        bad = [v for v in value.split() if v not in ok]
        return [self.finding(rel, line, f"python-version {' '.join(bad)} は {sorted(ok)} に無い")] if bad else []

    def pyprojects(self) -> list[Finding]:
        want = ".".join(map(str, self.floor))
        found = []
        for rel, path in self.files(_items(self.settings, "plugin_pyprojects")):
            if path is None:
                found.append(self.finding(rel, 1, "設定が挙げたファイルが無い"))
                continue
            floor, line = pyproject_floor(path)
            if floor != self.floor:
                found.append(self.finding(rel, line, f"requires-python の下限 {floor} が Python の下限 {want} と違う"))
        return found

    def launchers(self) -> list[Finding]:
        want = {"UV_VERSION": self.uv, "UV_INSTALLER_SHA256": self.digest}
        found = []
        for rel, path in self.files(_items(self.settings, "launchers")):
            if path is None:
                found.append(self.finding(rel, 1, "設定が挙げたファイルが無い"))
                continue
            seen = _launcher_lines(path)
            for name, value in want.items():
                if name not in seen:
                    found.append(self.finding(rel, 1, f"{name}= の行が無い"))
                elif seen[name][0] != value:
                    found.append(self.finding(rel, seen[name][1], f"{name} が配る構成の設定の {value} と違う"))
        return found


def _launcher_lines(path: Path) -> dict[str, tuple[str, int]]:
    """ランチャーの `UV_VERSION=`・`UV_INSTALLER_SHA256=` の最初の行の値と行。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(f"ランチャーを読めない: {path}: {exc}") from exc
    seen: dict[str, tuple[str, int]] = {}
    for n, text in enumerate(lines, 1):
        m = LAUNCHER_LINE.match(text)
        if m and m[1] not in seen:
            seen[m[1]] = (m[2], n)
    return seen


def check(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    """設定と指定箇所の食い違いを、1 か所 1 件の指摘にする。設定に `toolchain` が無ければ何も読まずに空。"""
    settings = scope.settings.get("toolchain")
    if not isinstance(settings, dict):
        return []
    checker = _Checker(scope, rules, settings)
    found = checker.pyprojects() + checker.launchers()
    if "workflows" in settings:
        found += checker.workflows(settings["workflows"])
    return found
