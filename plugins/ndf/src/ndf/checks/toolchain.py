"""規則 toolchain: 配る構成の設定と、リポジトリが Python と uv を指定する箇所の一致を確かめる（#3687 の受け入れ条件 3・4）。

配る構成の設定（`ndf.contracts.toolchain`）が NDF の動く条件を持ち、リポジトリの設定（`.ndf/project.json` の `toolchain`）が
突き合わせる対象を挙げる。設定のキーごとに独立して効く。

- `workflows`: ワークフローの `setup-python` が `python-version` を書き（`python-version-file` の参照先は読まない）、その値が
  `python_ci`（下限を確かめるワークフローでは `python_compat.versions` も）に在り、`setup-uv` の `version` が uv のバージョンと同じで、
  `run` のコマンドが `setup-uv` 以外で uv を入れない（シェルの語に分けて、コメント・`echo` の引数は見ず、行の継続はつなぐ）
- `plugin_pyprojects`: `requires-python` が許す最も古い Python が、Python の下限の `.0`（`3.11` なら `3.11.0`）と同じ
- `launchers`: ランチャーの `UV_VERSION=`・`UV_INSTALLER_SHA256=` の行が uv のバージョンとインストーラの sha256と同じ

設定に `toolchain` が無ければ何も読まない（利用先のリポジトリは新しいキーを置くまで今までどおり）。
"""

from __future__ import annotations

import re
import shlex
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
# `run` のコマンドで uv の導入とみなす形: `pip`・`pip3`・`python -m pip`・`pipx` の `install` の引数の uv の要求と、
# `curl`・`wget` での astral.sh/uv の取得
PIP_COMMANDS = frozenset({"pip", "pip3", "pipx"})
PYTHON_COMMAND = re.compile(r"^python[0-9.]*$")
UV_REQUIREMENT = re.compile(r"^uv(?:\[[^\]]*\])?(?:\s*[=<>!~].*)?$")
FETCH_COMMANDS = frozenset({"curl", "wget"})
UV_INSTALLER_URL = re.compile(r"\bastral\.sh/uv\b")
# コマンドの前に付いても中身を変えない語（環境変数の代入・sudo・env）
COMMAND_PREFIX = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=.*|sudo|env)$")
# シェルの区切り（`;`・`&&`・`||`・`|`・`&`・括弧）
SHELL_PUNCTUATION = frozenset(";&|()")
# ランチャーの行（`<名前>="<値>"`）
LAUNCHER_LINE = re.compile(r'^(UV_VERSION|UV_INSTALLER_SHA256)=["\']?([^"\'\s]*)')
# `requires-python` の 1 つの条件（演算子・リリースの番号・`==`/`!=` の `.*`）
SPEC_CLAUSE = re.compile(r"^\s*(===|~=|==|!=|<=|>=|<|>)\s*([0-9]+(?:\.[0-9]+)*)(\.\*)?\s*$")
# 許す最も古い Python を探す範囲（3.0.0 から順に）。3.0.0 を許せば下限は無いとみなす
PROBE_MINORS = range(100)
PROBE_PATCHES = range(50)
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
    except YAMLError as exc:
        # 例外の文には入力の行の抜粋が載る（Git の外の設定ファイルも読みうる）。パスと位置だけを出す
        mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
        where = f":{mark.line + 1}:{mark.column + 1}" if mark is not None else ""
        raise CheckError(f"ワークフローを YAML として読めない: {path}{where}") from None
    except UnicodeDecodeError as exc:
        raise CheckError(f"ワークフローを UTF-8 として読めない: {path}: 位置 {exc.start}") from None
    except OSError as exc:
        raise CheckError(f"ワークフローを読めない: {path}: {exc.strerror}") from None
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


def _logical_lines(body: str) -> Iterator[tuple[int, str]]:
    """`run` の本文の行を、行末の `\\` の継続をつないで返す（始まりの行の 0 始まりの位置と本文）。"""
    start, parts = 0, []
    for n, text in enumerate(body.splitlines()):
        if not parts:
            start = n
        if text.rstrip().endswith("\\"):
            parts.append(text.rstrip()[:-1])
            continue
        parts.append(text)
        yield start, " ".join(parts)
        parts = []
    if parts:
        yield start, " ".join(parts)


def _commands(line: str) -> Iterator[list[str]]:
    """1 行をシェルの語に分け、区切りごとのコマンドの語の並びを返す（`#` からのコメントは捨てる）。"""
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    words: list[str] = []
    for token in lexer:
        if token and set(token) <= SHELL_PUNCTUATION:
            yield words
            words = []
        else:
            words.append(token)
    yield words


def _installs_uv(words: list[str]) -> bool:
    """1 つのコマンドが setup-uv の外で uv を入れるか。"""
    while words and COMMAND_PREFIX.match(words[0]):
        words = words[1:]
    if not words:
        return False
    name = words[0].rsplit("/", 1)[-1]
    if name in FETCH_COMMANDS:
        return any(UV_INSTALLER_URL.search(w) for w in words[1:])
    if PYTHON_COMMAND.match(name) and words[1:3] == ["-m", "pip"]:
        args = words[3:]
    elif name in PIP_COMMANDS:
        args = words[1:]
    else:
        return False
    if "install" not in args:
        return False
    return any(UV_REQUIREMENT.match(w) for w in args[args.index("install") + 1 :])


def _line_installs_uv(line: str) -> bool:
    try:
        return any(_installs_uv(words) for words in _commands(line))
    except ValueError:
        # 閉じない引用符（行をまたぐ文字列）は語に分けられない。分けられた前までは見ずに、行の全体を 1 つの語の並びとして見る
        return any(_installs_uv(words) for words in [line.split()])


def _run_hits(wf: _Workflow) -> Iterator[int]:
    """`run` のコマンドが `setup-uv` 以外で uv を入れる行（1 ステップ 1 件）。"""
    for step in wf.steps:
        body = step.get("run")
        if not isinstance(body, str):
            continue
        key_line = step.lc.key("run")[0] + 1
        for start, line in _logical_lines(body):
            if _line_installs_uv(line):
                yield key_line + 1 + start if isinstance(body, LiteralScalarString) else key_line
                break


def _admits(clauses: list[tuple[str, tuple[int, ...], bool]], version: tuple[int, int, int]) -> bool:
    """リリースの番号 `version` が条件のすべてを満たすか（PEP 440 の比較のうち、リリースの番号だけで決まる分）。"""
    for op, want, wild in clauses:
        width = max(len(want), len(version))
        have, pad = version + (0,) * (width - len(version)), want + (0,) * (width - len(want))
        if wild:
            ok = version[: len(want)] == want
            ok = ok if op == "==" else not ok
        elif op == "~=":
            ok = have >= pad and version[: len(want) - 1] == want[:-1]
        else:
            ok = {
                "==": have == pad,
                "===": have == pad,
                "!=": have != pad,
                ">=": have >= pad,
                "<=": have <= pad,
                ">": have > pad,
                "<": have < pad,
            }[op]
        if not ok:
            return False
    return True


def _clauses(spec: str, path: Path) -> list[tuple[str, tuple[int, ...], bool]]:
    clauses = []
    for clause in spec.split(","):
        m = SPEC_CLAUSE.match(clause)
        if not m or (m[3] and m[1] not in ("==", "!=")) or (m[1] == "~=" and "." not in m[2]):
            raise CheckError(f"requires-python の条件を読めない: {path}: {clause.strip()}")
        clauses.append((m[1], tuple(int(x) for x in m[2].split(".")), bool(m[3])))
    return clauses


def pyproject_floor(path: Path) -> tuple[tuple[int, int, int] | None, int]:
    """`requires-python` が許す最も古い Python 3 のリリースとその行。

    無い・3.0.0 を許す（下限が無い）・どれも許さなければ `None`（行は無ければ 1）。`>=3.11.1` は `(3, 11, 1)`、
    `>=3.11,!=3.11.*` は `(3, 12, 0)` になる（比較の先頭だけでなく、条件の全体で決める）。
    """
    try:
        text = path.read_text(encoding="utf-8")
        project = tomllib.loads(text).get("project")
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise CheckError(f"pyproject.toml を読めない: {path}: {exc}") from exc
    line = next((n for n, s in enumerate(text.splitlines(), 1) if REQUIRES_PYTHON.match(s)), 1)
    spec = project.get("requires-python") if isinstance(project, dict) else None
    if not isinstance(spec, str):
        return None, line
    clauses = _clauses(spec, path)
    lowest = next(((3, m, p) for m in PROBE_MINORS for p in PROBE_PATCHES if _admits(clauses, (3, m, p))), None)
    return (None if lowest == (3, 0, 0) else lowest), line


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
            # python-version-file の参照先・指定なしの既定は許可の一覧と照らせないので、書いた値を求める
            return [self.finding(rel, tool.line, f"setup-python に python-version（{sorted(ok)} のどれか）が無い")]
        value, line = tool.values["python-version"]
        bad = [v for v in value.split() if v not in ok]
        return [self.finding(rel, line, f"python-version {' '.join(bad)} は {sorted(ok)} に無い")] if bad else []

    def pyprojects(self) -> list[Finding]:
        want = (*self.floor, 0)
        found = []
        for rel, path in self.files(_items(self.settings, "plugin_pyprojects")):
            if path is None:
                found.append(self.finding(rel, 1, "設定が挙げたファイルが無い"))
                continue
            floor, line = pyproject_floor(path)
            if floor != want:
                have = "無し" if floor is None else ".".join(map(str, floor))
                found.append(
                    self.finding(rel, line, f"requires-python が許す最も古い Python {have} が下限 {'.'.join(map(str, want))} と違う")
                )
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
