"""検査が共有する型: 指摘・見る範囲・1 回だけ解析するファイル・規則のデータ。

構文木は `Source.tree` が 1 ファイルにつき 1 回だけ作り、すべての規則が同じ木を読む（RF3）。規則は木を書き換えない。
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from ndf.checks import imports

RULES_PATH = Path(__file__).with_name("rules.json")
# スクリプトを置くディレクトリ（プラグインの根からの相対パスの前置き）
SCRIPT_PREFIXES = ("bin/", "hooks/")


class CheckError(Exception):
    """規則のデータ・用語集・git が読めず、検査を始められない。"""


@dataclass(frozen=True)
class Finding:
    """1 つの規則が 1 つの箇所で見つけた違反。`path` はリポジトリの根からの相対のパス。"""

    rule: str
    path: str
    line: int
    detail: str

    def as_item(self) -> dict[str, Any]:
        return {"rule": self.rule, "path": self.path, "line": self.line, "detail": self.detail}


@dataclass
class Source:
    """見るファイル 1 本。本文と構文木は最初に読んだときに 1 回だけ作る。"""

    path: Path
    rel: str

    @cached_property
    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @cached_property
    def tree(self) -> ast.Module:
        """構文の誤りは `SyntaxError` のまま送出する（検査のエントリポイントが `syntax` の違反にする）。"""
        return ast.parse(self.text, filename=self.rel)

    @property
    def is_python(self) -> bool:
        return self.path.suffix == ".py"


@dataclass
class Scope:
    """見る範囲。`plugin_root` の下だけを見る。`base` が無ければ差分の検査（I2）を飛ばす。"""

    repo_root: Path
    plugin_root: Path
    plugin_name: str
    base: str | None = None
    pr_body: str = ""
    sources: list[Source] = field(default_factory=list)

    def rel(self, path: Path) -> str:
        return path.relative_to(self.repo_root).as_posix()

    def plugin_rel(self, src: Source) -> str:
        """プラグインの根からの相対のパス（`src/ndf/...`・`tests/...`）。"""
        return src.path.relative_to(self.plugin_root).as_posix()

    def code(self) -> list[Source]:
        """製品のコード（`src/` の下の `.py`）。兆候・構造・輪・重複の規則が見る。"""
        return [s for s in self.sources if s.is_python and self.plugin_rel(s).startswith("src/")]

    def code_files(self) -> list[Source]:
        """コードのファイル（テストを含む `.py` と `bin/`・`hooks/` のスクリプト）。行数の上限が見る。"""
        return [s for s in self.sources if s.is_python or self.plugin_rel(s).startswith(SCRIPT_PREFIXES)]

    def modules(self) -> dict[str, Source]:
        """製品のコード（`code()`）をモジュール名 → ファイルの表にする。"""
        return {module_name(self, s): s for s in self.code()}

    def python(self) -> list[Source]:
        """テストを含む `.py` のすべて。I3 が見る。"""
        return [s for s in self.sources if s.is_python]


def module_name(scope: Scope, src: Source) -> str:
    """`src/ndf/core/loop.py` → `ndf.core.loop`。`__init__.py` はパッケージの名前。"""
    parts = list(src.path.relative_to(scope.plugin_root / "src").with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def source_imports(src: Source, module: str) -> list[imports.Import]:
    """ファイルの import（`__init__.py` はパッケージとして相対の import を解く）。"""
    return imports.collect(src.tree, module, src.path.name == "__init__.py")


def read_json_text(path: Path | str, unreadable: str) -> tuple[str, Any]:
    """JSON のファイルの本文と値。読めない・JSON でなければ `CheckError`（`<unreadable>: <渡したパス>: <例外>`）。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
        return text, json.loads(text)
    except (OSError, ValueError) as exc:
        raise CheckError(f"{unreadable}: {path}: {exc}") from exc


def read_json(path: Path | str, unreadable: str) -> Any:
    """JSON のファイルの値。読めないときは `read_json_text` と同じ `CheckError`。"""
    return read_json_text(path, unreadable)[1]


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    """規則のデータを読む。形の確かめは使う側の鍵の参照（欠ければ `KeyError`）に任せず、ここで鍵の有無を見る。"""
    data = read_json(path, "規則のデータを読めない")
    missing = [
        k
        for k in ("smells", "ruff", "boundaries", "exception_types", "structure", "facts", "words", "child_deps", "invariants")
        if k not in data
    ]
    if missing:
        raise CheckError(f"規則のデータに鍵が無い: {path}: {missing}")
    bad = [code for code, rule in data["smells"].items() if rule["mark"] not in ("none", "boundary", "boundary-or-limit")]
    if bad:
        raise CheckError(f"規則のデータの mark が知らない値: {bad}")
    return data
