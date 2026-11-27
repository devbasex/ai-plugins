"""差分の起点に使える前のラウンドの head を git で確かめる（比較基点・相手役・重点が今回と合うかは `scope_decide`）。

`run` の範囲も、文書の指摘の重要度の制限（#4285）も、`previous_head` が返す head だけを使う。範囲と制限が別の条件で
決まると、全差分を読み直す相手役に「前に読んだ文」の制限がかかる。`review-scope` は `descended` で同じ範囲を決める。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

from ndf.adapters import git
from ndf.domains.crossreview import scope_decide


def previous_head(g: git.Git, state: Mapping[str, Any], basis: scope_decide.Basis, head: str) -> Optional[str]:
    """相手役の一部が読めなかったラウンドを除いた最後のラウンドの head。今回の `basis` と合わないか、`head` の祖先でなければ、
    それより前のラウンドへは戻らずに None（PR の全差分を読む）。"""
    prev = scope_decide.read_by_all_head(state, basis)
    return prev if prev is not None and _reachable_from(g, prev, head) else None


def reviewed(g: git.Git, state: Mapping[str, Any], basis: scope_decide.Basis) -> set[str]:
    """今の起動で引き継げる詳細レビューの記録（`scope_decide.reviewed_for`）。記録の起点が今の起点の祖先かを git で確かめ、
    確かめられなければ引き継がない。`run` と `review-scope` の両方がこれを呼ぶ（PR 4638 の r3-1）。"""
    return scope_decide.reviewed_for(state, basis, lambda start, now: _reachable_from(g, start, now))


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
