"""リリースに含む PR の番号を、ベースブランチの first-parent の件名から決める（純粋な関数）。

件名の形は GitHub のマージの 2 通りである。

- merge commit: `Merge pull request #<番号> from <所有者>/<ブランチ>`
- squash: `<題> (#<番号>)`

リリースブランチ（設定の書式に合う名前。呼び手が `is_release` で渡す）からのマージ（前のリリースの PR）と、本番ブランチからの
マージ（本番からベースへ戻す PR。head は本番ブランチ）は含めない。

`release_branch` のリリースの単位のマージ（件名 `Merge <スプリント> into <リリースブランチ>`。`units.MERGE_SUBJECT`）は
`is_unit_merge` で見分け、呼び手がその中の PR を数える（#3615 の決定 8）。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable

# リリースの単位のマージの件名の形（`units.MERGE_SUBJECT` と同じ形。`units` は git を読むので判断のモジュールから読まない）
UNIT_MERGE_SUBJECT = "Merge {unit} into {branch}"

_MERGE = re.compile(r"^Merge pull request #(?P<number>\d+) from [^/\s]+/(?P<branch>\S+)")
_SQUASH = re.compile(r"\(#(?P<number>\d+)\)\s*$")


_UNIT_MERGE = re.compile(
    "^"
    + re.escape(UNIT_MERGE_SUBJECT).replace(re.escape("{unit}"), r"(?P<unit>\S+)").replace(re.escape("{branch}"), r"(?P<branch>\S+)")
    + "$"
)


def _never(_name: str) -> bool:
    return False


def _excluded(branch: str, skipped: Collection[str], is_release: Callable[[str], bool]) -> bool:
    """含めないブランチからのマージか（リリースブランチか、`skipped` の本番ブランチ）。"""
    return is_release(branch) or branch in skipped


def number_of(subject: str, skipped: Collection[str] = (), is_release: Callable[[str], bool] = _never) -> int | None:
    """件名 1 行の PR の番号。PR のマージでない・リリースブランチか `skipped` のブランチからのマージなら None。"""
    m = _MERGE.match(subject)
    if m is not None:
        return None if _excluded(m.group("branch"), skipped, is_release) else int(m.group("number"))
    s = _SQUASH.search(subject)
    return int(s.group("number")) if s is not None else None


def numbers(subjects: Iterable[str], skipped: Collection[str] = (), is_release: Callable[[str], bool] = _never) -> list[int]:
    """含む PR の番号（重なりを除き、小さい順）。`skipped` は含めないブランチ（本番ブランチ）。"""
    return sorted({n for n in (number_of(s, skipped, is_release) for s in subjects) if n is not None})


def is_unit_merge(subject: str, is_release: Callable[[str], bool]) -> bool:
    """件名がリリースの単位のマージ（`Merge <スプリント> into <リリースブランチ>`）か。リリースブランチの部分が書式に合うときだけ真。"""
    m = _UNIT_MERGE.match(subject)
    return m is not None and is_release(m.group("branch"))
