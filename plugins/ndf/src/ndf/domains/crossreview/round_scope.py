"""差分の起点に使える前のラウンドの head を、git で確かめて決める（比較基点・相手役・重点が今回と合うかは `scope_decide`）。

`run` と `review-scope` の範囲も、文書の指摘の重要度の制限（#4285）も、ここで決めた head だけを使う。範囲と制限が別の条件で
決まると、全差分を読み直す相手役に「前に読んだ文」の制限がかかる。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

from ndf.adapters import git
from ndf.domains.crossreview import scope_decide


def previous_head(g: git.Git, state: Mapping[str, Any], basis: scope_decide.Basis, head: str) -> Optional[str]:
    """今回の `basis` と合う、相手役の全員が読めた最後のラウンドの head。無いか、`head` の祖先でなければ None（PR の全差分を読む）。

    巻き直しで前の変更を取り除いた後の `前の head...HEAD` は分岐点から今の側だけを表し、前の head からの削除が相手役に渡らない
    （PR 3944 の r6-7）。"""
    prev = scope_decide.read_by_all_head(state, basis)
    if prev is None:
        return None
    try:
        kept = g.is_ancestor(prev, head)
    except git.GitError:
        # 前の head のコミットが手元に無い（巻き直しで消えた）。前の head からの差分は作れないので全差分にする
        kept = False
    return prev if kept else None
