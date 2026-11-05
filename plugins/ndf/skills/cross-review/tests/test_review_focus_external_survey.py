"""外部の調査の確認の観点を、分類 `design` / `code` を持つ PR の担当へ渡す（#2519 の I8・I9）。

期待値は `pr-review` の SKILL.md から節を読んだ文で、文言を固定しない。
"""

from __future__ import annotations

import gh_sections
import pytest
import review_lib.categories as categories_mod
import review_lib.commands.init
import review_lib.review_focus as rf


def _files(*paths: str) -> list[dict]:
    return [{"paths": [p], "status": "M"} for p in paths]


def _section() -> str:
    text = rf.PR_REVIEW_SKILL.read_text(encoding="utf-8")
    section = gh_sections.get_section(text, rf.EXTERNAL_SURVEY_HEADING)
    assert section, "pr-review の SKILL.md に外部の調査の確認の節が無い"
    return section


def _auto(*paths: str, profile=None) -> str:
    files = _files(*paths)
    cats = categories_mod._classify_changed_files(files)
    kwargs = {} if profile is None else {"profile": profile}
    return rf._auto_review(cats, files, **kwargs)[1]


@pytest.mark.parametrize("paths", [("issues/issue-1-design.md",), ("src/app.py", "docs/a.md")])
def test_design_and_code_prs_carry_the_section(paths):
    """受け入れ条件 14（載る例）: 設計 PR と実装 PR の観点に、節の文がそのまま入る。"""
    text = _auto(*paths)
    assert _section() in text
    assert text.index(_section()) > text.index("## 自動追加レビュー観点: 共通")


def test_docs_only_pr_does_not_carry_the_section():
    """受け入れ条件 14（載らない例）。"""
    assert _section() not in _auto("docs/a.md")


def test_a_profile_replacing_the_default_categories_keeps_the_section():
    """決定 11: replace_auto_categories のプロファイルでも外さない。"""
    profile = review_lib.commands.init.review_criteria.profile_from(
        {"categories": {"py": {"paths": ["**/*.py"], "instructions": "## 自動追加レビュー観点: py"}}, "replace_auto_categories": True}
    )
    text = _auto("src/app.py", profile=profile)
    assert _section() in text
    assert rf.COMMON_REVIEW_TEMPLATE not in text


def test_design_pr_detail_stage_carries_the_section_but_model_stage_does_not(tmp_path):
    """設計 PR の詳細レビューには入り、モデルレビューには入らない。"""
    files = _files("issues/issue-1-design.md")
    auto = _auto("issues/issue-1-design.md")
    instructions = rf._combined_review_instructions(auto, "")
    stages = rf._design_stage_fields("design", tmp_path, files, instructions, "")["review_instructions_by_stage"]
    assert _section() in stages["detail"]
    assert _section() not in stages["model"]


def test_a_skill_without_the_section_stops(monkeypatch, tmp_path):
    """I8: 節を読めなければ観点を組まずに 1 で止まる。"""
    skill = tmp_path / "SKILL.md"
    skill.write_text("# pr-review\n\n## 観点\n\n### 第 1 段: 仕様適合\n", encoding="utf-8")
    monkeypatch.setattr(rf, "PR_REVIEW_SKILL", skill)
    with pytest.raises(SystemExit) as exc:
        _auto("src/app.py")
    assert exc.value.code == 1


def test_a_missing_skill_stops(monkeypatch, tmp_path):
    monkeypatch.setattr(rf, "PR_REVIEW_SKILL", tmp_path / "missing" / "SKILL.md")
    with pytest.raises(SystemExit) as exc:
        _auto("issues/issue-1-design.md")
    assert exc.value.code == 1


def test_docs_only_pr_does_not_read_the_skill(monkeypatch, tmp_path):
    """観点を載せない PR は SKILL.md を読まず、無くても止まらない。"""
    monkeypatch.setattr(rf, "PR_REVIEW_SKILL", tmp_path / "missing" / "SKILL.md")
    assert "ドキュメントのみ PR" in _auto("docs/a.md")

