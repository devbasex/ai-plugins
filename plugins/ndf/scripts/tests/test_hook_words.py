"""hook_lib/words.py の command_stream の現状固定テスト（I-007）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_bash")

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
from hook_lib.words import command_stream  # noqa: E402


@pytest.mark.parametrize(
    ("cmd", "words"),
    [
        ('echo hi', ['echo', 'hi']),
        ('echo \'a b\' "c" | grep x 2>&1', ['echo', 'a b', 'c', '', 'grep', 'x', '2>&1']),
        ('a && b; c', ['a', '', 'b', '', 'c']),
        ('a | b > out', ['a', '', 'b', '>out']),
        ('{ a; b; } > out 2>&1', ['a', '', 'b']),
        ('x=$(git rev-parse HEAD) cmd >&2', ['cmd', '>&2', '', 'git', 'rev-parse', 'HEAD']),
        ('echo $(a $(b))', ['echo', '$(a$(b))', '', 'a', '$(b)', '', 'b']),
        ('cat <<EOF\nrm -rf /\nEOF', ['cat']),
        ('# only a comment', []),
        ("grep x <<< 'hello world'", ['grep', 'x', '<<<', 'hello world']),
        ('if true; then echo y; fi 2>/dev/null', ['true', '', 'echo', 'y']),
        ('', []),
    ],
)
def test_command_stream_splits_words_as_today(cmd, words):
    assert command_stream(cmd) == words
