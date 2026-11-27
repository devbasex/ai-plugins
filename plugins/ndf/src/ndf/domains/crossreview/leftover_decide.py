"""2 ラウンド目以降の線引き（#3780。純粋な関数）。

指摘を、修正へ進めるものと直さずに残すもの（指摘の行の `parked`）に分ける。行へ書くのは `journal.FindingJournal.sort` である。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple

from ndf.domains.crossreview import criteria_decide
from ndf.domains.records import mvv_decide

# 直さずに残す指摘の行の欄。修正担当へ渡さず、次のラウンドを始める理由にも数えない
PARKED = "parked"


class Touched(NamedTuple):
    """線引きの起点の後に変わったファイルと、足した行・書き換えた行の番号（行を消しただけの箇所は前後の行）。`commentable` は
    今の PR の差分に載る head の側の行の番号（行コメントを付けられる行）。"""

    files: frozenset[str] = frozenset()
    lines: Mapping[str, frozenset[int]] = {}
    commentable: Mapping[str, frozenset[int]] = {}


def normalized(text: str) -> str:
    """空白をまとめ、大文字小文字をそろえた本文の全体。"""
    return " ".join(text.split()).casefold()


def finding_kinds(row: Mapping[str, Any]) -> frozenset[str]:
    """指摘の種類（行の `protect` と、判定役の確認の `tracks` の和）。"""
    check = row.get(criteria_decide.CHECK)
    found: list[str] = []
    for kinds in (row.get(criteria_decide.PROTECT), check.get("tracks") if isinstance(check, Mapping) else None):
        if isinstance(kinds, (list, tuple)):
            found += kinds
    return frozenset(found)


def _path(row: Mapping[str, Any]) -> str | None:
    """指摘の `path`（先頭の `./` を除く）。無ければ None。"""
    path = row.get("path")
    return path.removeprefix("./") if isinstance(path, str) else None


def on_touched_line(row: Mapping[str, Any], touched: Touched) -> bool:
    """指摘の位置が、変えた行を指すか。"""
    path = _path(row)
    return path in touched.lines and row.get("line") in touched.lines[path]


def on_commentable_line(row: Mapping[str, Any], touched: Touched) -> bool:
    """指摘の位置が、PR の差分に載る行（行コメントを付けられる行）を指すか。"""
    path = _path(row)
    return path in touched.commentable and row.get("line") in touched.commentable[path]


def proceeds(row: Mapping[str, Any], touched: Touched, *, returned: bool = False, unconfirmed: bool = False) -> bool:
    """指摘の行 `row` を直さずに残さないか（真なら `parked` を付けない）。`returned` は直した後にもう一度出た指摘、
    `unconfirmed` は判定役へ渡して有効な回答の無かった指摘。minor・nit も真だが、重要度で修正担当へは渡らない。

    必ず人の承認が要る操作の指摘を先頭で通すので、ほかの条件や前の `parked` によらず、人の判断を待つ。"""
    if row.get(mvv_decide.REVIEW_REDLINE) or row.get("by") == mvv_decide.BY_NDF or row.get("round") == 0 or returned:
        return True
    # 残すのは収束の妨げになる重さのうち最も軽いもの（major）だけ
    if criteria_decide.severity_of(row.get("severity")) != criteria_decide.at_least_blocking(None):
        return True
    path, line = _path(row), row.get("line")
    if on_touched_line(row, touched):
        return True
    if finding_kinds(row) & set(criteria_decide.protect_kinds()):
        return True
    if line is None and (path is None or path in touched.files):
        return True
    return unconfirmed
