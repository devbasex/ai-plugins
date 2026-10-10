"""課題の本文の受け入れ条件の節の読み取りと印付け（#1683）。GitHub を呼ばない。

受け入れ条件の番号は、`## 受け入れ条件` の節のコードブロックの外にある、行頭（字下げなし）の
チェックボックスの行（`- [ ]` / `- [x]`）を上から数えた順（1 から）である。行頭に書いた番号は読まない
（設計の決定 2）。取り消し線で差し替えた行（`- ~~…~~ → …`）はチェックボックスを持たないため数えない。

印付けは元の本文の文字の位置で行い、チェックボックスの `[ ]` の空白 1 文字だけを `x` に置き換える。
節の外・説明の行・改行の形（`\\r\\n`）は変えない（決定 3）。
"""

from __future__ import annotations

import re
from typing import NamedTuple

import gh_sections

HEADING = "## 受け入れ条件"
_BOX = re.compile(r"- \[([ xX])\](?=[ \t\r]|$)")


class Criterion(NamedTuple):
    number: int
    checked: bool


class _Box(NamedTuple):
    number: int
    checked: bool
    at: int  # 印の 1 文字（`[ ]` の空白）の位置


def _boxes(body: str) -> list[_Box]:
    bounds = gh_sections.section_bounds(body, HEADING)
    if bounds is None:
        return []
    start, end = bounds
    out: list[_Box] = []
    fence = None
    pos = start
    for line in body[start:end].split("\n"):
        m = gh_sections._FENCE.match(line)
        if m:
            fence = None if fence == m.group(1) else (fence or m.group(1))
        elif fence is None:
            b = _BOX.match(line)
            if b:
                out.append(_Box(len(out) + 1, b.group(1) != " ", pos + b.start(1)))
        pos += len(line) + 1
    return out


def criteria(body: str) -> list[Criterion]:
    """受け入れ条件の番号と印の有無。節が無ければ空。"""
    return [Criterion(b.number, b.checked) for b in _boxes(body)]


def check(body: str, numbers) -> str:
    """`numbers` の受け入れ条件の `[ ]` を `[x]` にした本文。既に印のある行・範囲外の番号は変えない。"""
    want = set(numbers)
    chars = list(body)
    for b in _boxes(body):
        if b.number in want and not b.checked:
            chars[b.at] = "x"
    return "".join(chars)


def only_marks(old: str, new: str) -> bool:
    """`new` と `old` の違いが、受け入れ条件の印の空白を `x` にしたことだけか（書く前の確かめ）。"""
    if len(old) != len(new):
        return False
    allowed = {b.at for b in _boxes(old) if not b.checked}
    return all(i in allowed and new[i] == "x" for i, (o, n) in enumerate(zip(old, new)) if o != n)
