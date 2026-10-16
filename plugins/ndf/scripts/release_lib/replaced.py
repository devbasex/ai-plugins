"""置き換える箇条（#1834）。後の PR の本文の `## 置き換える箇条` で、同じ版の前の PR の「利用者向けの変化」の
箇条を CHANGELOG の版の節と承認資料の「配る中身」から除く（前の PR の本文には触れない）。

1 行に 1 つ `- #<前の PR>: <前の PR の箇条の 1 行目>`。比べるときは両側の末尾の `（#N…）` / `(#N…)` を外し、
空白の続きを 1 つにする。一致した箇条だけを除き、一致しない置き換えは何も除かずに not_found として返す。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import gh_sections

HEADING = "## 置き換える箇条"
LINE = re.compile(r"^[-*]\s+#(\d+)\s*[:：]\s*(\S.*)$")
TAIL = re.compile(r"\s*[（(]#\d+[^（）()]*[）)]\s*$")


@dataclass(frozen=True)
class Replacement:
    """置き換える箇条 1 行。text は前の PR の箇条の文（本文に書いたまま）。"""

    by: int
    target: int
    text: str


def comparable(text: str) -> str:
    """比べる形。1 行目だけを取り、末尾の `（#N…）` を外し、空白の続きを 1 つにする。"""
    first = text.partition("\n")[0]
    while (m := TAIL.search(first)) and m.start() > 0:
        first = first[: m.start()]
    return " ".join(first.split())


def replacements_in(body: str, n: int) -> tuple[list[Replacement], list[str]]:
    """PR #n の本文の置き換える箇条と、形に合わない行。節が無ければ ([], [])。"""
    found, unreadable = [], []
    for line in (gh_sections.get_section(body or "", HEADING) or "").splitlines():
        text = line.strip()
        if not text or text.lstrip("-* ").strip() in gh_sections.NO_ITEMS:
            continue
        if m := LINE.match(text):
            found.append(Replacement(n, int(m.group(1)), m.group(2).strip()))
        else:
            unreadable.append(text)
    return found, unreadable


def apply(items: dict[int, list[str]], replacements: list[Replacement]) -> tuple[dict[int, list[str]], list[dict]]:
    """PR ごとの箇条から、置き換えが指す箇条を除く。返すのは (除いた後の箇条, 結果 JSON の items の行)。
    指す PR が items に無い・文が一致しない置き換えは何も除かず `not_found`。"""
    out = {n: list(v) for n, v in items.items()}
    rows = []
    for r in replacements:
        want = comparable(r.text)
        before = out.get(r.target)
        kept = [i for i in before if comparable(i) != want] if before is not None else None
        removed = len(before) - len(kept) if before is not None else 0
        if removed:
            out[r.target] = kept
        rows.append(
            {"kind": "replacement", "name": f"#{r.by}→#{r.target}", "result": "removed" if removed else "not_found", "text": r.text}
        )
    return out, rows
