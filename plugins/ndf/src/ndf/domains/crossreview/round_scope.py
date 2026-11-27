"""差分の起点に使える前のラウンドの head を git で確かめる（比較基点・相手役・重点が今回と合うかは `scope_decide`）。

`run` の範囲も、文書の指摘の重要度の制限（#4285）も、`previous_head` が返す head だけを使う。範囲と制限が別の条件で
決まると、全差分を読み直す相手役に「前に読んだ文」の制限がかかる。`review-scope` は `descended` で同じ範囲を決める。
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any, Optional

from ndf.adapters import git
from ndf.domains.crossreview import changes, scope_decide


def previous_head(g: git.Git, state: Mapping[str, Any], basis: scope_decide.Basis, head: str) -> Optional[str]:
    """相手役の一部が読めなかったラウンドを除いた最後のラウンドの head。今回の `basis` と合わないか、`head` の祖先でなければ、
    それより前のラウンドへは戻らずに None（PR の全差分を読む）。"""
    prev = scope_decide.read_by_all_head(state, basis)
    return prev if prev is not None and _reachable_from(g, prev, head) else None


def reviewed(g: git.Git, state: Mapping[str, Any], basis: scope_decide.Basis, every_section: bool) -> set[str]:
    """今の起動で引き継げる詳細レビューの記録（`scope_decide.reviewed_for`）。`run` と `review-scope` の両方がこれを呼ぶ。
    git を起動するのは、詳細レビューを求める起動（`every_section`。設計の PR）で、記録の起点が今の起点と違い、引き継ぐ候補が
    あるときだけである（PR 4638 の r4-2・r5-1・r6-2）。ほかの起動は範囲に記録を使わないので、起点が違えば本文を比べずに外す。"""
    if not every_section:
        return scope_decide.reviewed_for(state, basis)
    return scope_decide.reviewed_for(state, basis, lambda start, now, kept: _base_texts(g, start, now, kept))


def _base_texts(g: git.Git, start: str, now: str, kept: Collection[str]) -> Optional[dict[str, tuple[Optional[str], Optional[str]]]]:
    """2 つの起点の間で変わったファイルのうち、`kept` のセクションを含む文書の、両方の起点の本文。変わったファイルの名前と
    変わり方を 1 回、該当する文書があればその本文を 1 回読む（git の起動は 1〜2 回）。`review-scope` と同じく手元のオブジェクト
    だけを読み、部分クローンで欠けた blob をリモートから取得しない（PR 4638 の r1-8）。どちらかの起点が手元に無い・変わり方から
    あるはずの本文が欠けているなどで読めなければ None（記録を引き継がずに詳細レビューへ回す）。"""
    local = git.Git.offline(g.cwd)
    try:
        fields = local.diff_between(start, now, "--name-status", "-z", changes.NO_RENAMES, "--no-relative").split("\0")
        status = dict(zip(fields[1::2], fields[0::2]))
        paths = [p for p in status if p and any(s.startswith(f"{p}#") for s in kept)]
        if not paths:
            return {}
        got = local.blobs(*(f"{c}:{p}" for p in paths for c in (start, now)))
    except git.GitError:  # noqa: NDF401 外部コマンド: 起点が手元に無いなどで読めなければ、引き継がずに詳細レビューへ回す
        return None
    for i, p in enumerate(paths):
        # 追加（A）なら記録の起点に、削除（D）なら今の起点に本文が無い。ほかに無い本文は手元に無い blob なので、無いファイルと読まない
        if (got[2 * i] is None) != (status[p] == "A") or (got[2 * i + 1] is None) != (status[p] == "D"):
            return None
    text = [b.decode("utf-8", errors="replace") if b is not None else None for b in got]
    return {p: (text[2 * i], text[2 * i + 1]) for i, p in enumerate(paths)}


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
