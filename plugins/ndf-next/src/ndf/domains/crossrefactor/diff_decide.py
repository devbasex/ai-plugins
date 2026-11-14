"""差分（`git diff -U0`）から、ファイルごとに触った行を読む。

触った行は変更の後のファイルの行番号で持つ。足した行はその行、消した行は消した位置の直前の行（変更の後のファイルで
消した行が挟まっていた所）に数える。関数の本文の行を消しただけの差分も、その関数を触ったと読むためである。
差分の読みは unidiff に任せる（自前で `@@` を読まない）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import unidiff


class DiffError(ValueError):
    """差分を読めない。"""


@dataclass(frozen=True)
class FileChange:
    """1 つのファイルの変更。`path` は変更の後のパス（消したファイルは前のパス）。"""

    path: str
    lines: frozenset[int]
    added: bool = False
    removed: bool = False


def _hunk_lines(hunk: unidiff.Hunk) -> set[int]:
    touched: set[int] = set()
    last_target: int | None = None
    for line in hunk:
        if line.target_line_no is not None:
            last_target = line.target_line_no
        if line.is_added:
            touched.add(line.target_line_no)
        elif line.is_removed:
            touched.add(max(1, last_target if last_target is not None else hunk.target_start))
    return touched


def file_changes(diff_text: str) -> Mapping[str, FileChange]:
    """差分の文字列からファイルごとの変更を返す。読めなければ `DiffError`。"""
    try:
        patch = unidiff.PatchSet(diff_text)
    except unidiff.UnidiffParseError as exc:
        raise DiffError(f"差分を読めない: {exc}") from exc
    out: dict[str, FileChange] = {}
    for pf in patch:
        lines: set[int] = set()
        for hunk in pf:
            lines |= _hunk_lines(hunk)
        out[pf.path] = FileChange(pf.path, frozenset(lines), added=pf.is_added_file, removed=pf.is_removed_file)
    return out


def in_scope(path: str, scope: tuple[str, ...]) -> bool:
    """`path` が範囲（ファイルかディレクトリの並び）の中か。`.` は作業場所の全体。"""
    for loc in scope:
        base = loc.rstrip("/")
        if base in ("", ".") or path == base or path.startswith(base + "/"):
            return True
    return False
