"""指示書を集める処理と本文の読み方（`instructions-check.py` から分けた。#1142 の C7）。

プロジェクトの追跡しているファイルと、宣言のスコープの場所から対象を集め、即時読み込みの参照を読む。
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pathmatch

from instructions_lib.model import CheckError, ScopeRoot, Source, Target, code_lines
from instructions_lib.declaration import Declaration


# 即時読み込みの参照。**`@` の直前が行頭・空白・`*`・`_`・`~` のものだけを参照とする**
# （Claude Code 2.1.274 で 8 通りを実測した範囲）。
IMPORT_RE = re.compile(r"(?:(?<=^)|(?<=[\s*_~]))@([A-Za-z0-9._\-/~]+)")


# --- 指示書を集める ----------------------------------------------------------

def _matches_file_pattern(rel: str, patterns: list[str]) -> bool:
    """`/` を含まない値はどの階層のファイル名にも、含む値は根からの相対パスに当てる（git の wildmatch）。"""
    return pathmatch.path_matches(rel, [p if "/" in p else f"**/{p}" for p in patterns])


def _expand(value: str, root: Path) -> Path:
    """先頭の `~` を home へ広げ、相対で書かれた値は `--root` からの相対として読む。"""
    path = Path(os.path.expanduser(value))
    return path if path.is_absolute() else (root / path)


def collect_project(root: Path, decl: Declaration) -> ScopeRoot:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CheckError(f"git が使えない（{root}）: {exc}") from exc
    scope_root = ScopeRoot(root=root, scope="project", imports=decl.imports)
    # 非 ASCII のパスを 8 進で表さない形で受け取るため `-z` を使う。
    for rel in sorted(entry for entry in result.stdout.split("\0") if entry):
        if not _matches_file_pattern(rel, decl.files):
            continue
        path = root / rel
        # **追跡されていても、実体が根の外を指す symlink は読まない。** 字句の上では
        # 中にある指示書でも、開いた先は作業ツリーの外である。
        if not path.is_file() or not _inside_root(str(path), root):
            continue
        scope_root.targets.append(Target(
            path=path, rel=rel, scope="project", root=root,
            is_root_file="/" not in rel, imports=decl.imports,
        ))
    return scope_root


def _build_scope_metadata(
    entry: dict, scope: str,
) -> tuple[Source | None, dict | None]:
    source = None
    if scope == "plugins":
        source = Source(
            name=str(entry.get("name", "")), version=str(entry.get("version", "")),
            origin=str(entry.get("origin", "")), update=str(entry.get("update", "")),
            ndf=bool(entry.get("ndf", False)),
        )
    allow = entry.get("imports")
    if allow is not None and not isinstance(allow, dict):
        raise CheckError(f"宣言の scopes.{scope} の imports がオブジェクトではない")
    return source, allow


def _enumerate_scope_files(
    located: Path, scope: str, allow: dict | None, source: Source | None,
) -> tuple[ScopeRoot, list[Path]] | None:
    if located.is_file():
        return ScopeRoot(located.parent, scope, allow, source), [located]
    if located.is_dir():
        scope_root = ScopeRoot(located, scope, allow, source)
        files = sorted(p for p in located.rglob("*") if p.is_file())
        return scope_root, files
    return None


def _collect_scope_entry(
    entry: dict, scope: str, root: Path, decl: Declaration,
) -> ScopeRoot | None:
    """`scopes` の 1 件から集める。位置が無ければ `None` を返す。"""
    raw_path = entry.get("path")
    if not isinstance(raw_path, str):
        raise CheckError(f"宣言の scopes.{scope} の path が無いか文字列ではない")
    located = _expand(raw_path, root)
    source, allow = _build_scope_metadata(entry, scope)
    found = _enumerate_scope_files(located, scope, allow, source)
    if found is None:
        return None
    scope_root, files = found
    for path in files:
        rel = path.relative_to(scope_root.root).as_posix()
        if located.is_dir() and not _matches_file_pattern(rel, decl.files):
            continue
        if not _inside_root(str(path), scope_root.root):
            continue
        scope_root.targets.append(Target(
            path=path, rel=rel, scope=scope, root=scope_root.root,
            is_root_file="/" not in rel, imports=allow, source=source,
        ))
    return scope_root


def collect_declared(scope: str, root: Path, decl: Declaration) -> list[ScopeRoot]:
    """`scopes` が挙げた位置から集める。**宣言に無ければそのスコープは走査しない。**"""
    roots: list[ScopeRoot] = []
    for entry in decl.scopes.get(scope) or []:
        scope_root = _collect_scope_entry(entry, scope, root, decl)
        if scope_root is not None:
            roots.append(scope_root)
    return roots


def display_path(target: Target) -> str:
    return target.rel if target.scope == "project" else str(target.path)


# --- 本文の読み方 ------------------------------------------------------------

def mask_code(text: str) -> list[str]:
    """コードブロックとコードスパンを空白へ潰した行の並び。**位置は保つ**（行と桁が動かない）。"""
    masked: list[str] = []
    for line, fenced in zip(text.splitlines(), code_lines(text)):
        if fenced:
            masked.append(" " * len(line))
            continue
        out: list[str] = []
        span = False
        for char in line:
            if char == "`":
                span = not span
                out.append(" ")
            else:
                out.append(" " if span else char)
        masked.append("".join(out))
    return masked


def interprets_imports(target: Target, decl: Declaration) -> bool:
    return _matches_file_pattern(target.rel, decl.import_syntax)


def references(text: str) -> list[tuple[int, str]]:
    """本文から即時読み込みの参照を拾う。返すのは（行番号, 書かれたとおりの名前）。"""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(mask_code(text), start=1):
        for match in IMPORT_RE.finditer(line):
            name = match.group(1).rstrip("._~")
            if name:
                found.append((number, name))
    return found


def _inside_root(candidate: str, root: Path) -> bool:
    """字句のパスと実体のパスが、どちらも根の中に収まるか。"""
    try:
        Path(candidate).relative_to(root)
        # **読む前に実体のパスが根の中に収まることを確かめる。** 字句の上では中でも、
        # 追跡された symlink が外を指していれば、開いた先は作業ツリーの外である。
        Path(os.path.realpath(candidate)).relative_to(Path(os.path.realpath(root)))
    except ValueError:
        return False
    return True


def resolve(name: str, base: Path, root: Path) -> Path | None:
    """参照先を解く。**解けないもの（`~`・根の外）は `None`** で、存在を判定しない。"""
    if name.startswith("~") or name.startswith("/"):
        return None
    candidate = os.path.normpath(str(base / name))
    if not _inside_root(candidate, root):
        return None
    return Path(candidate)
