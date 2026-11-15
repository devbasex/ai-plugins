"""検査のトリガーの行数の判定（旧 `scripts/check-trigger.py` の `lines`。#3087）。

範囲（前回の検査から今まで）の変更行数（追加 + 削除）が `.ndf/pace.json` の `triggers.lines` を超えたら立つ。
行数は `Git.changed_lines` で、範囲の first-parent の各コミットの変更行数を足して数え、除く PR（検査・リリース）の
コミットの分は数えない。範囲全体の差分から除く PR の差分を引くと、同じファイルを順に書き換えるリリースの PR の
重なりを二重に引き、行数が実際より小さくなる（0 へ潰れる）ためである。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Optional

from ndf.adapters.git import Git

LINES = "lines"


@dataclass(frozen=True)
class Fired:
    """立ったトリガー（名前・測った値・閾値）。"""

    trigger: str
    value: int
    threshold: int


@dataclass(frozen=True)
class LinesJudgement:
    """範囲の行数と、行数のトリガーが立ったか（立たなければ `fired` は None）。"""

    lines: int
    fired: Optional[Fired]


def lines_fired(lines: int, threshold: int) -> Optional[Fired]:
    """行数が閾値を超えたら（等しいときは立たない）行数のトリガーを返す。"""
    return Fired(LINES, lines, threshold) if lines > threshold else None


def judge_lines(git: Git, base: str, head: str, exclude: Iterable[str], threshold: int) -> LinesJudgement:
    """`base..head` の行数（`exclude` のコミットの分を除く）を数え、行数のトリガーを判定する。"""
    lines = git.changed_lines(base, head, exclude)
    return LinesJudgement(lines, lines_fired(lines, threshold))
