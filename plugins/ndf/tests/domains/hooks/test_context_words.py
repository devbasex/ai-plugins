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
    pytest.param(
        "rm $W/$n.md.done x",
        ("rm", "$W/$n.md.done", "x"),
        ("rm", "$W/$n.md.done", "x"),
        ("rm", "$W/$n.md.done", "x"),
        id="adjacent-sections-are-one-word",
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


def test_body_source_lines_up_with_the_words_after_the_wrapper():
    cmd = context.parse_shell("timeout 590 \"sleep\" '600'").commands[0]
    assert cmd.body_source == ('"sleep"', "'600'")
    split = context.parse_shell("env -S'rm -f' \"$W\"").commands[0]
    assert split.body_argv == ("rm", "-f", "$W")
    # `env -S` で分けた語は、文字のままの部分を単引用で囲んだ字面になる
    assert split.body_source == ("'rm'", "'-f'", '"$W"')
    plain = context.parse_shell('rm -f "$W"').commands[0]
    assert plain.body_source is plain.source


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("rm \\${D}/c", ("'rm'", "'${D}/c'")),
        ("${E}x", ("${E}'x'",)),
        ('""', ("''",)),
        ("it\\'s", ("'it'\\''s'",)),
    ],
)
def test_split_env_string_keeps_which_parts_env_expands(text, source):
    assert tuple(word.source for word in context._split_env_string(text)) == source


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ('"rm -f \\"\\$W/x\\""', 'rm -f "$W/x"'),
        ("'a\\b'", "a\\b"),
        ("a\\ b", "a b"),
        ('"a\\b"', "a\\b"),
        ("$'it\\'s'", "it's"),
        ('"$W"', "$W"),
        ("$'\\x72m'", "rm"),
        ("$'\\162m'", "rm"),
        ("$'\\u0072m'", "rm"),
        ("$'\\xZ'", "\\xZ"),
        ("$'\\cA'", "\x01"),
        ("$'a\\0b'c", "ac"),
        ("$'\\uD800'", "\ufffd"),
        ("$'\\UFFFFFFFF'", "\ufffd"),
    ],
)
def test_shell_value_undoes_the_outer_quotes_and_escapes(text, value):
    assert context.shell_value(text) == value
