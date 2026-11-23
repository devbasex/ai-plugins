"""単純コマンドの語の読み方（`_words` が返す引用を外した語・連結の語・字面）。

I-001 で語のセクションを拾う繰り返しを 1 つへまとめる前に、今の振る舞いを固定する。
"""

from __future__ import annotations

import pytest

from ndf.domains.hooks import context


def _command_node(text: str):
    stack = [context._parser().parse(text.encode("utf-8")).root_node]
    while stack:
        node = stack.pop()
        if node.type == "command":
            return node
        stack.extend(reversed(node.children))
    raise AssertionError(f"command が無い: {text!r}")


# (シェルの文, 引用を外した語, 連結の語, 字面)
_CASES = [
    pytest.param(
        "echo a 'b c' \"d e\" 42 $X ${Y} $'t\\n' x\"y\"'z'",
        ("echo", "a", "b c", "d e", "42", "$X", "${Y}", "$'t\\n'", "x\"y\"'z'"),
        ("echo", "a", "b c", "d e", "42", "$X", "${Y}", "$'t\\n'", "xyz"),
        ("echo", "a", "'b c'", '"d e"', "42", "$X", "${Y}", "$'t\\n'", "x\"y\"'z'"),
        id="word-kinds",
    ),
    pytest.param(
        "FOO=1 ls -l > out.txt 2>&1",
        ("ls", "-l"),
        ("ls", "-l"),
        ("ls", "-l"),
        id="skips-assignments-and-redirects",
    ),
    pytest.param(
        "env -S'a b' cmd",
        ("env", "-S'a b'", "cmd"),
        ("env", "-Sa b", "cmd"),
        ("env", "-S'a b'", "cmd"),
        id="joined-concatenation",
    ),
    pytest.param(
        '"/usr/bin/echo" hi',
        ("/usr/bin/echo", "hi"),
        ("/usr/bin/echo", "hi"),
        ('"/usr/bin/echo"', "hi"),
        id="quoted-command-name",
    ),
    pytest.param(
        "echo \\$HOME a\\ b",
        ("echo", "\\$HOME", "a\\ b"),
        ("echo", "\\$HOME", "a\\ b"),
        ("echo", "\\$HOME", "a\\ b"),
        id="escapes-kept",
    ),
    pytest.param("cat <<EOF\nx\nEOF", ("cat",), ("cat",), ("cat",), id="heredoc"),
    pytest.param("echo $(date) `pwd`", ("echo",), ("echo",), ("echo",), id="command-substitution-skipped"),
]


@pytest.mark.parametrize(("text", "argv", "joined", "source"), _CASES)
def test_words_of_a_simple_command(text, argv, joined, source):
    node = _command_node(text)
    assert context._words(node) == (argv, joined, source)


def test_parse_shell_carries_the_words_into_the_command():
    cmd = context.parse_shell("timeout 590 \"sleep\" '600'").commands[0]
    assert cmd.argv == ("timeout", "590", "sleep", "600")
    assert cmd.source == ("timeout", "590", '"sleep"', "'600'")
    assert cmd.body_argv == ("sleep", "600")
