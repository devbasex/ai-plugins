"""規則 toolchain: 配る構成の設定と、リポジトリが Python と uv を指定する箇所の一致を確かめる（#3687 の受け入れ条件 3・4）。

配る構成の設定（`ndf.contracts.toolchain`）が NDF の動く条件を持ち、リポジトリの設定（`.ndf/project.json` の `toolchain`）が
突き合わせる対象を挙げる。設定のキーごとに独立して効く。

- `workflows`: ワークフローの `setup-python` が `python-version` を書き（`python-version-file` の参照先は読まない）、その値が
  `python_ci`（下限を確かめるワークフローでは `python_compat.versions` も）に在り、`setup-uv` の `version` が uv のバージョンと同じで、
  `run` のコマンドが `setup-uv` 以外で uv を入れない（本文の全体を bash の構文木にし、制御構造の中のコマンドは見て、
  ヒアドキュメント・コメント・`echo` の引数は見ない）
- `plugin_pyprojects`: `requires-python` が許す最も古い Python が、Python の下限の `.0`（`3.11` なら `3.11.0`）と同じ
- `launchers`: ランチャーの `UV_VERSION=`・`UV_INSTALLER_SHA256=` の行が uv のバージョンとインストーラの sha256と同じ

設定に `toolchain` が無ければ何も読まない（利用先のリポジトリは新しいキーを置くまで今までどおり）。
"""

from __future__ import annotations

import functools
import re
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import VERSION_PATTERN, Version
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import LiteralScalarString

from ndf.checks.model import CheckError, Finding, Scope, Workflow, read_workflow
from ndf.contracts import toolchain as contract

RULE = __name__.rsplit(".", 1)[-1]
SETUP_UV = "astral-sh/setup-uv"
SETUP_PYTHON = "actions/setup-python"
# `run` のコマンドで uv の導入とみなす形: `pip`・`pip3`・`python -m pip`・`pipx` の `install` の引数の uv の要求と、
# `curl`・`wget` での astral.sh/uv の取得
PIP_COMMANDS = frozenset({"pip", "pip3", "pipx"})
PYTHON_COMMAND = re.compile(r"^python[0-9.]*$")
UV_REQUIREMENT = re.compile(r"^uv(?:\[[^\]]*\])?(?:\s*[=<>!~].*)?$")
FETCH_COMMAND = re.compile(r"^(?:curl|wget)$")
UV_INSTALLER_URL = re.compile(r"\bastral\.sh/uv\b")
# bash のエスケープ: 引用符の外はどの字も、二重引用符の中は `$`・`` ` ``・`"`・`\`・改行だけを字のまま残す
UNQUOTED_ESCAPE = re.compile(r"\\(.)", re.DOTALL)
QUOTED_ESCAPE = re.compile(r'\\([$`"\\\n])')
# 語と語の間が行の継続だけ（空白を挟まない）なら、bash には 1 語
LINE_CONTINUATION = re.compile(rb"(?:\\\n)+")
# コマンドの前に付いても中身を変えない語（環境変数の代入・sudo・env）
COMMAND_PREFIX = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=.*|sudo|env)$")
# ランチャーの行（`<名前>="<値>"`）
LAUNCHER_LINE = re.compile(r'^(UV_VERSION|UV_INSTALLER_SHA256)=["\']?([^"\'\s]*)')
# PEP 440 のバージョンの字（`packaging` の `Version` が受ける形）
PEP440_VERSION = re.compile(rf"^\s*{VERSION_PATTERN}\s*$", re.VERBOSE | re.IGNORECASE)
REQUIRES_PYTHON = re.compile(r"^\s*requires-python\s*=")


@dataclass(frozen=True)
class ToolUse:
    """ワークフローの 1 ステップの `setup-uv` か `setup-python`。`line` は `uses` の行、`values` は `with` のキー → (値の文字, 行)。"""

    action: str
    line: int
    values: dict[str, tuple[str, int]] = field(default_factory=dict)


def _scalar(mapping: CommentedMap, key: str, lines: list[str]) -> tuple[str, int]:
    """値の文字と行（1 始まり）。数として読める値（`3.10` は 3.1 になる）は書いたとおりの文字を本文から取る。"""
    value = mapping[key]
    line, col = mapping.lc.value(key)
    if not isinstance(value, str):
        value = lines[line][col:].split(" #", 1)[0].strip().strip("'\"")
    return str(value), line + 1


def _tools(wf: Workflow) -> list[ToolUse]:
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
    return _tools(read_workflow(path))


@functools.cache
def _bash() -> Any:
    import tree_sitter
    import tree_sitter_bash

    return tree_sitter.Parser(tree_sitter.Language(tree_sitter_bash.language()))


def _unescape(pattern: re.Pattern[str], text: str) -> str:
    # バックスラッシュと改行の組は行の継続で、字として残らない
    return pattern.sub(lambda m: "" if m[1] == "\n" else m[1], text)


def _word(node: Any) -> str:
    """シェルの 1 語の字（引用符とエスケープを bash と同じに外す。展開はそのままの字で残す）。"""
    text = node.text.decode("utf-8", "replace")
    if node.type == "raw_string":
        return text[1:-1]
    if node.type == "string" and len(text) >= 2:
        return _unescape(QUOTED_ESCAPE, text[1:-1])
    if node.type == "concatenation":
        return "".join(_word(c) for c in node.children)
    return _unescape(UNQUOTED_ESCAPE, text)


def _commands(body: str) -> Iterator[tuple[int, list[str]]]:
    """`run` の本文の全体を bash の構文木にし、実行されるコマンドごとに（本文の 0 始まりの行, 語の並び）を返す。

    `if`・`while` などの制御構造の中のコマンドも、`$(…)` の中のコマンドも拾う。ヒアドキュメントの本文・コメント・
    引用符の中の字はコマンドにならない。環境変数の代入はコマンドの語に入れない。
    """
    source = body.encode("utf-8")
    stack = [_bash().parse(source).root_node]
    while stack:
        node = stack.pop()
        if node.type == "command":
            yield node.start_point[0], _command_words(node, source)
        stack.extend(reversed(node.children))


def _command_words(node: Any, source: bytes) -> list[str]:
    """構文木の 1 つのコマンドの語の並び。環境変数の代入・リダイレクト・コメントは語に入れない。"""
    words: list[str] = []
    end = None
    for c in node.named_children:
        if c.type in ("variable_assignment", "file_redirect", "herestring_redirect", "comment"):
            end = None
            continue
        text = _word(c.named_children[0] if c.type == "command_name" and c.named_children else c)
        # 構文木は引用符の外の行の継続（バックスラッシュと改行）で語を分けるが、bash は前後を 1 語につなぐ
        if words and end is not None and LINE_CONTINUATION.fullmatch(source[end : c.start_byte]):
            words[-1] += text
        else:
            words.append(text)
        end = c.end_byte
    return words


def _installs_uv(words: list[str]) -> bool:
    """1 つのコマンドが setup-uv の外で uv を入れるか。"""
    while words and COMMAND_PREFIX.match(words[0]):
        words = words[1:]
    if not words:
        return False
    name = words[0].rsplit("/", 1)[-1]
    if FETCH_COMMAND.match(name):
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


def _run_hits(wf: Workflow) -> Iterator[int]:
    """`run` のコマンドが `setup-uv` 以外で uv を入れる行（1 ステップ 1 件）。"""
    for step in wf.steps:
        body = step.get("run")
        if not isinstance(body, str):
            continue
        key_line = step.lc.key("run")[0] + 1
        row = next((row for row, words in _commands(body) if _installs_uv(words)), None)
        if row is not None:
            yield key_line + 1 + row if isinstance(body, LiteralScalarString) else key_line


def _specifiers(spec: str, path: Path, line: int) -> SpecifierSet:
    try:
        return SpecifierSet(spec)
    except InvalidSpecifier:
        # 設定に誤って入った値（秘密を含みうる）を結果へ載せないよう、条件の字は引かずにファイルと位置で示す
        raise CheckError(f"requires-python を PEP 440 の条件として読めない: {path}:{line}") from None


def _pad(release: tuple[int, ...]) -> tuple[int, int, int]:
    major, minor, micro = (*release, 0, 0)[:3]
    return major, minor, micro


def _release(text: str) -> tuple[int, ...] | None:
    """条件の番号のリリースの部分。`===` の任意の字のようにバージョンとして読めなければ `None`。"""
    return Version(text).release if PEP440_VERSION.match(text) else None


def _candidates(specs: SpecifierSet) -> list[tuple[int, int, int]]:
    """許す最も古い Python 3 の正式リリースになりうる番号（小さい順）。

    許す番号の集まりは、条件ごとに許す範囲の重なりで、その最小は 3.0.0 か、どれかの条件の範囲の下の端に在る。
    下の端は、条件の番号そのものか、その直後（パッチを 1 つ進める）か、`!=` の `.*` の外の最初の番号のどれか。
    `===` の字がバージョンとして読めなければ、どの正式リリースの字とも一致しないので下の端を足さない。
    """
    found = {(3, 0, 0)}
    for s in specs:
        wild = s.version.endswith(".*")
        release = _release(s.version.removesuffix(".*"))
        if release is None:
            continue
        r = _pad(release)
        found |= {r, (r[0], r[1], r[2] + 1)}
        if wild:
            found.add(_pad((*release[:-1], release[-1] + 1)))
    return sorted(v for v in found if v[0] == 3)


def _admits(specs: SpecifierSet, version: tuple[int, int, int]) -> bool:
    """正式リリース `version` を `3.11.0` の形の文字列で条件のすべてと比べる（`===` は字の一致で比べる）。"""
    return specs.contains(".".join(map(str, version)))


def pyproject_floor(path: Path) -> tuple[tuple[int, int, int] | None, int]:
    """`requires-python` が許す最も古い Python 3 の正式リリースとその行。

    無い・3.0.0 を許す（下限が無い）・どれも許さなければ `None`（行は無ければ 1）。`>=3.11.1` は `(3, 11, 1)`、
    `>=3.11,!=3.11.*` は `(3, 12, 0)`、`>=3.11.0rc1,<4` は `(3, 11, 0)` になる（比較の先頭だけでなく、条件の全体で決める）。
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
    specs = _specifiers(spec, path, line)
    lowest = next((v for v in _candidates(specs) if _admits(specs, v)), None)
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

    def workflows(self) -> list[Finding]:
        allowed = set(_items(self.settings, "python_ci"))
        compat = self.settings["python_compat"] if "python_compat" in self.settings else {}
        compat_files = {p for _, p in self.files(_items(compat, "workflows")) if p is not None}
        found = []
        for wf in self.scope.workflows:
            rel = self.scope.rel(wf.path)
            ok = allowed | set(_items(compat, "versions")) if wf.path in compat_files else allowed
            for tool in _tools(wf):
                found += self._tool(rel, tool, ok)
            found += [self.finding(rel, n, "uv は setup-uv だけで入れる") for n in _run_hits(wf)]
        return found

    def _tool(self, rel: str, tool: ToolUse, ok: set[str]) -> list[Finding]:
        return self._setup_uv(rel, tool) if tool.action == SETUP_UV else self._setup_python(rel, tool, ok)

    def _setup_uv(self, rel: str, tool: ToolUse) -> list[Finding]:
        if "version" not in tool.values:
            return [self.finding(rel, tool.line, f"setup-uv に version（{self.uv}）が無い")]
        value, line = tool.values["version"]
        return [] if value == self.uv else [self.finding(rel, line, f"setup-uv の version {value} が {self.uv} と違う")]

    def _setup_python(self, rel: str, tool: ToolUse, ok: set[str]) -> list[Finding]:
        if "python-version" not in tool.values:
            # python-version-file の参照先・指定なしの既定は python_ci の値と比べられないので、書いた値を求める
            return [self.finding(rel, tool.line, f"setup-python に python-version（{sorted(ok)} のどれか）が無い")]
        value, line = tool.values["python-version"]
        if not value.split():
            # 空・空白だけの値は setup-python の既定へ落ちる。書いていないのと同じに扱う
            return [self.finding(rel, line, f"setup-python に python-version（{sorted(ok)} のどれか）が無い")]
        bad = [v for v in value.split() if v not in ok]
        return [self.finding(rel, line, f"python-version {' '.join(bad)} は {sorted(ok)} に無い")] if bad else []

    def listed(self, key: str, judge: Callable[[str, Path], list[Finding]]) -> list[Finding]:
        """設定の `key` が挙げたファイルごとの `judge` の指摘。無いファイルは 1 件の指摘にする（挙げた順のまま）。"""
        found = []
        for rel, path in self.files(_items(self.settings, key)):
            found += [self.finding(rel, 1, "設定が挙げたファイルが無い")] if path is None else judge(rel, path)
        return found

    def pyprojects(self) -> list[Finding]:
        return self.listed("plugin_pyprojects", self._pyproject)

    def _pyproject(self, rel: str, path: Path) -> list[Finding]:
        want = (*self.floor, 0)
        floor, line = pyproject_floor(path)
        if floor == want:
            return []
        have = "無し" if floor is None else ".".join(map(str, floor))
        return [self.finding(rel, line, f"requires-python が許す最も古い Python {have} が下限 {'.'.join(map(str, want))} と違う")]

    def launchers(self) -> list[Finding]:
        return self.listed("launchers", self._launcher)

    def _launcher(self, rel: str, path: Path) -> list[Finding]:
        want = {"UV_VERSION": self.uv, "UV_INSTALLER_SHA256": self.digest}
        seen = _launcher_lines(path)
        found = []
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
        found += checker.workflows()
    return found
