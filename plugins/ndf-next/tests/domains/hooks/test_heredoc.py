"""ヒアドキュメントの guard（#3699）: 区切りを引用符で囲まない本文のコマンド置換だけを止める。"""

from __future__ import annotations

import pytest

from ndf.domains.hooks import metrics

from ._hooks import bash, call_entry, deny_reason, records

BACKTICK = "python3 - <<EOF\nprint('`issues/a.md` を読む')\nEOF"
SUBST = "cat > note.md <<EOF\n今日は $(date) に書いた\nEOF"


@pytest.mark.parametrize(
    ("command", "denied"),
    [
        (BACKTICK, True),
        (SUBST, True),
        ("python3 - <<'EOF'\nprint('`issues/a.md` と $(date)')\nEOF", False),
        ('cat <<-"END"\n\t`x` と $(y)\n\tEND', False),
        ("cat <<EOF\n$HOME と ${NAME}\nEOF", False),
        ("echo `date` $(pwd)", False),
    ],
    ids=["unquoted-backtick", "unquoted-subst", "single-quoted", "dash-double-quoted", "variables-only", "no-heredoc"],
)
def test_heredoc_guard(tmp_path, command, denied):
    reason = deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, command)))
    assert (reason is not None) is denied
    if denied:
        assert "<<'EOF'" in reason and "環境変数" in reason


def test_substitution_after_the_delimiter_line_is_not_the_body(tmp_path):
    command = "cat <<EOF > a.txt\nplain\nEOF\necho $(pwd)"
    assert call_entry("claude", "PreToolUse", bash(tmp_path, command)) is None


@pytest.mark.parametrize(
    ("command", "denied"),
    [
        # 引用符で囲んだ本文の中の `<<EOF` は開きではない（ヒアドキュメントを説明する文書）
        ("cat > doc.md <<'MD'\n例: `cat <<EOF`\n```\ncat <<EOF\n$(date)\nEOF\n```\nMD", False),
        # 本文を読み飛ばした後の開きは探す
        ("cat > a.md <<'MD'\n`x`\nMD\ncat <<EOF\n$(date)\nEOF", True),
        # 1 行に並んだ開きの本文は順に続く
        ("cat <<'A' <<B\n`x`\nA\n$(y)\nB", True),
        ("cat <<A <<'B'\nplain\nA\n$(y)\nB", False),
    ],
    ids=["nested-in-quoted", "after-quoted", "second-unquoted", "second-quoted"],
)
def test_heredoc_bodies_are_skipped_before_looking_for_the_next_opening(tmp_path, command, denied):
    reason = deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, command)))
    assert (reason is not None) is denied


def test_refusals_are_recorded_as_deny(tmp_path):
    for command in (BACKTICK, SUBST):
        assert deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, command))) is not None
    assert metrics.stats(records())["guards"]["heredoc"]["denies"] == 2
