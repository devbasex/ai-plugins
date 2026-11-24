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
from ndf.domains.records import mvv_decide

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
# ラウンドの記録のキー（そのラウンドのレビューに使った MVV。#4076）と、その中身のキー。`MVV_ID` は MVV を見分ける値
# （`mvv_decide.identity_of`）、`MVV_BRANCH` は MVV を読んだブランチ（`--base` がコミットのときに、`review-scope` が
# GitHub へ問い合わせずに同じブランチの MVV を読むのに使う）
MVV = "mvv"
MVV_ID = "id"
MVV_BRANCH = "mvv_branch"
# 今の MVV を確かめられないときの、MVV を見分ける値。どの記録とも違うとみなし、前のラウンドを使わない
UNKNOWN_MVV = ""
# MVV の記録の無いラウンド（記録を足す前のバージョンが書いた）の、MVV を見分ける値
_NO_MVV = mvv_decide.identity_of(mvv_decide.State.ABSENT)
# 承認済みの MVV の、MVV を見分ける値の始まり
_APPROVED = f"{mvv_decide.State.APPROVED}:"
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


def reviewed_of(state: Mapping[str, Any], mvv: Optional[str] = None) -> set[str]:
    """状態ファイルの中身から、前のラウンドまでに詳細レビューを通ったセクション（並びでなければ無し）。今の MVV を見分ける
    値 `mvv`（None は確かめない）と違う MVV で通ったセクションは、通っていないとみなす（#4076）。"""
    if mvv is not None and _known(state.get(store.REVIEWED_MVV)) != mvv:
        return set()
    return set(store.list_of(state, store.REVIEWED))


def _known(value: object) -> str:
    """記録された、MVV を見分ける値。記録が無ければ、MVV が無いときの値とみなす。"""
    return value if isinstance(value, str) and value else _NO_MVV


def _mvv_record(record: object) -> Mapping[str, Any]:
    """ラウンドの記録にある MVV の記録（無ければ空）。"""
    held = record.get(MVV) if isinstance(record, Mapping) else None
    return held if isinstance(held, Mapping) else {}


def mvv_branch(state: Mapping[str, Any]) -> Optional[str]:
    """最後に MVV を読んだブランチ（記録が無ければ None）。"""
    names = [_mvv_record(r).get(MVV_BRANCH) for r in store.list_of(state, store.ROUNDS)]
    return next((n for n in reversed(names) if isinstance(n, str) and n), None)


class Basis(NamedTuple):
    """前のラウンドの head を差分の起点や収束の再利用に使える条件（今回の値。None は確かめない）。`base` は比較基点
    （merge-base の SHA。PR 3944 の r2-1）、`reviewers` は今回の相手役（r2-2）、`focus` は今回の重点（r4-3）、`mvv` は今の
    MVV を見分ける値（確かめられなければ `UNKNOWN_MVV`。#4076）。"""

    base: Optional[str] = None
    reviewers: Optional[Collection[str]] = None
    focus: Optional[Sequence[str]] = None
    mvv: Optional[str] = None


def read_by_all_head(state: Mapping[str, Any], basis: Basis = Basis()) -> Optional[str]:
    """相手役の全員が読めた最後のラウンドの head（無ければ None）。読めなかった相手役のいるラウンド
    （`finish_decide.INCOMPLETE_REVIEWERS`）の head を起点にすると、同じ head で打ち直したときに空の差分が渡り、
    読めなかった相手役が元の差分を一度も読まずに収束できるため、飛ばす（#3941）。`basis` の値があれば、そのラウンドの
    比較基点（`BASE`）が違う・そのラウンドを読んだ相手役（`REVIEWERS`）に無い相手役がいる・そのラウンドの重点（`FOCUS`）か
    MVV（`MVV`）が違うときは None（PR の全差分を今回の相手役と重点と MVV で読み直す）。承認済みの MVV があるのに、そのラウンドの
    head で根拠の記述を確かめ終えていない（`finish_decide.MVV_CHECKED`）ときも None（#4090）。"""
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
        if basis.mvv is not None and _known(_mvv_record(r).get(MVV_ID)) != basis.mvv:
            return None
        if basis.mvv is not None and basis.mvv.startswith(_APPROVED) and r.get(fin.MVV_CHECKED) is not True:
            return None
        return head if isinstance(head, str) and head else None
    return None


def state_scope(state: Mapping[str, Any], every_section: bool, round_no: int, sections: Iterable[str], basis: Basis = Basis()) -> Scope:
    """状態ファイルの中身（相手役の全員が読めた最後のラウンドの head・詳細レビューを通ったセクション）から、ラウンド `round_no` の範囲を決める。
    前のラウンドと `basis`（比較基点・相手役・重点・MVV）が合わなければ全差分。"""
    return review_scope(every_section, round_no, read_by_all_head(state, basis), sections, reviewed_of(state, basis.mvv))


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
