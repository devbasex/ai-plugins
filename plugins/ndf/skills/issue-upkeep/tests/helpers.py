"""2 つの固定テストが共有する Markdown 切り出しの補助。

`test_issue_upkeep_layout.py` と `test_related_skills_characterization.py` が
同じ目的で各自に持っていた helper を 1 箇所へ寄せたもの。見出し・表・囲みの
切り出し規則は同じ契約であり、片方だけを直すと照合が 2 系統へ割れる。

- `section` は囲み（```）の中の `#` を見出しとして数えない走査版に寄せた。
  正規表現版との差は末尾の空行の有無だけで、切り出す範囲は同じである。
- `table` のセル整形は太字の印を外す（`strip("*")`）版に寄せた。`replace("**", "")`
  版は太字が奇数個のときに印を取り切れず、両ファイルの現行の値と食い違う。
"""
from __future__ import annotations

import pathlib
import re


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def plain(text: str) -> str:
    """強調の印と折り返しの改行を除く。言い回しの位置ではなく文を照合する。"""
    return text.replace("\n", "").replace("**", "")


def section(text: str, heading: str) -> str:
    """見出しから、同じ深さか浅い次の見出しまでを返す。囲みの中の `#` は見出しとして数えない。"""
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
    """見出し行で始まる表の、データ行のセルを返す。太字の印は外す。

    見出しが本文に無ければ `text.index` が `ValueError` を投げる。表を消したときに
    固定テストが黙って通らないための番犬。
    """
    block = text[text.index(header):]
    block = block[:block.index("\n\n")] if "\n\n" in block else block
    rows = [line for line in block.split("\n")[2:] if line.startswith("|")]
    return [[cell.strip().strip("*").strip() for cell in row.strip("|").split("|")]
            for row in rows]


def bash_blocks(text: str) -> list[str]:
    return re.findall(r"^```bash\n(.*?)^```$", text, re.S | re.M)
