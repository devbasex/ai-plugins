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

# プラグインの根。`${CLAUDE_PLUGIN_ROOT}` が指す先で、Skill の実体はこの下の
# `skills/<名前>/` にある。
PLUGIN_ROOT = SKILL_DIR.parents[1]

# **Skill の hook のコマンドで置き換わる変数はこれだけである。** 実行ファイルは、
# 一覧の外の変数を見つけると次の文言で拒む（Claude Code 2.1.259 の実測）。
#
#     Hook command references ${...} but only ${CLAUDE_PLUGIN_ROOT} is available for
#     skill hooks (${CLAUDE_PLUGIN_DATA} is plugin-only).
#
# `${CLAUDE_PROJECT_DIR}` は hook の環境変数として渡るため、シェル形式でも展開される。
# `${CLAUDE_SKILL_DIR}` は SKILL.md の本文では使えるが、**hook のコマンドでは渡らない**。
# 空へ展開されるだけで拒否も警告も出ないため、発火しないことに気づく手がかりが無い（#304）。
ALLOWED_HOOK_VARIABLES = frozenset({"CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR"})

# 実行ファイルが変数を取り出す正規表現と同じもの。
VARIABLE = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_.]*)\}")


def frontmatter() -> str:
    body = SKILL.read_text(encoding="utf-8")
    found = re.match(r"\A---\s*\n(.*?)\n---\s*\n", body, re.DOTALL)
    assert found, "frontmatter を読み取れない"
    return found.group(1)


def hook_command() -> str:
    """frontmatter が登録する hook のコマンドを返す。

    読み取れないこと自体を失敗として扱う。行が消えるだけで、コマンドを見る検査が
    素通りになる形にしない。
    """
    found = re.search(r"^\s*command:\s*\"(.+)\"\s*$", frontmatter(), re.MULTILINE)
    assert found, f"hook のコマンドを読み取れない: {frontmatter()}"
    return found.group(1)


def test_the_frontmatter_registers_a_pre_tool_use_hook() -> None:
    body = frontmatter()

    assert re.search(r"^hooks:\s*$", body, re.MULTILINE), body
    assert re.search(r"^  PreToolUse:\s*$", body, re.MULTILINE), body
    assert re.search(r'^    - matcher: "Bash"\s*$', body, re.MULTILINE), body


def test_the_hook_points_at_the_guard_in_this_skill() -> None:
    """`${CLAUDE_PLUGIN_ROOT}` はプラグインの根を指す。作業ディレクトリに依存しない。"""
    body = frontmatter()

    assert "${CLAUDE_PLUGIN_ROOT}/skills/development-workflow/scripts/workflow-guard.sh" in body


def test_the_hook_command_only_uses_variables_available_to_skill_hooks() -> None:
    """一覧の外の変数は使わない。**空へ展開されるだけで、拒否も警告も出ない**（#304）。

    文字列の一致だけを見ると、同じ間違いが戻ったときに気づけない。`${CLAUDE_SKILL_DIR}`
    は SKILL.md の本文では使えるため、hook へ書いても誤りに見えない。
    """
    used = set(VARIABLE.findall(hook_command()))

    assert used, f"hook のコマンドが変数を持たない: {hook_command()}"
    forbidden = sorted(used - ALLOWED_HOOK_VARIABLES)
    assert not forbidden, (
        f"Skill の hook で置き換わらない変数を使っている: {forbidden}。"
        f"使えるのは {sorted(ALLOWED_HOOK_VARIABLES)} だけである"
    )


def test_the_hook_command_resolves_to_the_guard_under_the_plugin_root() -> None:
    """`${CLAUDE_PLUGIN_ROOT}` を実体の根へ置き換えると、判定のスクリプトへ届く。

    変数の名前だけを見ると、その先の道筋が誤っていても通る。**置き換えた結果を実体と
    突き合わせる。**
    """
    resolved = hook_command().replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN_ROOT))
    path = resolved.split()[-1]

    assert path == str(GUARD), f"判定のスクリプトを指していない: {path}"
    assert GUARD.is_file(), GUARD


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
