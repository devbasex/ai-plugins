"""起票先の判断を読み取る補助。

`conftest.py` ではなく固有名のモジュールへ置く。pytest は収集したテストのあるディレクトリを
`sys.path` の先頭へ足すため、束を同時に実行すると同名のファイルが互いを覆う。**束ごとに
違う名前を付ければ、どの束から実行しても同じものが読まれる。**

表の読み取りは `retrospective` の束にも同じものがある。**束は単独でも実行できる必要があり、
別の Skill のテストのディレクトリは、その束を収集しない限り `sys.path` に載らない。**
共有するには置き場所をリポジトリの根へ上げることになるため、束ごとに持つ。
"""
from __future__ import annotations

from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL = SKILL_DIR / "SKILL.md"
REFERENCE = SKILL_DIR / "references" / "issue-target.md"

DECISION_TABLE_HEADING = "## 判断表"
RESOLUTION_TABLE_HEADING = "## 起票先のリポジトリを決める"


def read(path: Path) -> str:
    assert path.is_file(), f"ファイルが無い: {path}"
    return path.read_text(encoding="utf-8")


def table(body: str, heading: str) -> tuple[list[str], list[list[str]]]:
    """見出しの直後に来る表の見出し行と本文の行を返す。

    読み取れないこと自体を失敗として扱う。素通りさせると、表の書き方を変えるだけで
    この検査を無効にできる。
    """
    lines = body.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        raise AssertionError(f"見出しが見つからない: {heading}")
    header: list[str] = []
    rows: list[list[str]] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("#"):
            break
        if not stripped.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not header:
            header = cells
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    assert header and rows, f"表を読み取れない: {heading}"
    return header, rows


def headings(body: str, prefix: str) -> list[str]:
    """その深さの見出しを、本文に現れる順で返す。囲みの中は数えない。"""
    found: list[str] = []
    fenced = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if line.startswith(prefix):
            found.append(line[len(prefix) :].strip())
    return found


def command_lines(body: str) -> list[str]:
    """囲みの中の行だけを返す。

    実行できる呼び出しかどうかを見るため、本文の説明に出てくる同じ語は数えない。
    """
    found: list[str] = []
    fenced = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            found.append(line)
    return found
