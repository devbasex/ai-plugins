"""ラウンドのレビューの範囲の判断（#3181。純粋な関数）。

1 ラウンド目は PR の全差分を見る。2 ラウンド目以降は前の head からの差分へ絞る。ただし設計の PR では、変わった
文書のどのセクションも少なくとも 1 回は詳細レビューを通るまで全差分を見続ける（モデルレビューの指摘で詳細レビューへ
進まなかったセクションを、前の head からの差分だけを見て残したまま収束させない）。
"""

from __future__ import annotations

import hashlib
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
# ラウンドの記録のキー（そのラウンドに claude が座っている、埋めた席の並び。`Filled.record()` の形。#3955）
FILLED = "filled"
# ラウンドの記録のキー（そのラウンドで相手役へ渡したレビューの重点。重点を変えた打ち直しは前の収束も差分の起点も使わない。
# PR 3944 の r4-3）
FOCUS = "focus"
# ラウンドの記録のキー（そのラウンドで渡したレビューの単位の指示を見分ける値。`unit_id`。単位の指示が無ければ書かない。#4369）
UNIT = "unit"
# ラウンドの記録のキー（そのラウンドの起動が実際に読んだ PR。`<所有者>/<リポジトリ>#<番号>`。読めなければ書かない。#4369）
PR = "pr"
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


class Filled(NamedTuple):
    """埋めた席 1 件。`seat` は席の名前、`absent` は欠けた相手役（`allowed` のために起動の前に埋めたときは None）、`reason` は理由。"""

    seat: str
    absent: Optional[str]
    reason: str

    def record(self) -> dict[str, Any]:
        return {"seat": self.seat, "for": self.absent, "reason": self.reason}


def filled_of(record: Mapping[str, Any]) -> list[Filled]:
    """ラウンドの記録の埋めた席（形の違う要素は飛ばす）。"""
    out = []
    for f in store.list_of(record, FILLED):
        if isinstance(f, Mapping) and isinstance(f.get("seat"), str):
            absent, reason = f.get("for"), f.get("reason")
            out.append(Filled(f["seat"], absent if isinstance(absent, str) else None, reason if isinstance(reason, str) else ""))
    return out


def stand_ins(record: Mapping[str, Any]) -> list[frozenset[str]]:
    """ラウンドの席ごとの、割り当ててよい今回の席の名前の候補（埋めた席で欠けた相手役があれば、席の名前とその相手役）。"""
    absent = {f.seat: f.absent for f in filled_of(record) if f.absent is not None}
    return [frozenset({s, absent[s]} if s in absent else {s}) for s in store.list_of(record, REVIEWERS) if isinstance(s, str)]


def assignable(seats: Collection[str], options: Sequence[Collection[str]]) -> bool:
    """`seats` の全員を、名前を候補に含む `options` の別々の要素へ割り当てられるか（2 部グラフの最大マッチング）。"""
    owner: dict[int, str] = {}

    def place(seat: str, seen: set[int]) -> bool:
        for i, names in enumerate(options):
            if seat in names and i not in seen:
                seen.add(i)
                if i not in owner or place(owner[i], seen):
                    owner[i] = seat
                    return True
        return False

    return all(place(s, set()) for s in seats)


def unit_id(unit: str) -> str:
    """レビューの単位の指示の中身を見分ける値（指示が無ければ空）。"""
    return hashlib.sha256(unit.encode("utf-8")).hexdigest() if unit else ""


class Basis(NamedTuple):
    """前のラウンドの head を差分の起点や収束の再利用に使える条件（今回の値。None は確かめない）。`base` は比較基点
    （merge-base の SHA。PR 3944 の r2-1）、`reviewers` は今回の相手役（r2-2）、`focus` は今回の重点（r4-3）、`mvv` は今の
    MVV を見分ける値（確かめられなければ `UNKNOWN_MVV`。#4076）。`stand_in` が真なら、前のラウンドの埋めた席を、席の名前か
    欠けた相手役のどちらか一方として今回の席に割り当てる（同じ head の打ち直しの再利用だけが使う。#3955）。`unit` は今回の
    単位の指示を見分ける値（`unit_id`。#4369）。`pr` は今回読んだ PR（読めなければ空で、どのラウンドとも合わない。読んだ PR の確認を
    求めない起動は None。#4369）。"""

    base: Optional[str] = None
    reviewers: Optional[Collection[str]] = None
    focus: Optional[Sequence[str]] = None
    mvv: Optional[str] = None
    stand_in: bool = False
    unit: Optional[str] = None
    pr: Optional[str] = None


def _matches(record: Mapping[str, Any], basis: Basis) -> bool:
    """ラウンドの記録が `basis` に合うか。`basis` の値があれば、比較基点（`BASE`）が違う・そのラウンドを読んだ相手役
    （`REVIEWERS`）に無い相手役がいる・重点（`FOCUS`）か単位の指示（`UNIT`）か読んだ PR（`PR`）か MVV（`MVV`）が違うときは合わない。承認済みの MVV があるのに、
    そのラウンドの head で根拠の記述を確かめ終えていない（`finish_decide.MVV_CHECKED`）ときも合わない（#4090）。"""
    if basis.base is not None and record.get(BASE) != basis.base:
        return False
    if basis.reviewers is not None and not _seated_read(record, basis):
        return False
    if basis.focus is not None and store.list_of(record, FOCUS) != list(basis.focus):
        return False
    # 単位の指示の無いラウンドはキーを持たず、指示の無い起動（`unit_id` は空）と合う
    if basis.unit is not None and (record[UNIT] if UNIT in record else "") != basis.unit:
        return False
    if basis.pr is not None and (record[PR] if PR in record else None) != basis.pr:
        return False
    if basis.mvv is None:
        return True
    if _known(_mvv_record(record).get(MVV_ID)) != basis.mvv:
        return False
    return not basis.mvv.startswith(_APPROVED) or record.get(fin.MVV_CHECKED) is True


def _seated_read(record: Mapping[str, Any], basis: Basis) -> bool:
    """今回の席（`basis.reviewers`）の全員がそのラウンドを読んだか。"""
    seats = set(basis.reviewers) if basis.reviewers is not None else set()
    if basis.stand_in:
        return assignable(seats, stand_ins(record))
    return seats <= set(store.list_of(record, REVIEWERS))


def read_by_all_head(state: Mapping[str, Any], basis: Basis = Basis()) -> Optional[str]:
    """相手役の全員が読めた最後のラウンドの head（無ければ None）。読めなかった相手役のいるラウンド
    （`finish_decide.INCOMPLETE_REVIEWERS`）の head を起点にすると、同じ head で打ち直したときに空の差分が渡り、
    読めなかった相手役が元の差分を一度も読まずに収束できるため、飛ばす（#3941）。そのラウンドの記録が `basis` に合わなければ
    （`_matches`）None（PR の全差分を今回の相手役と重点と MVV で読み直す）。"""
    for r in reversed(store.list_of(state, store.ROUNDS)):
        if not isinstance(r, Mapping) or r.get("verdict") == fin.INCOMPLETE_REVIEWERS:
            continue
        head = r.get("head")
        return head if _matches(r, basis) and isinstance(head, str) and head else None
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
