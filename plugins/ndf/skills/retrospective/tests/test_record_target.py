"""振り返りの記録先がコメント 1 件であることを固定する（#242 / #229）。

記録のためにブランチと Pull Request を作る費用が、記録の価値を上回っていた。実測では
7 本の Pull Request がこの用途だけで作られている。記録の本体はコメント 1 件へ移し、
辿る経路は対象の issue の本文末尾の 1 行で作る。

起票先の判断表はこの Skill には無い。持つのは `out-of-scope` だけで、ここは参照だけを持つ。
"""
from __future__ import annotations

import pytest

from retrospective_helpers import (
    CHANGE_TABLE_HEADING,
    DECISION_TABLE_HEADING,
    ISSUE_TARGET,
    POST_TARGET_HEADING,
    RECORD_REPO,
    SKILL,
    command_lines,
    fenced_blocks,
    gh_commands,
    line_after_table,
    link_targets,
    read,
    table,
)

# 記録に残す 3 つの節。雛形から消えると、何を書くかが手順から読めなくなる。
TEMPLATE_HEADINGS = ("## 何が起きたか", "## 次に変えること", "## 途中で起票した課題")

# 投稿先を決める 3 つの状況。**起点の issue を持たない変更も網羅する。**
# 置き場所が無いことを理由に工程を飛ばす経路を残さない。
POST_TARGET_CASES = ("1 件の issue", "複数の issue", "起点の issue を持たない")

# 記録に触る `gh` の下位コマンド。この工程は `merged` の後に来るため、作業ツリーが消えて
# いる。`--repo` を省くと、いま作業しているディレクトリのリポジトリへ向かう。
RECORD_COMMANDS = (
    "gh issue list",
    "gh issue comment",
    "gh pr comment",
    "gh issue view",
    "gh issue edit",
)


def test_the_record_is_not_a_file_under_development_history() -> None:
    """記録先としてのディレクトリを求めない。"""
    assert "docs/development-history/" not in read(SKILL)


def test_the_post_target_table_covers_three_cases() -> None:
    """投稿先の表が 3 つの状況を網羅する。"""
    _, rows = table(read(SKILL), POST_TARGET_HEADING)
    assert len(rows) == 3, f"投稿先の行数が 3 ではない: {rows}"
    starts = [row[0] for row in rows]
    for case in POST_TARGET_CASES:
        assert any(case in start for start in starts), f"網羅されていない状況: {case} / {starts}"


def test_the_posting_commands_are_written_out() -> None:
    """投稿の手順が、実行できるコマンドとして載っている。"""
    body = read(SKILL)
    for command in ("gh issue comment", "gh pr comment"):
        assert command in body, f"投稿の呼び出しが無い: {command}"


def test_the_record_repository_is_resolved_once() -> None:
    """記録の投稿先のリポジトリを、1 か所で決める。

    決める場所が 2 つあると、片方だけを直したときに投稿と追記が別のリポジトリへ向かう。
    """
    definitions = [
        line.strip()
        for line in command_lines(read(SKILL))
        if line.strip().startswith(f"{RECORD_REPO}=")
    ]
    assert len(definitions) == 1, f"投稿先の変数の定義が 1 か所ではない: {definitions}"
    assert "gh repo view" in definitions[0], f"変更を行ったリポジトリから引いていない: {definitions[0]}"


def test_every_record_call_passes_the_record_repository() -> None:
    """記録に触る `gh` の呼び出しは、すべて投稿先のリポジトリを受け取る。

    起票先（`out-of-scope`）とは別のものである。起票先は課題の性質で配布元になることが
    あるが、記録は起点の issue と配布した Pull Request がある場所に残る。
    """
    commands = [c for c in gh_commands(read(SKILL)) if c.startswith(RECORD_COMMANDS)]
    assert commands, "記録に触る `gh` の呼び出しを読み取れない"
    for command in RECORD_COMMANDS:
        assert any(c.startswith(command) for c in commands), f"呼び出しが無い: {command}"
    missing = [c for c in commands if f'--repo "${RECORD_REPO}"' not in c]
    assert not missing, f"投稿先が渡されていない: {missing}"


def test_the_pull_request_lookup_targets_the_record_repository() -> None:
    """番号を引く `gh api` も、作業ディレクトリではなく投稿先のリポジトリを見る。"""
    lines = [line for line in command_lines(read(SKILL)) if "commits/" in line and "/pulls" in line]
    assert lines, "コミットから Pull Request を引く呼び出しが無い"
    missing = [line for line in lines if f"${RECORD_REPO}" not in line]
    assert not missing, f"投稿先のリポジトリを見ていない: {missing}"


def test_the_back_reference_line_has_a_fixed_form() -> None:
    """本文末尾へ足す 1 行の書式が載っている。"""
    lines = [line.strip() for line in read(SKILL).splitlines()]
    assert any(line.startswith("振り返り: ") for line in lines), "辿る経路の 1 行の書式が無い"


def test_the_template_keeps_its_three_sections() -> None:
    """雛形に 3 つの節が残る。"""
    blocks = fenced_blocks(read(SKILL))
    assert any(
        all(heading in block for heading in TEMPLATE_HEADINGS) for block in blocks
    ), "雛形のコードブロックが見つからない"


def test_the_pull_request_number_comes_from_the_merge_commit() -> None:
    """Pull Request の番号を、消えたブランチではなくコミットから引く。"""
    lines = read(SKILL).splitlines()
    found = [i for i, line in enumerate(lines) if "commits/" in line and "/pulls" in line]
    assert found, "コミットから Pull Request を引く呼び出しが無い"
    near = "\n".join(lines[found[0] : found[0] + 6])
    assert "merged_at" in near, f"マージ済みへ絞っていない: {near}"


def test_the_decision_table_lives_only_in_out_of_scope() -> None:
    """起票先の分類は、この Skill には書き写されていない。"""
    _, rows = table(read(ISSUE_TARGET), DECISION_TABLE_HEADING)
    body = read(SKILL)
    for row in rows:
        assert row[0] not in body, f"判断表の分類が書き写されている: {row[0]}"


def test_the_change_table_is_followed_by_the_reference() -> None:
    """落とし先の表の直後に、判断表への参照が 1 行ある。"""
    line = line_after_table(read(SKILL), CHANGE_TABLE_HEADING)
    targets = link_targets(line)
    assert targets, f"参照のリンクが無い: {line}"
    resolved = [(SKILL.parent / target).resolve() for target in targets]
    assert ISSUE_TARGET.resolve() in resolved, f"判断表を指していない: {targets}"


@pytest.mark.parametrize("heading", [POST_TARGET_HEADING, CHANGE_TABLE_HEADING])
def test_an_unreadable_table_fails(heading: str) -> None:
    """表を読み取れないことは、素通りではなく失敗になる。"""
    with pytest.raises(AssertionError):
        table("見出しの無い本文", heading)
