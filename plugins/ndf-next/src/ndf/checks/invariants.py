"""新しいプラグインの不変条件 I1〜I3（issues/issue-1142-design-next.md の「不変条件」）。

- I1: コード（`src/` の `.py`・`bin/`・`hooks/`）は、自分のプラグイン名・ディレクトリ名・状態の置き場の名前を文字で
  持たない。名前は `identity` が `plugin.json` から求める
- I2: 差分が旧いプラグインのディレクトリを変えない。即時修正だけは、PR 本文の印の行で許す
- I3: 旧いプラグインの Python を読まない。import は自分のパッケージ・標準ライブラリ・宣言した依存だけ
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
from collections import defaultdict
from importlib import metadata
from pathlib import Path
from typing import Any

from ndf.checks.model import CheckError, Finding, Scope, Source

OWN_PACKAGE = __name__.split(".")[0]
LOCAL_TOPS = frozenset({OWN_PACKAGE, "conftest"})
CODE_DIRS = ("bin/", "hooks/")


def _name_pattern(names: set[str]) -> re.Pattern[str]:
    """名前をパスの一部（`/<名前>`・`<名前>/`）か Skill の前置き（`<名前>:`）として持つ箇所。"""
    alts = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![\w-])(?:{alts})(?=[:/])|(?<=/)(?:{alts})(?![\w-])")


def _docstrings(tree: ast.Module) -> set[int]:
    owners = [tree, *(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))]
    return {id(o.body[0].value) for o in owners if o.body and isinstance(o.body[0], ast.Expr) and isinstance(o.body[0].value, ast.Constant)}


def _strings(tree: ast.Module) -> list[tuple[int, str]]:
    """文字列の定数（docstring を除く）を行とともに返す。"""
    docs = _docstrings(tree)
    return [(n.lineno, n.value) for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def _shell_lines(src: Source) -> list[tuple[int, str]]:
    """コメントの行を除いた行。"""
    return [(no, line) for no, line in enumerate(src.text.splitlines(), 1) if not line.lstrip().startswith("#")]


def own_name(scope: Scope) -> list[Finding]:
    """I1: コードが自分の名前を文字で持つ箇所。"""
    names = {scope.plugin_name, scope.plugin_root.name}
    pattern = _name_pattern(names)
    found = []
    for src in scope.sources:
        rel = scope.plugin_rel(src)
        if src.is_python and rel.startswith("src/"):
            texts = _strings(src.tree)
        elif rel.startswith(CODE_DIRS):
            texts = _shell_lines(src)
        else:
            continue
        found += [
            Finding("I1", src.rel, no, f"自分の名前を文字で持つ: {text.strip()[:80]}")
            for no, text in texts
            if text in names or pattern.search(text)
        ]
    return found


def declared_tops(plugin_root: Path) -> frozenset[str]:
    """`pyproject.toml` が宣言した依存（グループを含む）が入れる最上位のモジュールの名前。"""
    try:
        project = tomllib.loads((plugin_root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CheckError(f"pyproject.toml を読めない: {exc}") from exc
    specs = list(project["project"]["dependencies"])
    specs += [s for group in project["dependency-groups"].values() for s in group]
    dists = {_normalize(re.split(r"[<>=!~;\[ ]", s, maxsplit=1)[0]) for s in specs}
    installed = {top for top, owners in metadata.packages_distributions().items() if any(_normalize(o) in dists for o in owners)}
    # 入れていない依存（`--no-dev` の環境の開発用の依存）は、依存の名前から最上位のモジュールの名前を推す
    return frozenset(installed | {d.replace("-", "_") for d in dists})


def pythonpath_tops(plugin_root: Path) -> frozenset[str]:
    """pytest の `pythonpath`（`[tool.pytest.ini_options]`）のディレクトリにある `.py` のモジュールの名前。

    テストの補助のモジュール（`tests/support/` など）は、pytest がこのディレクトリをパスへ足して読ませる。
    """
    project = tomllib.loads((plugin_root / "pyproject.toml").read_text(encoding="utf-8"))
    section: Any = project
    for key in ("tool", "pytest", "ini_options", "pythonpath"):
        section = section[key] if isinstance(section, dict) and key in section else ()
    dirs = section if isinstance(section, list) else ()
    return frozenset(f.stem for d in dirs for f in (plugin_root / d).glob("*.py"))


def _normalize(dist: str) -> str:
    return re.sub(r"[-_.]+", "-", dist).lower()


def old_imports(scope: Scope, old_plugin: str) -> list[Finding]:
    """I3: 自分のパッケージ・標準ライブラリ・宣言した依存の外の import と、旧いプラグインのパスの文字。

    同じディレクトリの隣の `.py` と、pytest の `pythonpath` にある `.py`（テストの補助のモジュール）は自分のものとして許す。
    """
    root = scope.plugin_root
    allowed = LOCAL_TOPS | frozenset(sys.stdlib_module_names) | declared_tops(root) | pythonpath_tops(root)
    path_pattern = re.compile(rf"(?<![\w-]){re.escape(old_plugin)}(?=/|$)")
    siblings: dict[Path, set[str]] = defaultdict(set)
    for src in scope.python():
        siblings[src.path.parent].add(src.path.stem)
    found = []
    for src in scope.python():
        own = allowed | siblings[src.path.parent]
        for node in ast.walk(src.tree):
            tops = _import_tops(node)
            found += [Finding("I3", src.rel, node.lineno, f"宣言の外のモジュールを読む: {t}") for t in tops if t not in own]
        found += [
            Finding("I3", src.rel, no, f"旧いプラグインのパスを持つ: {text.strip()[:80]}")
            for no, text in _strings(src.tree)
            if path_pattern.search(text)
        ]
    return found


def _import_tops(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name.split(".")[0] for alias in node.names]
    if isinstance(node, ast.ImportFrom) and not node.level and node.module:
        return [node.module.split(".")[0]]
    return []


def changed_files(repo_root: Path, base: str, old_plugin: str) -> list[str]:
    """起点と HEAD の merge-base からの差分のうち、旧いプラグインのディレクトリの下のファイル。"""
    cmd = ["git", "-C", str(repo_root), "diff", "--name-only", f"{base}...HEAD", "--", old_plugin + "/"]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip()[:300] if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise CheckError(f"差分を取れない（起点 {base}。履歴をすべて取ってから実行する）: {detail}") from exc
    return [line for line in done.stdout.splitlines() if line]


def immediate_fix_reason(pr_body: str, mark: str) -> str:
    """PR 本文の、印で始まる行の理由。印の行が無いか理由が空なら空の文字列。"""
    reasons = [line.strip()[len(mark) :].strip() for line in pr_body.splitlines() if line.strip().startswith(mark)]
    return next((r for r in reasons if r), "")


def old_plugin_diff(scope: Scope, conf: dict[str, Any]) -> list[Finding]:
    """I2: 差分が旧いプラグインを変えた箇所。起点が無ければ見ない。即時修正の印があれば許す。"""
    if scope.base is None:
        return []
    files = changed_files(scope.repo_root, scope.base, conf["old_plugin"])
    if not files or immediate_fix_reason(scope.pr_body, conf["immediate_fix_mark"]):
        return []
    hint = f"即時修正なら PR 本文に「{conf['immediate_fix_mark']} <理由>」の行を書く"
    return [Finding("I2", f, 1, f"旧いプラグインを変えた。{hint}") for f in files]


def check(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    conf = rules["invariants"]
    return own_name(scope) + old_imports(scope, conf["old_plugin"]) + old_plugin_diff(scope, conf)
