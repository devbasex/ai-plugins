"""差分の起点に使える前のラウンドの head を git で確かめる（記録が今回の条件に合うかは `scope_decide`）。

実装の PR の起点は `read_since`（相手役・読んだ PR・履歴だけを照らす。#3780）、設計の PR の起点は `previous_head`
（比較基点・重点・MVV なども照らす）が決める。`run` と `review-scope` は同じ関数を使う。範囲と文書の指摘の重要度の
制限（#4285）は同じ head を使う。別の条件で決まると、全差分を読み直す相手役に「前に読んだ文」の制限がかかる。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple, Optional

from ndf.adapters import git
from ndf.domains.crossreview import changes, scope_decide

# 比較基点が変わった打ち直しで、読んでいない変更を探せず全差分にしたときの注意
UNCOMPARABLE = "比較基点が前のラウンドと違い、読んでいない変更を手元のコミットで探せないので、全差分を範囲にした"


class Since(NamedTuple):
    """実装の PR の差分の起点。`previous` は起点のラウンドの比較基点と head、`added` は比較基点が変わって新しく PR の変更に
    なったファイル、`listed` は `changes.since_diff` へ渡す読んだ一覧、`comparable` はそのファイルを探せたか（偽なら呼び手は
    全差分にする）。"""

    previous: changes.Range
    added: tuple[str, ...]
    listed: changes.Listings
    comparable: bool = True


def previous_head(g: git.Git, state: Mapping[str, Any], basis: scope_decide.Basis, head: str) -> Optional[str]:
    """相手役の一部が読めなかったラウンドを除いた最後のラウンドの head。今回の `basis` と合わないか、`head` の祖先でなければ、
    それより前のラウンドへは戻らずに None（PR の全差分を読む）。"""
    prev = scope_decide.read_by_all_head(state, basis)
    return prev if prev is not None and _reachable_from(g, prev, head) else None


def read_origin(g: git.Git, state: Mapping[str, Any], basis: scope_decide.Basis, head: str) -> Optional[changes.Range]:
    """差分の起点（`scope_decide.origin_record` のラウンドの比較基点と head。#3780）。起点の記録が無いか、比較基点の記録が
    無いか、その head が `head` の祖先でなければ None。"""
    record = scope_decide.origin_record(state, basis)
    if record is None:
        return None
    base, prev = record.get(scope_decide.BASE), record.get("head")
    if not isinstance(base, str) or not base or not isinstance(prev, str) or not _reachable_from(g, prev, head):
        return None
    return changes.Range(base, prev)


def read_since(g: git.Git, state: Mapping[str, Any], basis: scope_decide.Basis, current: changes.Range) -> Optional[Since]:
    """実装の PR の差分の起点（`read_origin`）と、前の head からの差分に出ないので足すファイル。起点が無ければ None。"""
    previous = read_origin(g, state, basis, current.head)
    if previous is None:
        return None
    listed: changes.Listings = {}
    if previous.base == current.base:
        # 比較基点が同じなら新しく PR の変更になるファイルは無いので、差分を読まない
        return Since(previous, (), listed)
    added = changes.new_in_pr(g, previous, current, listed)
    if added is None:
        return Since(previous, (), listed, comparable=False)
    return Since(previous, added, listed)


def line_origin(g: git.Git, state: Mapping[str, Any], head: str) -> Optional[changes.Range]:
    """線引きの起点（相手役の全員が結果を返した最後のラウンドの比較基点と head。#3780）。読んだ顔ぶれと PR は問わないので、
    読んでいない相手役がいて全差分を読むラウンドにも残る。"""
    return read_origin(g, state, scope_decide.Basis(), head)


def descended(g: git.Git, scope: scope_decide.Scope, head: str) -> scope_decide.Scope:
    """前の head を git で確かめる前に決めた範囲のうち、前の head からの差分（`SINCE`）は、その head が `head` の祖先のときだけ
    使い、ほかは PR の全差分へ戻す。全差分の範囲には git を起動しない（`review-scope` は範囲だけを返すので、前の head が要らない）。"""
    if scope.base is None or _reachable_from(g, scope.base, head):
        return scope
    return scope_decide.Scope(scope_decide.FULL, None, ())


def _reachable_from(g: git.Git, prev: str, head: str) -> bool:
    """前の head が `head` の祖先か。巻き直しで前の変更を取り除いた後の `前の head...HEAD` は分岐点から今の側だけを表し、
    前の head からの削除が相手役に渡らない（PR 3944 の r6-7）。"""
    try:
        kept = g.is_ancestor(prev, head)
    except git.GitError:
        # 前の head のコミットが手元に無い（巻き直しで消えた）。前の head からの差分は作れないので全差分にする
        kept = False
    return kept
