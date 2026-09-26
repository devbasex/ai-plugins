"""本文の節の文字列の操作（#1142 の L0 で gh_parts から分けた）。GitHub を呼ばない。

節は「見出しの行から、同じか上の段の次の見出しまで」である。**最後の節の後ろへ足した行を
節に含めないため、書いた節の終わりへ目印（`SECTION_END`）を置く。** 目印があれば節は目印で終わり、
目印より後ろは節の外として残す（#659: 最後の節を差し替えると、末尾へ足した 1 行が消えた）。
"""
from __future__ import annotations

import re
from typing import NamedTuple

SECTION_END = "<!-- ndf:section-end -->"
_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})[ \t]+\S")


class _Span(NamedTuple):
    start: int      # 見出しの行
    content: int    # 見出しの次の行
    end: int        # 節の終わり（目印の行を含まない）
    after: int      # 節の外が始まる行（目印があれば目印の次）
    last: bool      # 後ろに見出しが無い


def _lines(body: str) -> list[str]:
    return body.replace("\r\n", "\n").split("\n")


def _heading_lines(lines: list[str]) -> list[tuple[int, int]]:
    """コードブロックの外にある見出しの `(行, 段)`。"""
    out: list[tuple[int, int]] = []
    fence = None
    for i, line in enumerate(lines):
        m = _FENCE.match(line)
        if m:
            fence = None if fence == m.group(1) else (fence or m.group(1))
            continue
        if fence is None:
            h = _HEADING.match(line)
            if h:
                out.append((i, len(h.group(1))))
    return out


def _find(lines: list[str], heading: str) -> _Span | None:
    heading = heading.strip()
    level = len(heading) - len(heading.lstrip("#"))
    heads = _heading_lines(lines)
    for n, (i, _) in enumerate(heads):
        if lines[i].rstrip() != heading:
            continue
        nxt = next((j for j, lv in heads[n + 1:] if lv <= level), None)
        end = len(lines) if nxt is None else nxt
        marker = next((k for k in range(i + 1, end) if lines[k].strip() == SECTION_END), None)
        if marker is not None:
            return _Span(i, i + 1, marker, marker + 1, nxt is None)
        return _Span(i, i + 1, end, end, nxt is None)
    return None


def get_section(body: str, heading: str) -> str | None:
    """節の中身（見出しと目印を除く）。節が無ければ `None`。"""
    lines = _lines(body)
    span = _find(lines, heading)
    if span is None:
        return None
    return "\n".join(lines[span.content:span.end]).strip("\n")


def _section_block(heading: str, content: str) -> list[str]:
    text = content.replace("\r\n", "\n").strip("\n")
    first, _, rest = text.partition("\n")
    if first.rstrip() == heading.strip():
        text = rest.strip("\n")
    return [heading.strip(), "", *(text.split("\n") if text else []), SECTION_END]


def replace_section(body: str, heading: str, content: str) -> str:
    """節を差し替える。無ければ末尾へ足す。節の外は変えず、同じ呼び出しを繰り返しても変わらない。"""
    lines = _lines(body)
    block = _section_block(heading, content)
    span = _find(lines, heading)
    if span is None:
        head = "\n".join(lines).rstrip("\n")
        return (head + "\n\n" if head else "") + "\n".join(block) + "\n"
    before = lines[:span.start]
    if span.after != span.end:
        # 目印の後ろは節の外である。空行も含めてそのまま残す。
        return "\n".join(before + block + lines[span.after:])
    after = lines[span.after:]
    while after and not after[0].strip():
        after = after[1:]
    if any(x.strip() for x in after):
        return "\n".join(before + block + [""] + after)
    return "\n".join(before + block) + "\n"


def append_line(body: str, line: str) -> str:
    """本文の末尾へ 1 行を足す。同じ行が既にあれば足さない。

    **最後の節が目印を持たなければ、足す前に目印を置いて閉じる。** 目印が無いまま足すと、足した行が
    最後の節の中に入り、次の節の差し替えで消える（#659）。
    """
    line = line.strip("\n")
    lines = _lines(body)
    if any(x.rstrip() == line.rstrip() for x in lines):
        return body
    heads = _heading_lines(lines)
    if heads:
        last_i, _ = heads[-1]
        tail = lines[last_i + 1:]
        if not any(x.strip() == SECTION_END for x in tail):
            while lines and not lines[-1].strip():
                lines.pop()
            lines.append(SECTION_END)
    head = "\n".join(lines).rstrip("\n")
    return (head + "\n\n" if head else "") + line + "\n"
