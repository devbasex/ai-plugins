"""ラッパーを外した後の語の位置と、シェルの `-c` の語の位置の読み方（`only_commands` と `waits`）。

`_is_steady`・`_fixed_inner`・`_inner_scripts` が、同じ語の位置を読むことを確かめる。
"""

from __future__ import annotations

import pytest

from ndf.domains.hooks import context


def _only(text: str) -> tuple[bool, list[tuple[str, ...]]]:
    """`only_commands` の値と、`accept` に渡ったコマンドの `body_argv`。`accept` は `rm` だけを断る。"""
    seen: list[tuple[str, ...]] = []

    def accept(cmd: context.ShellCommand) -> bool:
        seen.append(cmd.body_argv)
        return cmd.body_name != "rm"

    return context.only_commands(text, accept), seen


def _inner(text: str) -> list[tuple[str, ...]]:
    """`waits` のうち、中の文から読んだコマンドの語。"""
    script = context.parse_shell(text)
    return [cmd.argv for cmd in script.waits[len(script.commands) :]]


@pytest.mark.parametrize("wrapper", ["", "nohup ", "timeout 5 ", "timeout -k 5 600 nice -n 10 "])
@pytest.mark.parametrize(
    "text",
    [
        "bash -c 'w p'",
        "/bin/sh -ec 'w p'",
        "dash -e -u -c 'w p' name",
        "bash --norc --noprofile -xc 'w p'",
        "bash '-c' 'w p'",
        # 読むのは先頭の `-c` だけで、中の文より後ろの語は展開があってもよい
        "bash -c 'w p' -c \"rm x\"",
        "bash -c 'w p' \"$X\" *",
        "eval 'w p'",
        'eval w "p"',
    ],
)
def test_a_fixed_script_is_read_whatever_the_number_of_wrapper_words(wrapper, text):
    assert _only(wrapper + text) == (True, [("w", "p")])
    assert _inner(wrapper + text) == [("w", "p")]


@pytest.mark.parametrize("wrapper", ["", "nohup ", "timeout 5 ", "timeout -k 5 600 nice -n 10 "])
@pytest.mark.parametrize(
    ("text", "inner"),
    [
        # 中の文か、`-c` までの語が展開で決まる
        ('bash -c "w $P"', [("w", "$P")]),
        ("bash -c w\\ *", [("w", "*")]),
        ("bash -e -c $P", [("$P",)]),
        ("bash $O -c 'w p'", [("w", "p")]),
        ("bash -c{e,u} 'w p'", [("w", "p")]),
        ('eval w "$P"', [("w", "$P")]),
        ("eval 'w p' $P", [("w", "p", "$P")]),
        # `-c` までに、起動ファイルを読まない 1 語のオプションでない語がある
        ("bash -o pipefail -c 'w p'", [("w", "p")]),
        ("bash x.sh -c 'w p'", [("w", "p")]),
        ("bash -l -c 'w p'", [("w", "p")]),
        ("bash -lc 'w p'", [("w", "p")]),
        ("bash -e --login -c 'w p'", [("w", "p")]),
        ("zsh -c 'w p'", [("w", "p")]),
    ],
)
def test_an_unfixed_script_is_read_for_waits_but_not_confirmed(wrapper, text, inner):
    assert _only(wrapper + text) == (False, [])
    assert _inner(wrapper + text) == inner


@pytest.mark.parametrize(
    ("text", "seen"),
    [
        # `-c` の後に中の文が無い・`c` を含まないオプションだけ・`--` で始まる語は、`-c` として読まない
        ("bash -c", ("bash", "-c")),
        ("timeout 5 bash -e -c", ("bash", "-e", "-c")),
        ("bash -e 'w p'", ("bash", "-e", "w p")),
        ("bash --rcfile x", ("bash", "--rcfile", "x")),
        ("eval", ("eval",)),
        ("nohup eval", ("eval",)),
        # ラッパーが無ければ字面のエスケープを解かずに `-c` を探す
        (r"bash \-c 'w p'", ("bash", r"\-c", "w p")),
        # エスケープの付いた先頭の語はラッパーとして外さない（外した語が 0 個）
        (r"\env w", (r"\env", "w")),
        # コマンドを起動しないラッパー・読めないラッパーの引数は、語がすべて外れる
        ("command -v bash", ()),
        ("nohup timeout 5", ()),
        ("timeout --bogus 5 bash -c 'rm x'", ()),
    ],
)
def test_a_command_without_an_inner_script_is_passed_to_accept_itself(text, seen):
    assert _only(text) == (True, [seen])
    assert _inner(text) == []


def test_an_escaped_c_option_is_read_after_a_wrapper():
    # ラッパーの後ろの語は、外側のシェルがエスケープを解いた値で読む
    text = r"timeout 5 bash \-c 'w p'"
    assert _only(text) == (True, [("w", "p")])
    assert _inner(text) == [("w", "p")]


def test_the_word_after_the_first_c_option_is_the_script():
    # 2 つ目の `-c` は中の文として読む
    assert _only("bash -c -c 'rm x'") == (True, [("-c",)])
    assert _inner("bash -c -c 'rm x'") == [("-c",)]


@pytest.mark.parametrize(
    "text",
    [
        # 外したラッパーの語か、その次のコマンド名が展開で決まる
        "timeout $D bash -c 'w p'",
        "timeout -k $K 5 w p",
        "timeout 5 $X -c 'w p'",
        "timeout 5 ba* -c 'w p'",
        "command -v $X",
        # 外したラッパーの語に、動かすものを変えるラッパーがある
        "env bash -c 'w p'",
        "timeout 5 env w p",
        "exec eval 'w p'",
        "timeout 5 /usr/bin/xargs w",
        # `env -S` で分けた語の数が、元の語より少ない・同じ・多い
        "env -S 'w'",
        "env -S 'w p'",
        "env -S 'w a b'",
        "env -S 'bash -c' 'w p'",
        "env -S 'w a b c d e f'",
    ],
)
def test_a_command_that_is_not_steady_is_never_passed_to_accept(text):
    assert _only(text) == (False, [])


@pytest.mark.parametrize("text", ["timeout 5 w $P", "w env $P", "w *", "nohup w exec xargs"])
def test_only_the_wrapper_words_and_the_command_name_must_be_steady(text):
    assert _only(text)[0] is True


@pytest.mark.parametrize("wrapper", ["", "timeout 5 "])
@pytest.mark.parametrize("text", ["bash -c 'w p; rm x'", "bash -c 'w p' && rm x", "eval 'w p;' rm x"])
def test_a_command_refused_by_accept_makes_the_text_refused(wrapper, text):
    assert _only(wrapper + text)[0] is False
