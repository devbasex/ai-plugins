"""CHANGELOG の版の節を組み、差し込み、読み出す（純粋な関数）。

節は `## [<プラグイン名> <基底の版>] - <日付>` の見出しから次の `## ` の見出しの前までである。開発版は載せない
（呼び手が本番の版のときだけ書く）。

- 同じプラグインの同じ基底の版の節が既にあれば、その節だけを置き換える。ほかの節は 1 行も変えない
- 無ければ、最初の版の節の前（前書きの後）へ差し込む。版の節が 1 つも無ければ末尾へ足す
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from ndf.domains.release import version_decide

# 版の節の見出しの先頭
SECTION_MARK = "## "


class Entry(NamedTuple):
    """節の 1 行の元（含む PR の題と番号）。"""

    number: int
    title: str


def changelog_heading(plugin: str, version: str, date: str) -> str:
    """CHANGELOG の版の見出し（`## [<プラグイン名> <版>] - <日付>`）。"""
    return f"{changelog_key(plugin, version)} - {date}"


def changelog_key(plugin: str, version: str) -> str:
    """CHANGELOG の版の見出しの、日付を除いた先頭（節を探す鍵）。"""
    return f"{SECTION_MARK}[{plugin} {version_decide.base_of(version)}]"


def block(plugin: str, version: str, date: str, entries: Sequence[Entry]) -> list[str]:
    """節の行（見出し・空行・PR ごとの 1 行・空行）。"""
    lines = [changelog_heading(plugin, version, date), ""]
    lines += [f"- {e.title.strip()}（#{e.number}）" for e in sorted(entries, key=lambda e: e.number)]
    return [*lines, ""]


def _span(lines: list[str], plugin: str, version: str) -> tuple[int, int] | None:
    """版の節の (見出しの行, 次の節の行)。無ければ None。"""
    key = changelog_key(plugin, version)
    at = next((i for i, line in enumerate(lines) if line == key or line.startswith(key + " ")), None)
    if at is None:
        return None
    end = next((i for i in range(at + 1, len(lines)) if lines[i].startswith(SECTION_MARK)), len(lines))
    return at, end


def insert(text: str, plugin: str, version: str, new: Sequence[str]) -> str:
    """節を差し込んだ（か置き換えた）全文。"""
    lines = text.split("\n")
    span = _span(lines, plugin, version)
    if span is not None:
        lines[span[0] : span[1]] = list(new)
    else:
        first = next((i for i, line in enumerate(lines) if line.startswith(SECTION_MARK)), len(lines))
        lines[first:first] = list(new)
    return "\n".join(lines)


def notes(text: str, plugin: str, version: str) -> str | None:
    """版の節の本文（見出しを除き、前後の空行を落とす）。節が無ければ None。"""
    lines = text.split("\n")
    span = _span(lines, plugin, version)
    if span is None:
        return None
    return "\n".join(lines[span[0] + 1 : span[1]]).strip()
