"""プラグインの不変条件 I1・I3（issues/issue-1142-design-next.md の「不変条件」）。

- I1: コード（`src/` の `.py`・`bin/`・`hooks/` のスクリプト）は、自分のプラグイン名・ディレクトリ名・状態の保存先の名前を
  文文字列で持たない。名前は `identity` が `plugin.json` から求める。`hooks/` の `.json`（hook の登録）は `ndf hooks render` が
  `plugin.json` から作る生成物で、検証しない（名前を変えたら作り直す）
- I3: import は自分のパッケージ・標準ライブラリ・定義した依存だけ

旧いプラグイン（10 系）と並べて作った間の I2（子の PR が旧いプラグインを変えない）・規則 port（旧いプラグインの直しを移す）・
I3 の旧いプラグインのパスの文字は、入れ替え（#3304）で旧いプラグインが無くなったため外した。
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from collections import defaultdict
from collections.abc import Mapping
from importlib import metadata
from pathlib import Path
from typing import Any

from ndf import identity
from ndf.checks.model import SCRIPT_PREFIXES, SRC_DIR, CheckError, Finding, Scope, Source, module_rel, string_constants

OWN_PACKAGE = __name__.split(".")[0]
LOCAL_TOPS = frozenset({OWN_PACKAGE, "conftest"})
# I1 が定義の行を通す `identity` のファイル（プラグインの根からの相対パス）と、通す名前
IDENTITY_FILE = module_rel(identity.__name__, ".py")
DEFINITION_NAMES = frozenset({"FINAL_NAME", "FIXED_WORDS"})
# Python のパッケージの形（`src/<パッケージ>`・`<パッケージ>.<モジュール>`）。プラグイン名から切り離した固定の名前で、I1 は検証しない
PACKAGE_FORM = re.compile(rf"(?<![\w.-])(?:src/{OWN_PACKAGE}(?![\w-])|{OWN_PACKAGE}\.(?=[A-Za-z_]))")


def _name_pattern(names: set[str]) -> re.Pattern[str]:
    """名前をパスの一部（`/<名前>`・`<名前>/`）か Skill の前置き（`<名前>:`）として持つ箇所。"""
    alts = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![\w-])(?:{alts})(?=[:/])|(?<=/)(?:{alts})(?![\w-])")


def _shell_lines(src: Source) -> list[tuple[int, str]]:
    """コメントの行を除いた行。"""
    return [(no, line) for no, line in enumerate(src.text.splitlines(), 1) if not line.lstrip().startswith("#")]


def own_name(scope: Scope) -> list[Finding]:
    """I1: コードが自分の名前を文文字列で持つ箇所（#3347 の決定 101）。

    名前の文字列そのものは今の名前と入れ替えの後の名前（`identity.FINAL_NAME`）で、パスと前置きはそれに
    ディレクトリ名を足して検証する。Python のパッケージの形（`src/ndf/`・`ndf.` で始まるモジュール名）は検証しない。
    `identity` の固定の語の表（`FIXED_WORDS`）と `FINAL_NAME` の定義の行だけは通す。
    """
    exact = {scope.plugin_name, identity.FINAL_NAME, *identity.FIXED_WORDS.values()}
    pattern = _name_pattern(exact | {scope.plugin_root.name})
    return [
        Finding("I1", src.rel, no, f"自分の名前を文文字列で持つ: {text.strip()[:80]}")
        for src in scope.sources
        for no, text in _own_name_texts(scope, src)
        if text in exact or pattern.search(PACKAGE_FORM.sub("", text))
    ]


def _own_name_texts(scope: Scope, src: Source) -> list[tuple[int, str]]:
    """I1 が検証する行と文字。製品の Python は文字列の定数（`identity` の定義の行を除く）、スクリプトはコメントを除いた行。

    `bin/`・`hooks/` の `.json`（`ndf hooks render` が作る登録）は検証しない。

    ほかのファイルは検証しない（空）。
    """
    rel = scope.plugin_rel(src)
    if src.is_python and rel.startswith(SRC_DIR + "/"):
        skip = _definition_lines(src.tree) if rel == IDENTITY_FILE else set()
        return [(no, text) for no, text in string_constants(src.tree) if no not in skip]
    if rel.startswith(SCRIPT_PREFIXES) and src.path.suffix != ".json":
        return _shell_lines(src)
    return []


def _definition_lines(tree: ast.Module) -> set[int]:
    """`identity` の最上位で `DEFINITION_NAMES` に代入する文の行。"""
    lines: set[int] = set()
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id in DEFINITION_NAMES for t in targets):
            lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return lines


def _pyproject(plugin_root: Path) -> dict[str, Any]:
    """`pyproject.toml` の中身。読めなければ `CheckError`。"""
    try:
        return tomllib.loads((plugin_root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CheckError(f"pyproject.toml を読めない: {exc}") from exc


def declared_tops(plugin_root: Path) -> frozenset[str]:
    """`pyproject.toml` が定義した依存（グループを含む）が入れる最上位のモジュールの名前。"""
    project = _pyproject(plugin_root)
    dists = _declared_dists(project)
    return _tops_from(dists, metadata.packages_distributions())


def _declared_dists(project: dict[str, Any]) -> set[str]:
    """`pyproject.toml` の値から、定義した依存（グループを含む）の正規化したパッケージ（distribution）の名前。"""
    specs = list(project["project"]["dependencies"])
    specs += [s for group in project["dependency-groups"].values() for s in group]
    return {_normalize(re.split(r"[<>=!~;\[ ]", s, maxsplit=1)[0]) for s in specs}


def _tops_from(dists: set[str], packages: Mapping[str, list[str]]) -> frozenset[str]:
    """パッケージ（distribution）の名前と、最上位のモジュール → 持つパッケージの対応表（`packages_distributions()` の形）から最上位の名前を求める。"""
    installed = {top for top, owners in packages.items() if any(_normalize(o) in dists for o in owners)}
    # 入れていない依存（`--no-dev` の環境の開発用の依存）は、依存の名前から最上位のモジュールの名前を推す
    return frozenset(installed | {d.replace("-", "_") for d in dists})


def pythonpath_tops(plugin_root: Path) -> frozenset[str]:
    """pytest の `pythonpath`（`[tool.pytest.ini_options]`）のディレクトリにある `.py` のモジュールの名前。

    テストの補助のモジュール（`tests/support/` など）は、pytest がこのディレクトリをパスへ足して読ませる。
    """
    section: Any = _pyproject(plugin_root)
    for key in ("tool", "pytest", "ini_options", "pythonpath"):
        section = section[key] if isinstance(section, dict) and key in section else ()
    dirs = section if isinstance(section, list) else ()
    return frozenset(f.stem for d in dirs for f in (plugin_root / d).glob("*.py"))


def _normalize(dist: str) -> str:
    return re.sub(r"[-_.]+", "-", dist).lower()


def undeclared_imports(scope: Scope) -> list[Finding]:
    """I3: 自分のパッケージ・標準ライブラリ・定義した依存の外の import。

    同じディレクトリの隣の `.py` と、pytest の `pythonpath` にある `.py`（テストの補助のモジュール）は自分のものとして許す。
    """
    root = scope.plugin_root
    allowed = LOCAL_TOPS | frozenset(sys.stdlib_module_names) | declared_tops(root) | pythonpath_tops(root)
    siblings: dict[Path, set[str]] = defaultdict(set)
    for src in scope.python():
        siblings[src.path.parent].add(src.path.stem)
    found = []
    for src in scope.python():
        found += _undeclared_imports(src, allowed | siblings[src.path.parent])
    return found


def _undeclared_imports(src: Source, own: frozenset[str]) -> list[Finding]:
    """`own` の外の最上位のモジュールを読む import。"""
    found = []
    for node in ast.walk(src.tree):
        found += [Finding("I3", src.rel, node.lineno, f"設定の外のモジュールを読む: {t}") for t in _import_tops(node) if t not in own]
    return found


def _import_tops(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name.split(".")[0] for alias in node.names]
    if isinstance(node, ast.ImportFrom) and not node.level and node.module:
        return [node.module.split(".")[0]]
    return []


def check(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    return own_name(scope) + undeclared_imports(scope)
