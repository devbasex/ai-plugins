"""`/ndf:external-ai` を指す Skill は、それと一緒に配られることを固定する（#286）。

`pr-review` は外部 CLI の起動の約束（フラグの並び・完了の検知・成果物の回収）を
`external-ai` とは別に持っていた。前提は「同梱されていない runtime では以下の要点に従う」
だったが、配る先（今は Claude Code と Codex）のすべてが両方を一緒に配っている。要点は正本の複製であり、実際に
`--print-timeout` が食い違っていた。

要点を消して参照だけを指す形にしたため、**一本化が成り立つ条件そのものを固定する**。
要点の字面を突き合わせる案は採らない。2 か所の書き方が違うため、突き合わせるにはどちらかを
正本と同じ字面へ揃えることになり、揃えるなら片方を消すのと変わらない。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / "plugins/ndf/skills"
# 配る Skill の表。行ごとに Skill の名前と配るランタイムを持つ
TABLE = json.loads((REPO / "plugins/ndf/manifests/skills.json").read_text(encoding="utf-8"))
RUNTIMES = sorted(TABLE["runtimes"])

POINTER = "/ndf:external-ai"
PROVIDER = "external-ai"


def _skills_pointing_at_external_ai() -> list[str]:
    """本文に `/ndf:external-ai` を書いている Skill の名前を返す。

    `SKILL.md` だけでなく参照も見る。`qa-security-scan` は報告書の雛形から指している。
    """
    names = []
    for skill in sorted(p for p in SKILLS.iterdir() if p.is_dir()):
        if skill.name == PROVIDER:
            continue
        if any(POINTER in md.read_text(encoding="utf-8") for md in skill.rglob("*.md")):
            names.append(skill.name)
    return names


def _distributed(runtime: str) -> set[str]:
    return {row["name"] for row in TABLE["skills"] if runtime in row["runtimes"]}


def test_some_skill_points_at_external_ai() -> None:
    """走査が 0 件になると、以下のチェックが何も見ないまま通る。"""
    assert _skills_pointing_at_external_ai()


def test_the_runtimes_are_found() -> None:
    """配る先が 0 件になると、以下のチェックが何も見ないまま通る。"""
    assert RUNTIMES == ["claude", "codex"], RUNTIMES


@pytest.mark.parametrize("runtime", RUNTIMES)
def test_a_skill_that_points_at_external_ai_is_distributed_with_it(runtime: str) -> None:
    distributed = _distributed(runtime)
    pointing = [s for s in _skills_pointing_at_external_ai() if s in distributed]
    if not pointing:
        pytest.skip(f"{runtime} は external-ai を指す Skill を配らない")
    assert PROVIDER in distributed, f"{runtime} は {pointing} を配るが {PROVIDER} を配らない。起動の約束を読めない配布先ができる"
