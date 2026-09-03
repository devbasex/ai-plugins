"""hook の結線と、手順書の記述が実体と合っていることを固定する（#221 / #266）。

hook が実際に登録されることは会話の単位を起こさないと確かめられないため、実機確認へ
分けている。ここで固定するのは**書式と結線**である。書式が崩れると Skill の読み込み
そのものが失敗し、モード判定を失う。
"""
from __future__ import annotations

import re

import pytest

from workflow_helpers import GUARD, SKILL_DIR, STAGE_CHECK

SKILL = SKILL_DIR / "SKILL.md"
TRACKING = SKILL_DIR / "references/projects-tracking.md"
COMPLETENESS = SKILL_DIR / "references/stage-completeness.md"
MERGED = SKILL_DIR.parent / "merged/SKILL.md"


def frontmatter() -> str:
    body = SKILL.read_text(encoding="utf-8")
    found = re.match(r"\A---\s*\n(.*?)\n---\s*\n", body, re.DOTALL)
    assert found, "frontmatter を読み取れない"
    return found.group(1)


def test_the_frontmatter_registers_a_pre_tool_use_hook() -> None:
    body = frontmatter()

    assert re.search(r"^hooks:\s*$", body, re.MULTILINE), body
    assert re.search(r"^  PreToolUse:\s*$", body, re.MULTILINE), body
    assert re.search(r'^    - matcher: "Bash"\s*$', body, re.MULTILINE), body


def test_the_hook_points_at_the_guard_in_this_skill() -> None:
    """`${CLAUDE_SKILL_DIR}` は SKILL.md の置き場所を指す。作業ディレクトリに依存しない。"""
    body = frontmatter()

    assert "${CLAUDE_SKILL_DIR}/scripts/workflow-guard.sh" in body


def test_the_hook_is_not_removed_after_the_first_run() -> None:
    """`once: true` を置かない。工程は 1 回の判定では終わらない。"""
    assert "once:" not in frontmatter()


def test_the_hook_block_is_nested_in_the_documented_order() -> None:
    """公式ドキュメントが示す入れ子（hooks → PreToolUse → matcher → hooks → command）。

    書式が崩れると Skill の読み込みそのものが失敗し、モード判定を失う。**外部の
    ライブラリに頼らず確かめる。** 読み飛ばされる検査は、崩れても気づけない。
    """
    lines = [line for line in frontmatter().splitlines() if line.strip()]
    start = lines.index("hooks:")
    block = lines[start : start + 7]

    assert block[1] == "  PreToolUse:"
    assert block[2] == '    - matcher: "Bash"'
    assert block[3] == "      hooks:"
    assert block[4] == "        - type: command"
    assert block[5].startswith("          command: ")
    assert block[6] == "          timeout: 10"


@pytest.mark.parametrize("script", [GUARD, STAGE_CHECK])
def test_the_scripts_are_executable_shell(script) -> None:
    assert script.is_file(), script
    assert script.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")


def test_the_skill_names_the_design_branch_prefix() -> None:
    """設計 Pull Request の見分けは head のブランチ名で行う（決定 4）。規約を本文へ書く。"""
    body = SKILL.read_text(encoding="utf-8")
    section = body.split("**設計レビューは")[1].split("\n## ")[0]

    assert "design/" in section


def test_the_reference_is_linked_from_the_skill() -> None:
    assert "references/stage-completeness.md" in SKILL.read_text(encoding="utf-8")
    assert COMPLETENESS.is_file()


def test_the_scripts_lookup_section_is_unchanged() -> None:
    """#221-6: 進行の記録を書く手順は変わらない。"""
    body = TRACKING.read_text(encoding="utf-8")

    assert "### `$SCRIPTS` を決める" in body
    assert 'bash "$SCRIPTS/projects-sync.sh" <issue番号> <キー> "<値>"' in body


def test_the_merged_report_carries_the_stage_report() -> None:
    """#221-2: 後片付けの完了報告に、スクリプトの出力として載る。"""
    body = MERGED.read_text(encoding="utf-8")

    assert "stage-check.sh" in body
    assert "report" in body


def test_the_merged_skill_closes_issues_with_their_repository() -> None:
    """#229-2: 取り出した 2 つの値を `--repo` へ渡す書き方であること。"""
    body = MERGED.read_text(encoding="utf-8")

    assert "gh issue close <番号> --repo <所有者>/<リポジトリ>" in body
