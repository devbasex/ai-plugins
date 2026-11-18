"""CHANGELOG のバージョンのセクションを組み、差し込み、読み出す（純粋な関数）。

セクションは見出し（設定の書式 `changelog_heading` から `names_decide.NameFormats.heading_of` が作る）から次の `## ` の見出しの
前までである。セクションは見出しの書式に日付まで含めて全体一致する行（`NameFormats.heading_pattern`）で探す。開発版は載せない
（呼び手が本番のバージョンのときだけ書く）。

- 同じ見出しのセクションが既にあれば、そのセクションだけを置き換える。ほかのセクションは 1 行も変えない
- 無ければ、設定の書式に合う最初のバージョンの見出し（`NameFormats.versions_pattern`）の前へ差し込む。書式に合う見出しが
  無ければ、未リリースのセクション（Keep a Changelog の `## [Unreleased]`）を除いた最初の `## ` の前へ差し込み、それも無ければ末尾へ足す。
  未リリースのセクションは先頭に残る
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import NamedTuple

# バージョンのセクションの見出しの先頭
SECTION_MARK = "## "
# 未リリースのセクションの見出し（Keep a Changelog 1.1.0 の `## [Unreleased]`。新しいバージョンをこのセクションより前へ入れない）
UNRELEASED = re.compile(r"##\s+\[?unreleased\]?(?:\s.*)?", re.IGNORECASE)


class Entry(NamedTuple):
    """セクションの 1 行の元（含む PR の題と番号）。"""

    number: int
    title: str


def block(heading: str, entries: Sequence[Entry]) -> list[str]:
    """セクションの行（見出し・空行・PR ごとの 1 行・空行）。"""
    lines = [heading, ""]
    lines += [f"- {e.title.strip()}（#{e.number}）" for e in sorted(entries, key=lambda e: e.number)]
    return [*lines, ""]


def _span(lines: list[str], heading: re.Pattern[str]) -> tuple[int, int] | None:
    """見出しの式に全体一致する行のセクションの (見出しの行, 次のセクションの行)。無ければ None。"""
    at = next((i for i, line in enumerate(lines) if heading.fullmatch(line.rstrip())), None)
    if at is None:
        return None
    end = next((i for i in range(at + 1, len(lines)) if lines[i].startswith(SECTION_MARK)), len(lines))
    return at, end


def _insert_at(lines: list[str], versions: re.Pattern[str]) -> int:
    """新しいセクションを差し込む行（書式に合う最初の見出し・未リリースのセクションを除く最初の `## `・末尾の順）。"""
    found = next((i for i, line in enumerate(lines) if versions.fullmatch(line.rstrip())), None)
    if found is not None:
        return found
    other = (i for i, line in enumerate(lines) if line.startswith(SECTION_MARK) and not UNRELEASED.fullmatch(line.rstrip()))
    return next(other, len(lines))


def insert(text: str, heading: re.Pattern[str], new: Sequence[str], versions: re.Pattern[str]) -> str:
    """セクションを差し込んだ（か置き換えた）全文。`versions` は書式に合うどのバージョンの見出しにも当たる式。"""
    lines = text.split("\n")
    span = _span(lines, heading)
    if span is not None:
        lines[span[0] : span[1]] = list(new)
    else:
        at = _insert_at(lines, versions)
        lines[at:at] = list(new)
    return "\n".join(lines)


def notes(text: str, heading: re.Pattern[str]) -> str | None:
    """バージョンのセクションの本文（見出しを除き、前後の空行を落とす）。セクションが無ければ None。"""
    lines = text.split("\n")
    span = _span(lines, heading)
    if span is None:
        return None
    return "\n".join(lines[span[0] + 1 : span[1]]).strip()
