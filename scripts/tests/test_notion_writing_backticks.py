"""`notion-writing` のコード表記が対になっていることを固定する（#417 の 5）。

**対象はこの 1 本に限る。** 引用の中のフェンスや、行をまたぐコード表記があるため、
リポジトリ全体へ同じ規則を掛けると偽の指摘が出る（実測で 23 件）。バッククォートを
**含む文字**を並べる文はこの Skill の本文にしかなく、そこだけが二重の囲みを要する。
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TARGET = REPO / "plugins" / "ndf" / "skills" / "notion-writing" / "SKILL.md"


def unbalanced_lines() -> list[tuple[int, str]]:
    """フェンスの外で、コード表記の囲みが閉じていない行を返す。

    **数の偶奇では見ない。** バッククォートそのものを含む表記は二重の囲みで書くため、
    その行のバッククォートは奇数になる。CommonMark と同じく、**同じ長さの連なりで
    開いて閉じる**ものとして読む。
    """
    found: list[tuple[int, str]] = []
    in_fence = False
    for number, line in enumerate(TARGET.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        runs = [len(run) for run in re.findall(r"`+", line)]
        index = 0
        while index < len(runs):
            opening = runs[index]
            closing = next(
                (j for j in range(index + 1, len(runs)) if runs[j] == opening), None
            )
            if closing is None:
                found.append((number, line))
                break
            index = closing + 1
    return found


def test_every_code_span_is_closed() -> None:
    assert unbalanced_lines() == []
