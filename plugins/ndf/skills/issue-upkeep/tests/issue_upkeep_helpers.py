"""`issue-upkeep` のテストで Markdown 構造を読み取る補助。

`conftest.py` ではなく固有名のモジュールへ置く。pytest は収集したテストのあるディレクトリを
`sys.path` の先頭へ足すため、束を同時に実行すると同名のファイルが互いを覆う。
"""
from __future__ import annotations

import re


def plain(text: str) -> str:
    """強調の印と折り返しの改行を除く。言い回しの位置ではなく文を照合する。"""
    return text.replace("\n", "").replace("**", "")


def section(text: str, heading: str) -> str:
    """見出しから、同じ深さか浅い次の見出しまでを返す。囲みの中の `#` は見出しとして数えない。"""
    depth = len(heading.split(" ", 1)[0])
    lines = text.split("\n")
    try:
        start = next(i for i, line in enumerate(lines) if line.rstrip() == heading.rstrip())
    except StopIteration:
        raise ValueError(f"見出しが見つからない: {heading}")
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
    lines = [line for line in block.split("\n")[2:] if line.startswith("|")]
    return [[cell.strip().replace("**", "").strip("*").strip()
             for cell in line.strip().strip("|").split("|")]
            for line in lines]


# test_related_skills_characterization との互換性のためのエイリアス
rows = table


def bash_blocks(text: str) -> list[str]:
    """本文中の bash コードブロックの内容を返す。"""
    return re.findall(r"^```bash\n(.*?)^```$", text, re.S | re.M)
