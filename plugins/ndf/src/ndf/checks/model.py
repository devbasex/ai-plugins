"""検査が共有する型: 指摘・見る範囲・1 回だけ解析するファイル・規則のデータ。

構文木は `Source.tree` が 1 ファイルにつき 1 回だけ作り、すべての規則が同じ木を読む（RF3）。規則は木を書き換えない。
"""

from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path, PurePosixPath
from typing import Any

from ndf import identity
from ndf.checks import imports

RULES_PATH = Path(__file__).with_name("rules.json")
# スクリプトを置くディレクトリ（プラグインの根からの相対パスの前置き）
SCRIPT_PREFIXES = ("bin/", "hooks/")
# 製品のコードを置くディレクトリ（プラグインの根からの相対パス）
SRC_DIR = "src"
# シンボリックリンクをたどる回数の上限（輪になったリンクで止まらないため。Linux の MAXSYMLINKS と同じ）
MAX_LINK_HOPS = 40


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
    """見る範囲。`sources` は `plugin_root` の下だけを持つ。"""

    repo_root: Path
    plugin_root: Path
    plugin_name: str
    sources: list[Source] = field(default_factory=list)

    @cached_property
    def settings(self) -> dict[str, Any]:
        """リポジトリの設定（`.ndf/project.json`）の値。設定を使う規則が最初に読んだときに 1 回だけ読む。

        設定を使わない規則だけを当てる検査は、設定のファイルが壊れていても止まらない。
        """
        return load_settings(self.repo_root)

    def rel(self, path: Path) -> str:
        return path.relative_to(self.repo_root).as_posix()

    def repo_files(self, glob: str) -> list[Path]:
        """`repo_root` からの相対のグロブに当たるファイル（プラグインの外も引ける）。並びは昇順。

        グロブが絶対パス・`..` を含む・空なら `CheckError`。解決した先がリポジトリの外のもの（外を指すシンボリックリンク）は返さない。
        """
        pure = PurePosixPath(glob)
        if not glob or pure.is_absolute() or Path(glob).is_absolute() or ".." in pure.parts:
            raise CheckError(f"リポジトリの根からの相対のグロブでない: {glob!r}")
        root = self.repo_root.resolve()
        try:
            matched = list(self.repo_root.glob(glob))
        except (ValueError, NotImplementedError) as exc:
            raise CheckError(f"グロブを解けない: {glob!r}: {exc}") from exc
        return sorted(p for p in matched if p.is_file() and p.resolve().is_relative_to(root))

    def plugin_rel(self, src: Source) -> str:
        """プラグインの根からの相対のパス（`src/ndf/...`・`tests/...`）。"""
        return src.path.relative_to(self.plugin_root).as_posix()

    def code(self) -> list[Source]:
        """製品のコード（`src/` の下の `.py`）。兆候・構造・輪・重複の規則が見る。"""
        return [s for s in self.sources if s.is_python and self.plugin_rel(s).startswith(SRC_DIR + "/")]

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


def module_rel(module: str, suffix: str) -> str:
    """`ndf.identity` と `.py` → `src/ndf/identity.py`（プラグインの根からの相対パス。`module_name` の逆）。"""
    return SRC_DIR + "/" + module.replace(".", "/") + suffix


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


def load_settings(repo_root: Path) -> dict[str, Any]:
    """リポジトリの設定（`.ndf/project.json`）の値。無ければ空の辞書、読めない・オブジェクトでなければ `CheckError`。

    シンボリックリンクは 1 つずつたどり、途中でリポジトリの外へ出た時点で `CheckError` にする（`_resolve_inside`）。
    外のパスは解決もしないので、外のファイル・リンクの有無は結果に出ない。
    """
    path = repo_root / identity.FIXED_WORDS["ndf_settings_dir"] / "project.json"
    path = _resolve_inside(repo_root, path)
    if not path.is_file():
        return {}
    data = read_json(path, "リポジトリの設定を読めない")
    if not isinstance(data, dict):
        raise CheckError(f"リポジトリの設定がオブジェクトでない: {path}")
    return data


def _resolve_inside(repo_root: Path, path: Path) -> Path:
    """`repo_root` の下の `path` のシンボリックリンクを 1 つずつ解いた実体のパス。

    リンクの先・`..` がリポジトリの外へ出た時点で `CheckError` にし、外のパスは `lstat` もしない（`Path.resolve` は
    外のリンクをたどってから内へ戻れば通すため、外のリンクの有無が結果に出る）。絶対パスのリンク先は、解決済みの根の
    パスで始まるものだけを内側として扱う。
    """
    root = repo_root.resolve()
    outside = CheckError(f"リポジトリの設定がリポジトリの外を指す: {path}")
    pending = list(path.relative_to(repo_root).parts)
    current = root
    hops = 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if current == root:
                raise outside
            current = current.parent
            continue
        step = current / part
        if not step.is_symlink():
            current = step
            continue
        hops += 1
        if hops > MAX_LINK_HOPS:
            raise CheckError(f"リポジトリの設定のリンクが深すぎる: {path}")
        target = Path(os.readlink(step))
        if target.is_absolute():
            if target.parts[: len(root.parts)] != root.parts:
                raise outside
            current = root
            pending = list(target.parts[len(root.parts) :]) + pending
        else:
            pending = list(target.parts) + pending
    return current


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    """規則のデータを読む。形の確かめは使う側のキーの参照（欠ければ `KeyError`）に任せず、ここでキーの有無を見る。"""
    data = read_json(path, "規則のデータを読めない")
    missing = [k for k in ("smells", "ruff", "boundaries", "exception_types", "structure", "facts", "words", "child_deps") if k not in data]
    if missing:
        raise CheckError(f"規則のデータにキーが無い: {path}: {missing}")
    bad = [code for code, rule in data["smells"].items() if rule["suppress"] not in ("none", "boundary", "boundary-or-limit")]
    if bad:
        raise CheckError(f"規則のデータの mark が知らない値: {bad}")
    return data
