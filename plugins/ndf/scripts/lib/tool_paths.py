"""ツールのパス（#1436）: CLI が起動したツール（Serena MCP など）が worktree の中で書き換える既知のパス。

cross-review・cross-refactoring・`pr` の 3 つの工程は、定義をこのモジュールの `load` からだけ得る
（工程ごとに既定を持たない）。定義は既定の 2 つに `.ndf/worktree.json` の `tool_paths.add` を足し、
`tool_paths.remove` を引いたもの。項目は完全一致か、末尾 `/` の前方一致で照合する。

**ファイルの中身を読まない。** 扱うのは git が返すパスだけで、案内にもパスだけを出す
（中身に `auth_secret` を含み得る）。
"""

from __future__ import annotations

import sys
from typing import Iterable, NamedTuple, Sequence

import proc
import repo

# NDF と mcp-serena が `SERENA_HOME=.serena` で起動する Serena の書き先。
DEFAULT: tuple[str, ...] = (".serena/project.yml", ".serena/serena_config.yml")
DESCRIBE_HEAD = "↷ ツールのパスを検査から外した:"


class ToolPathsUnreadable(Exception):
    """`.ndf/worktree.json` があるのに `tool_paths` を読めない。既定へ戻さずに止める。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason

    @property
    def message(self) -> str:
        """案内の 1 行（先頭の `❌` は呼び出し側の中断が付ける）。"""
        return f"{repo.WORKTREE_DECL} の tool_paths を読めない: {self.reason}。直してから打ち直す"


class Split(NamedTuple):
    user: list[str]
    tool: list[str]


def _string_list(value, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ToolPathsUnreadable(f"tool_paths.{key} が文字列の配列でない")
    return list(value)


def load(root) -> list[str]:
    """`root` の直下の `.ndf/worktree.json` から定義を読む（メインディレクトリへ探しに行かない）。

    設定が無い・`tool_paths` が無いときは既定。読めないときは `ToolPathsUnreadable`。
    """
    data, reason = repo.read_worktree_decl(root, fallback=False)
    if reason:
        raise ToolPathsUnreadable(reason)
    if "tool_paths" not in data:
        return list(DEFAULT)
    if data.get("version") != 1:
        raise ToolPathsUnreadable(f"version が 1 でない（{data.get('version')!r}）")
    section = data["tool_paths"]
    if not isinstance(section, dict):
        raise ToolPathsUnreadable("tool_paths がオブジェクトでない")
    add = _string_list(section.get("add"), "add")
    remove = set(_string_list(section.get("remove"), "remove"))
    return [p for p in dict.fromkeys([*DEFAULT, *add]) if p not in remove]


def matches(path: str, entries: Iterable[str]) -> bool:
    """完全一致か、末尾 `/` の項目の前方一致なら真。"""
    return any(path == e or (e.endswith("/") and path.startswith(e)) for e in entries)


def split(paths: Iterable[str], entries: Sequence[str]) -> Split:
    """パスを利用者の変更とツールのパスに分ける。順序は入力のまま。"""
    user: list[str] = []
    tool: list[str] = []
    for p in paths:
        (tool if matches(p, entries) else user).append(p)
    return Split(user, tool)


def _git_paths(worktree, *args) -> list[str] | None:
    """`git --literal-pathspecs <args>`（`-z` の出力）のパス。失敗は `None`。"""
    try:
        p = proc.git(worktree, "--literal-pathspecs", *args, check=False)
    except OSError:
        return None
    if p.returncode != 0:
        return None
    return [x for x in p.stdout.split("\0") if x]


def tracked(worktree, entries: Sequence[str]) -> list[str]:
    """ツールのパスのうち追跡対象のもの。git が失敗したら空。"""
    if not entries:
        return []
    return _git_paths(worktree, "ls-files", "-z", "--", *entries) or []


def hidden(worktree, entries: Sequence[str]) -> list[str]:
    """ツールのパスのうち skip-worktree の印が掛かっているもの。"""
    if not entries:
        return []
    lines = _git_paths(worktree, "ls-files", "-v", "-z", "--", *entries) or []
    return [x[2:] for x in lines if x.startswith("S ")]


def _warn(action: str, detail: str) -> None:
    print(f"⚠ ツールのパスの印を{action}: {detail.strip()[:200]}", file=sys.stderr)


def hide(worktree, entries: Sequence[str]) -> list[str]:
    """追跡対象のツールのパスへ skip-worktree の印を掛ける。レビュー worktree でだけ呼ぶ。

    失敗しても止めない（空を返し、1 行の警告を出す）。
    """
    paths = tracked(worktree, entries)
    if not paths:
        return []
    p = proc.git(worktree, "--literal-pathspecs", "update-index", "--skip-worktree", "--", *paths, check=False)
    if p.returncode != 0:
        _warn("掛けられない", p.stderr)
        return []
    return paths


def release(worktree, entries: Sequence[str]) -> list[str]:
    """印を外し、ツールのパスの中身を HEAD へ戻す。**中身を捨てる。** レビュー worktree でだけ呼ぶ。"""
    paths = tracked(worktree, entries)
    if not paths:
        return []
    for args in (("update-index", "--no-skip-worktree", "--", *paths), ("checkout", "HEAD", "--", *paths)):
        p = proc.git(worktree, "--literal-pathspecs", *args, check=False)
        if p.returncode != 0:
            _warn("外せない", p.stderr)
            return []
    return paths


def unstage(worktree, entries: Sequence[str]) -> list[str]:
    """index に入ったツールのパスを外す。ファイルの中身は変えない。失敗は `proc.StepError`。"""
    staged = proc.git(worktree, "diff", "--cached", "--name-only", "-z").stdout.split("\0")
    paths = [p for p in staged if p and matches(p, entries)]
    if paths:
        proc.git(worktree, "--literal-pathspecs", "reset", "-q", "--", *paths)
    return paths


def committed(worktree, base: str, entries: Sequence[str]) -> list[str] | None:
    """`<base>...HEAD` で変わったツールのパス。比べられなければ `None`。"""
    changed = _git_paths(worktree, "diff", "--name-only", "-z", f"{base}...HEAD")
    if changed is None:
        return None
    return [p for p in changed if matches(p, entries)]


def describe(paths: Iterable[str]) -> str:
    """`↷ ツールのパスを検査から外した: a b` の 1 行。空なら空文字。"""
    paths = list(paths)
    return f"{DESCRIBE_HEAD} {' '.join(paths)}" if paths else ""


def push_blocked(worktree, base: str, entries: Sequence[str]) -> str | None:
    """push の直前の検査。止める理由の 1 行、止めなくてよければ `None`。"""
    found = committed(worktree, base, entries)
    if found is None:
        return "ツールのパスの有無を確かめられないため push しない"
    if found:
        return (
            f"ツールのパスがコミットに入っているため push しない: {' '.join(found)}。 .gitignore に入れるか、コミットから外してから打ち直す"
        )
    return None
