"""設計の検査（`ndf records design check`。#2912 の受け入れ条件 4・決定 1）の、検査する文書を集める口。

引数のファイルか、起点との分岐点から worktree までの差分の設計成果物を集め、`design_check_rules.findings` を通して結果 JSON を
作る。差分は未コミットの変更と未追跡のファイルを含み、取得（fetch）はしない。git の起動は対象のファイルの数によらず決まった回数で、
差分の出力は利用者とプロジェクトの Git の設定に左右されない形に固定する（外部 diff・textconv・色・hunk の結合・改名の設定）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import cli
from ndf.adapters import git
from ndf.contracts import result
from ndf.core import issue_docs, start_point
from ndf.domains.records import design_check_rules as dc
from ndf.domains.records import mvv_material_decide as mm

TOOL = cli.tool_of(f"{__package__}.cli")
# 差分の形を固定する引数（`git.FIXED_DIFF`）に、パスを引用しない設定と改名の検出を足す。行番号を取る差分は近い hunk を結合させない
_NAMES_DIFF = ("-c", "core.quotePath=false", "diff", "--name-status", "-z", "--find-renames", *git.FIXED_DIFF)
_LINES_DIFF = ("-c", "core.quotePath=false", "diff", "-U0", "--inter-hunk-context=0", "--find-renames", *git.FIXED_DIFF)


class Target(NamedTuple):
    """検査する 1 ファイル。`lines` が None なら全行を見る。"""

    path: str
    text: str
    lines: Optional[frozenset[int]]


class DesignCheckError(Exception):
    """検査する文書を集められない（`stopped`（precondition））。"""

    kind = "precondition"


class _Changed(NamedTuple):
    """3 の差分の 1 件。`old` は改名の前のパス（改名でなければ None）、`new` は新しいパス、`whole` は全行を見るか。"""

    old: Optional[str]
    new: str
    whole: bool


class _Part(NamedTuple):
    """4 の差分の 1 ファイル分。`old` は改名の前のパス、`patch` は hunk（無ければ None。モードだけの変更は見出しを持たない）。"""

    old: Optional[str]
    patch: Optional[str]


def _worktree_text(root: Path, path: str) -> str:
    try:
        return (root / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DesignCheckError(f"{path} を読めない: {exc}") from None


def _tree_text(root: Path, path: str) -> str:
    """差分から列挙した `path` を、シンボリックリンクをたどらず作業ツリーの中の通常ファイルとして読む。

    どこかの階層がリンクなら（解決した先が `root` の下の同じパスでなければ）読まず止める。作業ツリーの外の文書の行を
    結果の `items[].text` へ出さないため。
    """
    top = root.resolve()
    p = top / path
    if p.resolve() != p or p.is_symlink() or not p.is_file():
        raise DesignCheckError(f"{path} は作業ツリーの中の通常ファイルでない（シンボリックリンクはたどらない）")
    return _worktree_text(top, path)


def from_files(root: Path, paths: Sequence[str]) -> list[Target]:
    """指定したファイルを、名前を問わず全行の対象として読む。"""
    return [Target(p, _worktree_text(root, p), None) for p in paths]


def _name_status(raw: str) -> list[_Changed]:
    """`git diff --name-status -z` の出力。削除を除き、追加・複製は全行、改名は前のパスを持つ。"""
    fields = raw.split("\0")
    out: list[_Changed] = []
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        if status[0] in "RC":
            old, new = fields[i + 1], fields[i + 2]
            i += 3
            out.append(_Changed(old, new, False) if status[0] == "R" else _Changed(None, new, True))
            continue
        path = fields[i + 1]
        i += 2
        if status[0] != "D":
            out.append(_Changed(None, path, status[0] == "A"))
    return out


def hunk_parts(patch: str) -> dict[str, _Part]:
    """`git diff -U0` の出力を、新しいパスごとの 1 ファイル分に分ける（読み方は MVV 判定の手元の差分と同じ `diff_rows`）。

    読めなければ空にし、どの対象も結び付かずに全行を見る。消したファイルは返さない。
    """
    try:
        rows = mm.diff_rows(patch)
    except mm.DiffError:
        return {}
    return {
        str(r["filename"]): _Part(r.get("previous_filename"), str(r["patch"]) if "patch" in r else None)
        for r in rows
        if r["status"] != "removed"
    }


def _lines_of(changed: _Changed, part: Optional[_Part], before: Optional[str], after: str) -> Optional[frozenset[int]]:
    """追跡済みの対象の見る行。結び付けられない・改名の組が食い違う・表の行が失われた・見出しに触れたなら全行（None）。

    hunk を持たない部分は、本文を変えない改名（見る行は無い）のほかは結び付けられないものとして全行を見る（モードだけの変更など）。
    """
    if part is None or part.old != changed.old or before is None:
        return None
    if part.patch is None:
        return frozenset() if part.old is not None else None
    patch = part.patch
    if "\n@@" not in f"\n{patch}":
        return None
    lines = dc.added_lines(patch)
    if lines is None or dc.anchors_lost(before, after) or dc.heading_touched(before, after, patch):
        return None
    return lines


def from_base(root: Path, base: str, remote: str, git_of: Callable[[Path], git.Git] = git.Git) -> list[Target]:
    """起点との分岐点から worktree までに足した・変えた・名前を変えた設計成果物と、ファイルごとに見る行。"""
    g = git_of(root)
    try:
        _, start = start_point.resolve(g, base, remote)
    except start_point.StartPointError as exc:
        raise DesignCheckError(exc.detail) from None
    try:
        fork = g.merge_base(start, "HEAD")
        changed = _name_status(g.run(*_NAMES_DIFF, fork, "--"))
        untracked = [p for p in g.run("ls-files", "--others", "--exclude-standard", "-z").split("\0") if p]
        changed += [_Changed(None, p, True) for p in untracked]
        changed = [c for c in changed if issue_docs.DESIGN_FILE.match(c.new)]
        tracked = [c for c in changed if not c.whole]
        found: dict[str, _Part] = {}
        befores: list[Optional[bytes]] = []
        if tracked:
            paths = [p for c in tracked for p in ((c.new,) if c.old is None else (c.old, c.new))]
            found = hunk_parts(g.run(*_LINES_DIFF, fork, "--", *paths))
            befores = g.blobs(*(f"{fork}:{c.old or c.new}" for c in tracked))
    except git.GitError as exc:
        raise DesignCheckError(f"起点 {base} からの差分を読めない: {exc}") from None
    before_of = {c.new: b for c, b in zip(tracked, befores)}
    out: list[Target] = []
    for c in changed:
        text = _tree_text(root, c.new)
        if c.whole:
            out.append(Target(c.new, text, None))
            continue
        raw = before_of.get(c.new)
        before = raw.decode("utf-8", "replace") if raw is not None else None
        out.append(Target(c.new, text, _lines_of(c, found.get(c.new), before, text)))
    return out


def check(targets: Sequence[Target]) -> dict[str, Any]:
    """対象ごとに指摘を集め、統一 JSON の結果（指摘なしは ok、ありは `check_failed`）を作る。"""
    found = [f for t in targets for f in dc.findings(t.text, t.path, t.lines)]
    if not found:
        return result.result(TOOL, result.OK, f"番号の無い委ね方 0 件（{len(targets)} ファイル）")
    items = [{"name": f"{f.path}:{f.line}", "rule": dc.RULE, "text": f.text} for f in found]
    detail = f"レッドラインの番号を示さずに判断を人へ残す行か、欄を欠く「人へ残す判断」の表の行が {len(found)} 件ある"
    return result.stopped(TOOL, "check_failed", detail, items=items)
