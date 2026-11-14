"""テストのファイルの見分けと、範囲テストに渡すテストのファイルの選び方。

- 項目の範囲テストは、項目が触るソースを import するか名前の対応するテストのファイルへ絞る。ディレクトリを渡さず、
  実質の全体テストにしない（#3049）
- 全体テストを CI に任せる戦略の着手前の範囲テストは、`--scope` の配下のテストのファイルだけを `{paths}` へ渡す。
  ファイルだけを受けるテンプレート（`python3 -m unittest {paths}` など）で落ちない（#2096）
- 足したテストが全体の実行に収集されるかを、宣言の `collect_command` の出力から分ける（#620）

テストのファイルの見分け方は領域の表 `test-files`（cross-review・試走の候補と同じ表）から読む。
"""

from __future__ import annotations

import ast
import fnmatch
import shlex
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from ndf.contracts import facts
from ndf.domains.crossrefactor.diff_decide import in_scope
from ndf.domains.crossrefactor.symbols_decide import PYTHON_SUFFIX

TABLE = "test-files"
PATHS_SLOT = "{paths}"
# 名前の対応（Python 以外のソースと、import を持たないテスト）。`{s}` はソースのファイル名の語幹
NAME_FORMS = ("test_{s}", "{s}_test", "{s}Test", "{s}Tests", "{s}_spec")


def is_test_file(path: str) -> bool:
    """表 `test-files` のどれか 1 行に当たればテストのファイル。"""
    p = PurePosixPath(path)
    for row in facts.rows(TABLE):
        if row["where"] == "name" and fnmatch.fnmatchcase(p.name, row["pattern"]):
            return True
        if row["where"] == "dir" and row["pattern"] in p.parts[:-1]:
            return True
    return False


def module_names(path: str) -> frozenset[str]:
    """Python のファイルが import されうる名前（パスの末尾から数えた各段の点区切り）。`pkg/__init__.py` は `pkg`。"""
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return frozenset(".".join(parts[i:]) for i in range(len(parts)))


def imports_of(source: str) -> frozenset[str]:
    """テストのファイルが import する名前。`from a import b` は `a` と `a.b` の両方。構文の誤りは `SyntaxError`。"""
    return frozenset(name for node in ast.walk(ast.parse(source)) for name in _imported(node))


def _imported(node: ast.AST) -> list[str]:
    """1 つのノードが import する名前（import でなければ空）。"""
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    base = node.module
    if not base:
        return [a.name for a in node.names]
    return [*(f"{base}.{a.name}" for a in node.names), base]


def _stem(path: str) -> str:
    return PurePosixPath(path).name.split(".", 1)[0]


def names_match(source: str, test: str) -> bool:
    """テストのファイルの名前が、ソースの語幹の名前の形（`test_<語幹>`・`<語幹>.test.ts` など）に当たるか。"""
    s, t = _stem(source), _stem(test)
    return t in {form.format(s=s) for form in NAME_FORMS} or (t == s and test != source and is_test_file(test))


@dataclass(frozen=True)
class PickedTests:
    """範囲テストに渡すテストのファイル。`unmapped` は対応するテストを引けなかったソース、`unreadable` は読めないテスト。"""

    files: tuple[str, ...]
    unmapped: tuple[str, ...]
    unreadable: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {"files": list(self.files), "unmapped": list(self.unmapped), "unreadable": list(self.unreadable)}


def _tests_of(source: str, imports: Mapping[str, frozenset[str]]) -> set[str]:
    names = module_names(source) if source.endswith(PYTHON_SUFFIX) else frozenset()
    picked = {t for t, imported in imports.items() if not imported.isdisjoint(names)}
    picked |= {t for t in imports if names_match(source, t)}
    return picked


def _parsed_imports(path: str, text: str | None) -> frozenset[str] | None:
    """テストの import。読めない・構文木にできないテストは None（名前の対応だけで選ぶ）。"""
    if text is None:
        return None
    if not path.endswith(PYTHON_SUFFIX):
        return frozenset()
    try:
        return imports_of(text)
    except SyntaxError:  # noqa: NDF401 ファイル: 構文の誤りのテストは import を読まず、名前の対応だけで選ぶ
        return None


def tests_for_change(changed: Iterable[str], tests: Mapping[str, str | None]) -> PickedTests:
    """触るファイルから範囲テストのファイルを選ぶ。

    `tests` はテストのファイル → 中身（読めなければ None）。触ったテストのファイルはそれ自身を選ぶ。
    Python のソースはそれを import するテスト、どの言語でも名前の対応するテストを選ぶ。返す並びにディレクトリは入らない。
    """
    imports: dict[str, frozenset[str]] = {}
    unreadable: list[str] = []
    for path, text in sorted(tests.items()):
        parsed = _parsed_imports(path, text)
        if parsed is None:
            unreadable.append(path)
        imports[path] = frozenset() if parsed is None else parsed
    files: set[str] = set()
    unmapped: list[str] = []
    for path in sorted(set(changed)):
        picked = {path} if is_test_file(path) else _tests_of(path, imports)
        files |= picked
        if not picked:
            unmapped.append(path)
    return PickedTests(tuple(sorted(files)), tuple(unmapped), tuple(unreadable))


def scope_test_files(tracked: Iterable[str], scope: Sequence[str]) -> list[str]:
    """追跡されるファイルのうち、範囲の配下のテストのファイル（ファイルだけで、ディレクトリを含まない）。"""
    return sorted(p for p in tracked if in_scope(p, tuple(scope)) and is_test_file(p))


def fill_paths(template: str, files: Sequence[str]) -> str:
    """テンプレートの `{paths}` へファイルを引用して入れる。`{paths}` の無いテンプレートは `ValueError`。"""
    if PATHS_SLOT not in template:
        raise ValueError(f"テンプレートに {PATHS_SLOT} が無い: {template}")
    return template.replace(PATHS_SLOT, " ".join(shlex.quote(f) for f in files))


@dataclass(frozen=True)
class CollectVerdict:
    """足したテストのファイルの収集の判定。どのファイルもちょうど 1 つに入る。"""

    collected: tuple[str, ...]
    uncollected: tuple[str, ...]
    undecided: tuple[str, ...]


def collected_files(output: str) -> frozenset[str]:
    """収集の出力（pytest の `--collect-only -q` の `<ファイル>::<名前>` の行、または 1 行 1 ファイル）からファイルを集める。"""
    files: set[str] = set()
    for raw in output.splitlines():
        line = raw.strip()
        if not line or " " in line.split("::", 1)[0]:
            continue
        files.add(line.split("::", 1)[0])
    return frozenset(files)


def collect_verdict(paths: Iterable[str], collected: Mapping[str, frozenset[str] | None]) -> CollectVerdict:
    """`collected` はファイル → そのファイルを受け持つ suite が収集したファイル（宣言が無ければ None）。"""
    sure: list[str] = []
    missed: list[str] = []
    undecided: list[str] = []
    for path in sorted(set(paths)):
        got = collected[path] if path in collected else None
        if got is None:
            undecided.append(path)
        elif path in got:
            sure.append(path)
        else:
            missed.append(path)
    return CollectVerdict(tuple(sure), tuple(missed), tuple(undecided))
