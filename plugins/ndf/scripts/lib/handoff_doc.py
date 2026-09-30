"""引継ぎ文書の節の読み書き（#1560）。`sprint-state.py` と `handoff.py` が使う。

見出しに語を含む節を探し、節の本文だけを置き換える。囲みのコードブロックの中の `#` は見出しとみなさない
（見出しの読み取りは `md.headings`）。見出しは行頭の `#` で始まるもの（ATX）だけを数える。
"""

from __future__ import annotations

import md


def find_section(text: str, word: str) -> tuple[int, int, int, str] | None:
    """見出しに word を含む最初の節の (見出しの行の始まり, 本文の始まり, 本文の終わり, 見出しの行) を返す。

    本文は、見出しと同じか浅い見出しの手前まで。"""
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    atx = [h for h in md.headings(text) if lines[h.line].startswith("#")]
    for k, h in enumerate(atx):
        if word not in lines[h.line].rstrip("\r\n"):
            continue
        end = next((o.line for o in atx[k + 1 :] if o.level <= h.level), None)
        return offsets[h.line], offsets[h.line + 1], len(text) if end is None else offsets[end], lines[h.line]
    return None


def heading_text(line: str) -> str:
    """見出しの行から `#` を除いた文。"""
    return line.rstrip("\r\n").lstrip("#").strip()


def replace_body(text: str, word: str, body: str) -> str | None:
    """見出しに word を含む最初の節の本文だけを body に置き換えた全文。節が無ければ `None`。"""
    found = find_section(text, word)
    if found is None:
        return None
    _, body_start, body_end, _ = found
    return text[:body_start] + body + text[body_end:]


def level_headings(text: str, level: int) -> list[tuple[str, int]]:
    """深さ level の ATX 見出しの (文, 行の番号) の並び。囲みの中の `#` を数えない。"""
    lines = text.splitlines()
    return [(heading_text(lines[h.line]), h.line) for h in md.headings(text) if h.level == level and lines[h.line].startswith("#")]
