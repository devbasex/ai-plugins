"""要求の手動確認の行（#1485 の決定 5・決定 9）。

要求（`issues/issue-<番号>-requirements.md`）の「検証手段」の表で、項目が「手動確認」で始まる行を読む。項目が
`手動確認（マージ前）` の行だけがマージ前で、それ以外はリリース後テストへ回す。PR 本文の「手動確認」の節は
マージ前の行をチェックボックスで並べ、利用者が印を付けると確認済みになる。`merge-gate` は印の無いマージ前の行を
数えて承認ゲートで止まる。PR 本文の読み書きは Markdown のパーサーを使わず、行の形で見る（`merged-steps.py` は
Markdown のパーサーを読み込まない）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

HEADING = "## 手動確認"
ITEM = "手動確認"
BEFORE_MERGE = "手動確認（マージ前）"
SECTION = "検証手段"  # 要求の中の、手動確認の行を読む節
_BOX = re.compile(r"^\s*-\s+\[( |x|X)\]\s+(#\d+\s+マージ前:.*)$")


class ManualRow(NamedTuple):
    issue: int
    text: str
    before_merge: bool


def rows_of(text: str, issue: int) -> list[ManualRow]:
    """要求の本文から手動確認の行を読む。「検証手段」の節の表の、項目が「手動確認」で始まる行だけを見る。"""
    import md  # Markdown のパーサーは要求を読むときだけ使う（merged-steps.py の経路で読み込まない）

    sec = md.section_named(text, SECTION)
    if sec is None:
        return []
    out = []
    for table in md.tables(text):
        if not (sec.start <= table.start < sec.end) or len(table.header) < 2:
            continue
        for row in table.rows:
            item = (row[0] if row else "").strip()
            what = (row[1] if len(row) > 1 else "").strip()
            if item.startswith(ITEM) and what:
                out.append(ManualRow(issue, " ".join(what.split()), item == BEFORE_MERGE))
    return out


def read_rows(root: Path, issues, path_of) -> list[ManualRow]:
    """課題ごとに要求の写し（`path_of(番号)`）を読み、手動確認の行を課題の順に返す。写しの無い課題は飛ばす。"""
    out: list[ManualRow] = []
    for n in issues:
        f = Path(root) / path_of(n)
        if f.is_file():
            out.extend(rows_of(f.read_text(encoding="utf-8"), int(n)))
    return out


def line_of(row: ManualRow) -> str:
    """節の 1 行の本文（チェックボックスを除く）。"""
    when = "マージ前" if row.before_merge else "リリース後テストへ回す"
    return f"#{row.issue} {when}: {row.text}"


def checked_lines(body: str) -> set[str]:
    """PR 本文で印の付いたマージ前の行の本文。"""
    return {m.group(2).strip() for m in map(_BOX.match, (body or "").splitlines()) if m and m.group(1) in "xX"}


def section(rows: list[ManualRow], old_body: str = "") -> str:
    """PR 本文の「手動確認」の節。行が無ければ空（節を置かない）。`old_body` で印の付いた行は印を引き継ぐ。"""
    if not rows:
        return ""
    done = checked_lines(old_body)
    lines = []
    for r in rows:
        text = line_of(r)
        lines.append(f"- [{'x' if text in done else ' '}] {text}" if r.before_merge else f"- {text}")
    return HEADING + "\n\n" + "\n".join(lines)


def unchecked_before_merge(body: str) -> list[str]:
    """PR 本文の「手動確認」の節にある、印の無いマージ前の行の本文。"""
    out, inside = [], False
    for line in (body or "").splitlines():
        if line.startswith("## "):
            inside = line.strip() == HEADING
            continue
        m = _BOX.match(line) if inside else None
        if m and m.group(1) == " ":
            out.append(m.group(2).strip())
    return out
