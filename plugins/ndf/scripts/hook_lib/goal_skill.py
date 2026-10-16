"""`/goal` の条件の Skill を読み込む案内（`UserPromptSubmit` hook。#1492）。

    <hook の環境の python> hook.py goal-skill   < UserPromptSubmit の JSON

Claude Code の `/goal` は条件の文の中の Skill を展開しない。入力が `/goal /<プラグイン>:<Skill> …` のときだけ、
作業の最初に Skill ツールでその Skill を読み込むよう案内の文を返す。入力は書き換えず、状態を持たない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 1 行目だけを見る。条件の先頭の語が `/<プラグイン>:<Skill>` のときだけ当たる
PATTERN = re.compile(r"^/goal\s+/([A-Za-z0-9_.-]+):([A-Za-z0-9_.-]+)(?:\s+(.*))?$")

NOTICE = (
    "ndf: この目標の条件は Skill `{name}` を名指ししている。作業の最初の Tool の呼び出しとして Skill ツールで `{name}` を"
    "{with_args}読み込み、その手順に従う（承認ゲートでは AskUserQuestion で止まる）。"
    "Skill が見つからなければ読み込まずに、目標の条件のまま続ける。"
)


@dataclass(frozen=True)
class GoalSkill:
    plugin: str
    skill: str
    args: str

    @property
    def name(self) -> str:
        return f"{self.plugin}:{self.skill}"


def parse_goal(prompt) -> GoalSkill | None:
    """入力の文から条件の Skill と引数（条件の残り。2 行目以降を含む）を読む。当たらなければ None。"""
    if not isinstance(prompt, str):
        return None
    first, _, rest = prompt.strip().partition("\n")
    m = PATTERN.match(first.strip())
    if not m:
        return None
    args = "\n".join(p for p in ((m.group(3) or "").strip(), rest.strip()) if p)
    return GoalSkill(m.group(1), m.group(2), args)


def context(hook_input) -> str | None:
    """`UserPromptSubmit` の入力から案内の文を作る。案内しないときは None。"""
    if not isinstance(hook_input, dict):
        return None
    got = parse_goal(hook_input.get("prompt"))
    if got is None:
        return None
    with_args = f"引数 `{got.args}` で" if got.args else ""
    return NOTICE.format(name=got.name, with_args=with_args)
