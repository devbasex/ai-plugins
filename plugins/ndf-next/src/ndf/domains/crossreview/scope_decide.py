"""ラウンドのレビューの範囲の判断（#3181。純粋な関数）。

1 ラウンド目は PR の全差分を見る。2 ラウンド目以降は前の head からの差分へ絞る。ただし設計の PR では、変わった
文書のどの節も少なくとも 1 回は詳細レビューを通るまで全差分を見続ける（モデルレビューの指摘で詳細レビューへ
進まなかった節を、前の head からの差分だけを見て残したまま収束させない）。
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from typing import Any, NamedTuple, Optional

from ndf.domains.crossreview import store

FULL = "full"
SINCE = "since"
# Markdown のコードブロックの囲み
_FENCES = ("```", "~~~")


class Scope(NamedTuple):
    """`kind` は `FULL`（全差分）か `SINCE`（`base` からの差分）。`unreviewed` は詳細レビューをまだ通らない節。"""

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
    """状態ファイルの中身から、前のラウンドまでに詳細レビューを通った節（並びでなければ無し）。"""
    value = state.get(store.REVIEWED)
    return set(value) if isinstance(value, list) else set()


def state_scope(state: Mapping[str, Any], every_section: bool, round_no: int, sections: Iterable[str]) -> Scope:
    """状態ファイルの中身（最後のラウンドの head・詳細レビューを通った節）から、ラウンド `round_no` の範囲を決める。"""
    return review_scope(every_section, round_no, store.last_round(state).get("head"), sections, reviewed_of(state))


def review_scope(every_section: bool, round_no: int, prev_head: Optional[str], sections: Iterable[str], reviewed: Collection[str]) -> Scope:
    """ラウンド `round_no`（1 から）の範囲。`every_section`（設計の PR）なら、`reviewed`（前のラウンドまでに詳細レビューを
    通った節）に無い節が残る間は全差分を見る。"""
    unreviewed = tuple(s for s in sections if s not in reviewed) if every_section else ()
    if round_no <= 1 or not prev_head or unreviewed:
        return Scope(FULL, None, unreviewed)
    return Scope(SINCE, prev_head, ())
