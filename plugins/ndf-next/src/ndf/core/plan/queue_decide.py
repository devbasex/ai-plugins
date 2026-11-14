"""キューの判断（純粋な関数）: 前提のプラン・再開の前置き・触るファイルの重なり・同時に流す組。

- 前提のプランがすべて `ok` で終わっていなければ流さない（`blocked_reason`）
- 再開の開始点より前にある `RESUME_ALWAYS` のステップは毎回通す（`resume_prelude`。#3114・#3047）
- 触るファイルの宣言（`touches`）の無いプランは、すべてのプランと重なるものとして直列にする（#2165）
- 宣言の値は、リポジトリの根からの相対のパス。`/` で終わる値はディレクトリで、その下のファイルすべてと重なる
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from pathlib import PurePosixPath
from typing import NamedTuple, Optional

from ndf.contracts.exit_codes import OK

# 再開の開始点より前にあれば毎回通すステップ。ベースブランチと振り返りの範囲は前の実行の後に変わりうる
RESUME_ALWAYS = ("base-sync", "deep-due")


class Queued(NamedTuple):
    """キューの 1 本。`touches` が None なら宣言が無い。"""

    name: str
    touches: Optional[tuple[str, ...]]
    needs: tuple[str, ...] = ()


def blocked_reason(plan: str, deps: Sequence[str], queued: Sequence[str], results: Mapping[str, Optional[str]]) -> Optional[str]:
    """前提のプランの結果から、流さない理由を返す。すべての前提が `ok` なら None。

    `queued` はキューの並び、`results` は前提の名前から結果の status（見つからなければ None）への対応。
    """
    reasons = []
    after = set(queued[queued.index(plan) + 1 :]) if plan in queued else set()
    for dep in deps:
        found = results[dep] if dep in results else None
        if dep in after:
            reasons.append(f"前提のプラン {dep} の結果が無い（後ろにある）")
        elif found is None:
            reasons.append(f"前提のプラン {dep} が見つからない")
        elif found != OK:
            reasons.append(f"前提のプランが完了でない: {dep}（{found}）")
    return "。".join(reasons) if reasons else None


def resume_prelude(order: Sequence[str], start: str, always: Collection[str] = RESUME_ALWAYS) -> tuple[str, ...]:
    """再開の開始点より前にある、毎回通すステップを並びのまま返す。`start` が並びに無ければ `ValueError`。"""
    if start not in order:
        raise ValueError(f"開始点 {start} はプランのステップに無い")
    return tuple(s for s in order[: order.index(start)] if s in always)


def _norm(path: str) -> str:
    """宣言の値をリポジトリの根からの相対のパスにそろえる（`./` と重なった `/` を除く。ディレクトリの `/` は残す）。"""
    tail = "/" if path.endswith("/") else ""
    body = str(PurePosixPath(path.strip()))
    return "" if body in (".", "/") else body.removeprefix("./") + tail


def contains(a: str, b: str) -> bool:
    """宣言 `a` と `b` が同じファイルを指しうるか（同じパスか、一方のディレクトリの下に他方がある）。"""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return True
    if a.rstrip("/") == b.rstrip("/"):
        return True
    return (a.endswith("/") and b.startswith(a)) or (b.endswith("/") and a.startswith(b))


def shared_hits(a: Sequence[str], b: Sequence[str]) -> list[str]:
    """2 つの宣言で重なる値（`a` の側）を並べる。"""
    return [x for x in a if any(contains(x, y) for y in b)]


def overlaps(a: Optional[Sequence[str]], b: Optional[Sequence[str]]) -> bool:
    """2 本のプランが同時に流せないか。どちらかに宣言が無ければ重なる（#2165）。"""
    if a is None or b is None:
        return True
    return bool(shared_hits(a, b))


def _admits(batch: Sequence[Queued], item: Queued, placed: Collection[str]) -> bool:
    if any(dep not in placed for dep in item.needs):
        return False
    return not any(overlaps(item.touches, other.touches) for other in batch)


def waves(items: Sequence[Queued]) -> list[list[str]]:
    """キューの並びを、同時に流してよい組の並びに分ける。

    並びの順に、前提がすべて前の組にあり、組の中のどれとも重ならないものを今の組へ入れる。入らないものは次の組へ回す。
    前提がキューに無いものは、残りがすべて置けなくなった時点で 1 本ずつの組にする（流す前に `blocked_reason` が止める）。
    """
    rest = list(items)
    placed: set[str] = set()
    out: list[list[str]] = []
    while rest:
        batch: list[Queued] = []
        for item in rest:
            if _admits(batch, item, placed):
                batch.append(item)
        if not batch:
            batch = [rest[0]]
        out.append([b.name for b in batch])
        placed.update(b.name for b in batch)
        rest = [r for r in rest if r not in batch]
    return out
