"""リリースに含む PR の番号を、ベースブランチの first-parent の件名から決める（純粋な関数）。

件名の形は GitHub のマージの 2 通りと、ruleset の無い `strict` の早送り（`land`）の 1 通りである。

- merge commit: `Merge pull request #<番号> from <所有者>/<ブランチ>`
- squash: `<題> (#<番号>)`
- 早送り（`LAND_SUBJECT`）: `Land #<番号> from <ブランチ>`

リリースブランチ（設定の書式に合う名前。呼び手が `is_release` で渡す）からのマージ（前のリリースの PR）と、本番ブランチからの
マージ（本番からベースへ戻す PR。head は本番ブランチ）は含めない。squash の件名はブランチを持たないため、題がリリースの PR の題
（`RELEASE_TITLE`）か本番から戻す PR の題（`SYNC_TITLE`）の形なら含めない（API で head を読まない）。

`release_branch` のリリースの単位のマージ（件名 `Merge <スプリント> into <リリースブランチ>`。形の SSOT は `UNIT_MERGE_SUBJECT`）は
`is_unit_merge` で見分け、呼び手がその中の PR を数える（#3615 の決定 8）。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable

# リリースの単位のマージの件名の形の SSOT（件名を組む `units.merge_subject` がここを読む。`units` は git を読むので判断のモジュールから読まない）
UNIT_MERGE_SUBJECT = "Merge {unit} into {branch}"
# 早送りで本番へ入れたコミットの件名の形の SSOT（`land` が組む。ブランチは PR の head）
LAND_SUBJECT = "Land #{number} from {branch}"
# リリースの PR の題の形の SSOT（`assemble` が組む）と、本番からベースへ戻す PR の題の形の SSOT（`land` が組む）
RELEASE_TITLE = "Release: {unit} v{version}"
SYNC_TITLE = "Sync: {production} を {base} へ戻す"

_MERGE = re.compile(r"^Merge pull request #(?P<number>\d+) from [^/\s]+/(?P<branch>\S+)")
_SQUASH = re.compile(r"\(#(?P<number>\d+)\)\s*$")


def _pattern(form: str, groups: dict[str, str]) -> re.Pattern[str]:
    """件名・題の形 `form` の置き換えの語を `groups` の正規表現にした、行頭からの照合。"""
    out = re.escape(form)
    for name, expr in groups.items():
        out = out.replace(re.escape("{" + name + "}"), expr)
    return re.compile("^" + out)


_LAND = _pattern(LAND_SUBJECT, {"number": r"(?P<number>\d+)", "branch": r"(?P<branch>\S+)"})
_NOT_INCLUDED_TITLES = (
    _pattern(RELEASE_TITLE, {"unit": r"\S+", "version": r"\S+"}),
    _pattern(SYNC_TITLE, {"production": r"\S+", "base": r"\S+"}),
)


_UNIT_MERGE = re.compile(_pattern(UNIT_MERGE_SUBJECT, {"unit": r"(?P<unit>\S+)", "branch": r"(?P<branch>\S+)"}).pattern + "$")


def _never(_name: str) -> bool:
    return False


def _excluded(branch: str, skipped: Collection[str], is_release: Callable[[str], bool]) -> bool:
    """含めないブランチからのマージか（リリースブランチか、`skipped` の本番ブランチ）。"""
    return is_release(branch) or branch in skipped


def number_of(subject: str, skipped: Collection[str] = (), is_release: Callable[[str], bool] = _never) -> int | None:
    """件名 1 行の PR の番号。PR のマージでない・リリースブランチか `skipped` のブランチからのマージ・リリースの PR か本番から戻す
    PR の squash なら None。"""
    m = _MERGE.match(subject) or _LAND.match(subject)
    if m is not None:
        return None if _excluded(m.group("branch"), skipped, is_release) else int(m.group("number"))
    s = _SQUASH.search(subject)
    if s is None or any(p.match(subject) for p in _NOT_INCLUDED_TITLES):
        return None
    return int(s.group("number"))


def numbers(subjects: Iterable[str], skipped: Collection[str] = (), is_release: Callable[[str], bool] = _never) -> list[int]:
    """含む PR の番号（重なりを除き、小さい順）。`skipped` は含めないブランチ（本番ブランチ）。"""
    return sorted({n for n in (number_of(s, skipped, is_release) for s in subjects) if n is not None})


def is_unit_merge(subject: str, is_release: Callable[[str], bool]) -> bool:
    """件名がリリースの単位のマージ（`Merge <スプリント> into <リリースブランチ>`）か。リリースブランチの部分が書式に合うときだけ真。"""
    m = _UNIT_MERGE.match(subject)
    return m is not None and is_release(m.group("branch"))
