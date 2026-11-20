"""PR に既にあるコメントのパース（#3941。純粋な関数）。

- 既存の指摘: インラインのレビューコメント・レビュー本文・PR コメントの 3 種（人と bot と前のラウンドの返信を含む）を、
  相手役へ「既に出ている指摘」として渡す。cross-review 自身が書いた記録のコメントとスレッドの最初のコメント（どちらも
  `report_decide.MARKER` で始まる）は除く（自分の指摘はジャーナルが持つ）
- 引き継ぐ指摘: 前の実行が開いたまま残した自分のスレッド（最初のコメントが `MARKER` で始まり、resolve されていない）を、
  別の保存先で始めた実行が未解決の指摘として引き継ぐ。状態ファイルの `threads` が既に持つスレッドは引き継がない
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Sequence
from typing import NamedTuple, Optional

from ndf.domains.crossreview import report_decide

# スレッドの最初のコメント（`threads.opening`）の見出しと本文
_OPENING = re.compile(
    r"\*\*cross-review の指摘 (?P<id>\S+)\*\*（(?P<by>[^・）]*)・ラウンド \d+(?:・(?P<severity>[a-z]+))?）\n\n(?P<text>.*)", re.S
)


class Remark(NamedTuple):
    """PR にあるコメント 1 件。`kind` は `review_comment`（インライン）・`review`（レビュー本文）・`comment`（PR コメント）。"""

    kind: str
    author: str
    body: str
    path: Optional[str] = None
    line: Optional[int] = None


class Thread(NamedTuple):
    """PR のレビューのスレッド 1 つ（最初のコメントの id と本文、resolve 済みか、付いた場所）。"""

    comment: int
    body: str
    resolved: bool
    path: Optional[str] = None
    line: Optional[int] = None


class Carried(NamedTuple):
    """前の実行から引き継ぐ未解決の指摘 1 件。"""

    id: str
    comment: int
    text: str
    by: str
    severity: Optional[str]
    path: Optional[str]
    line: Optional[int]


def _ours(body: str) -> bool:
    return body.lstrip().startswith(report_decide.MARKER)


def others(remarks: Iterable[Remark]) -> list[Remark]:
    """相手役へ渡すコメント（本文があり、cross-review 自身の記録・スレッドの最初のコメントでないもの）。"""
    return [r for r in remarks if r.body.strip() and not _ours(r.body)]


def _one_line(text: str, chars: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= chars else flat[:chars] + "…"


def remark_lines(remarks: Sequence[Remark], chars: int) -> list[str]:
    """相手役へ渡す行（1 件 1 行。本文は `chars` 字で切る）。"""
    out = []
    for r in remarks:
        where = f"・{r.path}{':' + str(r.line) if r.line else ''}" if r.path else ""
        out.append(f"- {r.author or '不明'}（{r.kind}{where}）: {_one_line(r.body, chars)}")
    return out


def carried(threads: Iterable[Thread], tracked: Collection[int]) -> list[Carried]:
    """引き継ぐ指摘。自分のスレッドで resolve されておらず、`tracked`（状態ファイルが持つスレッドの最初のコメント）に無いもの。"""
    out = []
    for t in threads:
        if t.resolved or t.comment in tracked or not _ours(t.body):
            continue
        found = _OPENING.search(t.body)
        if found is None or not found.group("text").strip():
            continue
        out.append(
            Carried(f"t{t.comment}", t.comment, found.group("text").strip(), found.group("by"), found.group("severity"), t.path, t.line)
        )
    return out
