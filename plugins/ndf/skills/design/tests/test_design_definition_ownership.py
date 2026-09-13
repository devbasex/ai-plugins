"""設計の雛形と規約の定義元を固定する（#526 / R4-002・R4-004）。"""
from __future__ import annotations

import pathlib


DESIGN = pathlib.Path(__file__).resolve().parents[1]
SKILL = DESIGN / "SKILL.md"
TEMPLATE = DESIGN / "references" / "design-template.md"


def _section(path: pathlib.Path, heading: str) -> str:
    """見出しから、同じ水準の次の見出しの手前までを返す。"""
    level = heading.split(" ", 1)[0]
    body = path.read_text(encoding="utf-8")
    rest = body[body.index(heading) + len(heading):]
    end = rest.find(f"\n{level} ")
    return rest if end < 0 else rest[:end]


def test_all_sections_keep_their_implementation_plan_usage() -> None:
    """全 10 節と実装計画での使われ方の対応を固定する。"""
    section = _section(TEMPLATE, "## 各節が実装計画のどこへつながるか")
    expected_rows = {
        "機能一覧": "タスクを機能の単位で割る根拠になる",
        "構成要素": "タスクの単位になる",
        "構造": "変更の範囲を読む",
        "データ構造": "移行タスクと検査タスクになる",
        "入出力の契約": "契約テストのタスクになる",
        "処理の流れ": "順序の依存を読む",
        "非機能の実現方式": "検査タスクになる",
        "決定の記録": "代替案の再検討を止める",
        "テスト設計": "テストのタスクになる",
        "未確認のまま残ること": "リスクとして計画へ移す",
    }

    for heading, usage in expected_rows.items():
        assert f"| {heading} | {usage} |" in section


def test_deliverables_owns_what_each_section_contains() -> None:
    """雛形は内容を再掲せず、成果物規約の定義元を指す。"""
    section = _section(TEMPLATE, "## 各節が実装計画のどこへつながるか")
    assert "[deliverables.md](deliverables.md) の「成果物ごとの中身」" in section
    assert "| 節 | 書く内容 |" not in section


def test_consistency_rationales_live_only_in_the_template() -> None:
    """4 モード適用と結果を残さない根拠は雛形だけが持つ。"""
    template_section = _section(TEMPLATE, "## 進む前に突き合わせる対")
    skill_step = _section(SKILL, "### 4. 進む前に文書の内部整合を突き合わせる")

    for rule in ("4 モードすべてで行う", "確かめた結果は残さない"):
        assert rule in template_section
        assert rule not in skill_step
    assert "[references/design-template.md](references/design-template.md)" in skill_step
