"""`design` の雛形が他の参照の定義を再掲しないことを固定する（#526 / R4-001・R4-003）。

**図の階層と役割は `system-architecture.md` と `structure-behavior.md`、要否は
`deliverables.md` が唯一の定義である。** 雛形の「図の水準」が 4 階層の表を再掲していたが、
同じ規則を 3 箇所に持つと、図の役割か要否が参照先ごとにずれる。雛形には参照の経路だけを残す。

**「タスクを機械的に導けるだけの情報を書く」は雛形の「各節が実装計画のどこへつながるか」が
定義元である。** `SKILL.md` の手順 2 は雛形を参照先として指しており、同じ文を持たない。

この現状固定テストは、書き換えの前後で次が保たれることを示す。

1. 雛形の「図の水準」から、要否表と各図の書き方と記法のガイドへ到達できる
2. 「図の水準」は 4 階層の表を再掲しない
3. 規則の文は雛形にだけあり、`SKILL.md` の手順 2 は雛形を参照して言い直さない
"""
from __future__ import annotations

import pathlib

DESIGN = pathlib.Path(__file__).resolve().parents[1]
SKILL = DESIGN / "SKILL.md"
TEMPLATE = DESIGN / "references" / "design-template.md"

RULE = "**タスクを機械的に導けるだけの情報を書く。** 導けない設計は、次の工程が成立しない。"


def _section(path: pathlib.Path, heading: str) -> str:
    """見出しの次の行から、同じ水準の次の見出しの手前までを返す。"""
    level = heading.split(" ", 1)[0]
    body = path.read_text(encoding="utf-8")
    rest = body[body.index(heading + "\n") + len(heading) + 1:]
    end = rest.find("\n" + level + " ")
    return rest if end < 0 else rest[:end]


def test_the_diagram_level_section_reaches_every_definition() -> None:
    """要否表・各図の書き方・記法のガイドへの経路を持つ。"""
    section = _section(TEMPLATE, "## 図の水準")
    assert "[deliverables.md](deliverables.md)" in section
    assert "system-architecture.md" in section
    assert "structure-behavior.md" in section
    assert "`markdown-writing` の図表ガイド" in section


def test_the_diagram_level_section_does_not_restate_the_levels() -> None:
    """4 階層の表と、クラス図の対象範囲を言い直さない。"""
    section = _section(TEMPLATE, "## 図の水準")
    assert "| 階層 |" not in section
    assert "変更が触る型だけに絞る" not in section


def test_the_rule_lives_only_in_the_template() -> None:
    """規則の文は雛形の対応表の節にだけ置く。"""
    assert _section(TEMPLATE, "## 各節が実装計画のどこへつながるか").count(RULE) == 1
    assert SKILL.read_text(encoding="utf-8").count(RULE) == 0


def test_step_two_points_to_the_template() -> None:
    """手順 2 は雛形を参照先として指す。"""
    step = _section(SKILL, "### 2. 設計文書を書く")
    assert "[references/design-template.md](references/design-template.md)" in step
    assert "`implementation-plan` のどのタスクへつながるか" in step
