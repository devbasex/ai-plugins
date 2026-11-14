"""殻: 差分と作業場所のファイルを読み、触る関数（#622）と範囲テストのファイル（#2096・#3049）を組む。

git は差分（`git diff -U0`）と追跡されるファイルの一覧（`git ls-files`）の 2 回だけ起動する。ファイルの中身は作業場所の
ファイルから読むため、`head` は作業場所の HEAD に限る。判断は `symbols_decide`・`tests_decide` が持つ。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ndf.adapters.git import Git, GitError
from ndf.domains.crossrefactor import diff_decide, symbols_decide, tests_decide
from ndf.domains.crossrefactor.symbols_decide import PYTHON_SUFFIX, Touched


class ScopeError(Exception):
    """範囲を読めない（git の失敗・差分や構文木の読みの失敗・head が作業場所の HEAD でない）。"""


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):  # noqa: NDF401 ファイル: 読めないファイルは None として呼ぶ側が扱う
        return None


def _git(work: Path) -> Git:
    return Git(work)


def tracked(work: Path, scope: Sequence[str]) -> list[str]:
    """範囲の配下の追跡されるファイル。"""
    try:
        out = _git(work).run("ls-files", "-z", "--", *scope)
    except GitError as exc:
        raise ScopeError(str(exc)) from exc
    return [p for p in out.split("\0") if p]


def touched(work: Path, base: str, head: str, scope: Sequence[str]) -> Touched:
    """`base..head` の差分が触る関数を、範囲の中のファイルごとに返す。読めなければ `ScopeError`（ファイル単位へ倒さない）。"""
    git = _git(work)
    try:
        if git.rev_parse(head) != git.rev_parse("HEAD"):
            raise ScopeError(f"{head} は作業場所の HEAD でない（中身を作業場所のファイルから読むため）")
        diff = git.run("diff", "-U0", "--no-color", "--no-ext-diff", f"{base}..{head}", "--", *scope)
        changes = diff_decide.file_changes(diff)
    except (GitError, diff_decide.DiffError) as exc:
        raise ScopeError(str(exc)) from exc
    sources: dict[str, str] = {}
    for path, change in changes.items():
        text = None if change.removed or not path.endswith(PYTHON_SUFFIX) else _read(work / path)
        if text is not None:
            sources[path] = text
    try:
        return symbols_decide.touched_symbols(changes, sources, tuple(scope))
    except symbols_decide.SymbolsError as exc:
        raise ScopeError(str(exc)) from exc


def scope_test_files(work: Path, scope: Sequence[str]) -> list[str]:
    """着手前の範囲テストへ渡す、範囲の配下のテストのファイル（ディレクトリを含まない。#2096）。"""
    return tests_decide.scope_test_files(tracked(work, scope), scope)


def tests_for_change(work: Path, changed: Sequence[str]) -> tests_decide.PickedTests:
    """項目が触るファイルに対応するテストのファイル（#3049）。作業場所の追跡されるテストのファイルから選ぶ。"""
    tests = {p: _read(work / p) for p in tracked(work, ["."]) if tests_decide.is_test_file(p)}
    return tests_decide.tests_for_change(changed, tests)
