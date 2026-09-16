"""Markdown を読むテストで共有する補助。"""
from __future__ import annotations

import re


def plain(text: str) -> str:
    """強調の印と折り返しの改行を除く。"""
    return text.replace("\n", "").replace("**", "")


def section(text: str, heading: str) -> str:
    """見出しから、同じ深さか浅い次の見出しまでを返す。"""
    depth = len(heading.split(" ", 1)[0])
    lines = text.split("\n")
    start = lines.index(heading)
    fenced = False
    for end in range(start + 1, len(lines)):
        line = lines[end]
        if line.startswith("```"):
            fenced = not fenced
            continue
        match = re.match(r"^(#+) ", line)
        if not fenced and match and len(match.group(1)) <= depth:
            return "\n".join(lines[start:end])
    return "\n".join(lines[start:])


def table(text: str, header: str) -> list[list[str]]:
    """見出し行で始まる表の、データ行のセルを返す。太字の印は外す。"""
    block = text[text.index(header):]
    block = block[:block.index("\n\n")] if "\n\n" in block else block
    rows = [line for line in block.split("\n")[2:] if line.startswith("|")]
    return [
        [cell.strip().replace("**", "") for cell in row.strip().strip("|").split("|")]
        for row in rows
    ]
