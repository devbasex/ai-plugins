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


def test_refusals_are_recorded_as_deny(tmp_path):
    for command in (BACKTICK, SUBST):
        assert deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, command))) is not None
    assert metrics.stats(records())["guards"]["heredoc"]["denies"] == 2
