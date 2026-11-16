"""リリースに含む PR の番号を、ベースブランチの first-parent の件名から決める（純粋な関数）。

件名の形は GitHub のマージの 2 通りである。

- merge commit: `Merge pull request #<番号> from <所有者>/<ブランチ>`
- squash: `<題> (#<番号>)`

リリースのブランチ（`release/v<版>`）からのマージ（前のリリースの PR）と、本番ブランチからのマージ（本番からベースへ
戻す PR。head は本番ブランチ）は含めない。
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable

from ndf.domains.release import version_decide

_MERGE = re.compile(r"^Merge pull request #(?P<number>\d+) from [^/\s]+/(?P<branch>\S+)")
_SQUASH = re.compile(r"\(#(?P<number>\d+)\)\s*$")


def _excluded(branch: str, skipped: Collection[str]) -> bool:
    """含めないブランチからのマージか（リリースのブランチか、`skipped` の本番ブランチ）。"""
    return branch.startswith(version_decide.BRANCH_PREFIX) or branch in skipped


def number_of(subject: str, skipped: Collection[str] = ()) -> int | None:
    """件名 1 行の PR の番号。PR のマージでない・リリースのブランチか `skipped` のブランチからのマージなら None。"""
    m = _MERGE.match(subject)
    if m is not None:
        return None if _excluded(m.group("branch"), skipped) else int(m.group("number"))
    s = _SQUASH.search(subject)
    return int(s.group("number")) if s is not None else None


def numbers(subjects: Iterable[str], skipped: Collection[str] = ()) -> list[int]:
    """含む PR の番号（重なりを除き、小さい順）。`skipped` は含めないブランチ（本番ブランチ）。"""
    return sorted({n for n in (number_of(s, skipped) for s in subjects) if n is not None})
