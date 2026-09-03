"""振り返りの記録先を読み取る補助。

`conftest.py` ではなく固有名のモジュールへ置く。pytest は収集したテストのあるディレクトリを
`sys.path` の先頭へ足すため、束を同時に実行すると同名のファイルが互いを覆う。

表の読み取りは `out-of-scope` の束にも同じものがある。**束は単独でも実行できる必要があり、
別の Skill のテストのディレクトリは、その束を収集しない限り `sys.path` に載らない。**
起票先の判断表は import ではなくファイルとして読む。
"""
from __future__ import annotations

import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL = SKILL_DIR / "SKILL.md"
ISSUE_TARGET = SKILL_DIR.parent / "out-of-scope" / "references" / "issue-target.md"

POST_TARGET_HEADING = "#### 投稿先を決める"
CHANGE_TABLE_HEADING = "### 3. 次に変えることを決める"
DECISION_TABLE_HEADING = "## 判断表"

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def read(path: Path) -> str:
    assert path.is_file(), f"ファイルが無い: {path}"
    return path.read_text(encoding="utf-8")


def _table_span(lines: list[str], heading: str) -> tuple[int, int]:
    """見出しの直後に来る表の、最初の行と終端の次の行を返す。"""
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        raise AssertionError(f"見出しが見つからない: {heading}")
    first = -1
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("#"):
            break
        if stripped.startswith("|"):
            if first < 0:
                first = index
            continue
        if first >= 0:
            return first, index
    assert first >= 0, f"表を読み取れない: {heading}"
    return first, len(lines)


def table(body: str, heading: str) -> tuple[list[str], list[list[str]]]:
    """見出しの直後に来る表の見出し行と本文の行を返す。"""
    lines = body.splitlines()
    first, end = _table_span(lines, heading)
    header: list[str] = []
    rows: list[list[str]] = []
    for line in lines[first:end]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not header:
            header = cells
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    assert header and rows, f"表を読み取れない: {heading}"
    return header, rows


def line_after_table(body: str, heading: str) -> str:
    """見出しの直後に来る表の、次に現れる本文の 1 行を返す。"""
    lines = body.splitlines()
    _, end = _table_span(lines, heading)
    for line in lines[end:]:
        if line.strip():
            return line.strip()
    raise AssertionError(f"表の後に本文が無い: {heading}")


def fenced_blocks(body: str) -> list[str]:
    """囲みの中身を、本文に現れる順で返す。"""
    blocks: list[str] = []
    current: list[str] | None = None
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current))
                current = None
            continue
        if current is not None:
            current.append(line)
    return blocks


def link_targets(text: str) -> list[str]:
    return [target.split("#", 1)[0].strip() for target in LINK_RE.findall(text)]
