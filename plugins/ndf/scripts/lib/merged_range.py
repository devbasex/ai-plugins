"""ベースブランチへ入った PR の拾い出し（#1274）。検査（`check-trigger.py`）と配布（`release_lib/included.py`）が使う。

`<起点>..<終点>` の first-parent のログから、merge commit（件名 `Merge pull request #N from <owner>/<branch>`）と
squash merge（件名の末尾 `(#N)`）で入った PR を拾う。squash の件名には head のブランチが残らないため、
`with_heads` が GitHub から 100 件ずつまとめて読む。**どの PR を外すかは持たない**（呼び手が決める）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

import gh_rest
import proc
from proc import StepError

MERGE_SUBJECT = re.compile(r"^Merge pull request #(\d+) from [^/\s]+/(\S+)")
SQUASH_SUBJECT = re.compile(r"\(#(\d+)\)$")  # squash merge の既定の件名「<題> (#N)」


@dataclass(frozen=True)
class Merge:
    """ベースブランチ上のマージ。branch は merge commit なら件名から、squash なら with_heads まで None、
    PR でない番号（squash の件名の `(#N)` が課題を指すなど）なら空文字。"""

    sha: str
    number: int
    branch: str | None


def _merge_of_line(parents: str, subject: str) -> tuple[int, str | None] | None:
    """first-parent のログの 1 行（親の列・件名）が PR のマージなら (番号, merge commit の head のブランチ または None)。"""
    if " " in parents:
        m = MERGE_SUBJECT.match(subject)
        return (int(m.group(1)), m.group(2)) if m else None
    m = SQUASH_SUBJECT.search(subject.rstrip())
    return (int(m.group(1)), None) if m else None


def subject_pr_number(parents: str, subject: str) -> int | None:
    """first-parent のログの 1 行（親の列・件名）から読める PR の番号。merge commit の `Merge pull request #N`
    と squash の末尾 `(#N)` を読む。読めなければ None（#1834 の指示書の変更も使う）。"""
    m = _merge_of_line(parents, subject)
    return m[0] if m else None


def merges_in(root: Path, frm: str, to: str) -> list[Merge]:
    """`frm..to` の first-parent のログで、merge commit か squash merge として入った PR（ログの順）。
    git が読めなければ StepError（2）。"""
    try:
        p = proc.git(root, "log", "--first-parent", "--format=%H%x09%P%x09%s", f"{frm}..{to}", check=False)
    except OSError:
        raise StepError("git が無い", 2)
    if p.returncode != 0:
        raise StepError(f"git log {frm}..{to}: {p.stderr.strip()[:300]}", 2)
    out = []
    for line in p.stdout.splitlines():
        sha, parents, subject = line.split("\t", 2)
        if m := _merge_of_line(parents, subject):
            out.append(Merge(sha, *m))
    return out


def nested_merges(root: Path, merge: Merge) -> list[Merge]:
    """merge commit `merge` の 2 つ目の親の側（`<sha>^1..<sha>^2` の first-parent のログ）へ merge commit で入った PR
    （#1885 のスプリント経由の PR）。squash の件名の `(#N)` は課題を指すことが多いため拾わない。squash の `merge`
    （親が 1 つ）は空。`merges_in` の返す値は変えない（`check-trigger.py` の起動の範囲を保つ）。git が読めなければ StepError（2）。"""
    try:
        p = proc.git(root, "log", "--first-parent", "--format=%H%x09%P%x09%s", f"{merge.sha}^1..{merge.sha}^2", check=False)
    except OSError:
        raise StepError("git が無い", 2)
    if p.returncode != 0:
        if "unknown revision" in p.stderr or "bad revision" in p.stderr:  # 親が 1 つ（squash）
            return []
        raise StepError(f"git log {merge.sha[:12]}^1..^2: {p.stderr.strip()[:300]}", 2)
    out = []
    for line in p.stdout.splitlines():
        sha, parents, subject = line.split("\t", 2)
        if " " in parents and (m := MERGE_SUBJECT.match(subject)):
            out.append(Merge(sha, int(m.group(1)), m.group(2)))
    return out


def with_heads(root: Path, merges: list[Merge]) -> list[Merge]:
    """branch が None（squash）の番号だけを GitHub から読んで埋める。読めなければ StepError（1）。"""
    need = [m.number for m in merges if m.branch is None]
    if not need:
        return list(merges)
    heads, err = gh_rest.pr_head_branches(need, cwd=str(root))
    if heads is None:
        raise StepError(f"squash merge の PR のブランチを読めない: {err}", 1)
    return [m if m.branch is not None else replace(m, branch=heads.get(m.number, "")) for m in merges]
