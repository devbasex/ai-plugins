"""ラウンドのレビューの範囲の判断（#3181。純粋な関数）。

1 ラウンド目は PR の全差分を見る。2 ラウンド目以降は前の head からの差分へ絞る。ただし設計の PR では、変わった
文書のどのセクションも少なくとも 1 回は詳細レビューを通るまで全差分を見続ける（モデルレビューの指摘で詳細レビューへ
進まなかったセクションを、前の head からの差分だけを見て残したまま収束させない）。
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any, NamedTuple, Optional

from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview import store

FULL = "full"
SINCE = "since"
# ラウンドの記録のキー（そのラウンドの比較基点。`--base` と HEAD の merge-base の SHA。ブランチ名だと参照先が動いても
# 同じ値になり、変わった PR の全差分を見落とす。PR 3944 の r2-1）
BASE = "base"
# ラウンドの記録のキー（そのラウンドを読んだ相手役の並び。PR 3944 の r2-2）
REVIEWERS = "reviewers"
# ラウンドの記録のキー（そのラウンドで相手役へ渡したレビューの重点。重点を変えた打ち直しは前の収束も差分の起点も使わない。
# PR 3944 の r4-3）
FOCUS = "focus"
# Markdown のコードブロックの囲み
_FENCES = ("```", "~~~")


class Scope(NamedTuple):
    """`kind` は `FULL`（全差分）か `SINCE`（`base` からの差分）。`unreviewed` は詳細レビューをまだ通らないセクション。"""

    kind: str
    base: Optional[str]
    unreviewed: tuple[str, ...]


def sections_of(path: str, text: str) -> list[str]:
    """Markdown の見出しを `<パス>#<見出し>` の並びにする（コードブロックの中の `#` は見出しでない）。"""
    out: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith(_FENCES):
            fenced = not fenced
            continue
        if not fenced and line.startswith("#"):
            title = line.lstrip("#").strip()
            if title:
                out.append(f"{path}#{title}")
    return out


def reviewed_of(state: Mapping[str, Any]) -> set[str]:
    """状態ファイルの中身から、前のラウンドまでに詳細レビューを通ったセクション（並びでなければ無し）。"""
    return set(store.list_of(state, store.REVIEWED))


class Basis(NamedTuple):
    """前のラウンドの head を差分の起点や収束の再利用に使える条件（今回の値。None は確かめない）。`base` は比較基点
    （merge-base の SHA。PR 3944 の r2-1）、`reviewers` は今回の相手役（r2-2）、`focus` は今回の重点（r4-3）。"""

    base: Optional[str] = None
    reviewers: Optional[Collection[str]] = None
    focus: Optional[Sequence[str]] = None


def read_by_all_head(state: Mapping[str, Any], basis: Basis = Basis()) -> Optional[str]:
    """相手役の全員が読めた最後のラウンドの head（無ければ None）。読めなかった相手役のいるラウンド
    （`finish_decide.INCOMPLETE_REVIEWERS`）の head を起点にすると、同じ head で打ち直したときに空の差分が渡り、
    読めなかった相手役が元の差分を一度も読まずに収束できるため、飛ばす（#3941）。`basis` の値があれば、そのラウンドの
    比較基点（`BASE`）が違う・そのラウンドを読んだ相手役（`REVIEWERS`）に無い相手役がいる・そのラウンドの重点（`FOCUS`）が
    違うときは None（PR の全差分を今回の相手役と重点で読み直す）。"""
    for r in reversed(store.list_of(state, store.ROUNDS)):
        if not isinstance(r, Mapping) or r.get("verdict") == fin.INCOMPLETE_REVIEWERS:
            continue
        head = r.get("head")
        if basis.base is not None and r.get(BASE) != basis.base:
            return None
        if basis.reviewers is not None and not set(basis.reviewers) <= set(store.list_of(r, REVIEWERS)):
            return None
        if basis.focus is not None and store.list_of(r, FOCUS) != list(basis.focus):
            return None
        return head if isinstance(head, str) and head else None
    return None


def review_scope(every_section: bool, round_no: int, prev_head: Optional[str], sections: Iterable[str], reviewed: Collection[str]) -> Scope:
    """ラウンド `round_no`（1 から）の範囲。`every_section`（設計の PR）なら、`reviewed`（前のラウンドまでに詳細レビューを
    通ったセクション）に無いセクションが残る間は全差分を見る。"""
    unreviewed = tuple(s for s in sections if s not in reviewed) if every_section else ()
    if round_no <= 1 or not prev_head or unreviewed:
        return Scope(FULL, None, unreviewed)
    return Scope(SINCE, prev_head, ())


def oversized(lengths: Mapping[str, int], limit: int) -> list[tuple[str, int]]:
    """行数が `limit` を超える文書（パスと行数。`lengths` の順）。設計文書が長すぎることを知らせる（止めない。#3941）。"""
    return [(path, n) for path, n in lengths.items() if n > limit]
