"""CHANGELOG のバージョンのセクションを組み、差し込み、読み出す（純粋な関数）。

セクションは見出し（設定の書式 `changelog_heading` から `names_decide.NameFormats.heading_of` が作る）から次の `## ` の見出しの
前までである。セクションは見出しのキー（`NameFormats.heading_key`。日付から後ろを除いた先頭）で探す。開発版は載せない
（呼び手が本番のバージョンのときだけ書く）。

- 同じキーのセクションが既にあれば、そのセクションだけを置き換える。ほかのセクションは 1 行も変えない
- 無ければ、最初のバージョンのセクションの前（前書きの後）へ差し込む。バージョンのセクションが 1 つも無ければ末尾へ足す
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

# バージョンのセクションの見出しの先頭
SECTION_MARK = "## "


class Entry(NamedTuple):
    """セクションの 1 行の元（含む PR の題と番号）。"""

    number: int
    title: str


def block(heading: str, entries: Sequence[Entry]) -> list[str]:
    """セクションの行（見出し・空行・PR ごとの 1 行・空行）。"""
    lines = [heading, ""]
    lines += [f"- {e.title.strip()}（#{e.number}）" for e in sorted(entries, key=lambda e: e.number)]
    return [*lines, ""]


def _span(lines: list[str], key: str) -> tuple[int, int] | None:
    """キーで始まる見出しのセクションの (見出しの行, 次のセクションの行)。無ければ None。"""
    at = next((i for i, line in enumerate(lines) if line == key or line.startswith(key + " ")), None)
    if at is None:
        return None
    end = next((i for i in range(at + 1, len(lines)) if lines[i].startswith(SECTION_MARK)), len(lines))
    return at, end


def insert(text: str, key: str, new: Sequence[str]) -> str:
    """セクションを差し込んだ（か置き換えた）全文。"""
    lines = text.split("\n")
    span = _span(lines, key)
    if span is not None:
        lines[span[0] : span[1]] = list(new)
    else:
        first = next((i for i, line in enumerate(lines) if line.startswith(SECTION_MARK)), len(lines))
        lines[first:first] = list(new)
    return "\n".join(lines)


def notes(text: str, key: str) -> str | None:
    """バージョンのセクションの本文（見出しを除き、前後の空行を落とす）。セクションが無ければ None。"""
    lines = text.split("\n")
    span = _span(lines, key)
    if span is None:
        return None
    return "\n".join(lines[span[0] + 1 : span[1]]).strip()
