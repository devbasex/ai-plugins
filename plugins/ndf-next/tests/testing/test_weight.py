from __future__ import annotations

import pytest

from ndf.testing import weight

PYTEST = {"name": "pytest", "runner": "pytest", "command": "uv run --frozen --project . pytest . -q -n 4"}
VITEST = {"name": "factory", "runner": "vitest", "command": "npm --prefix factory run check"}
LINT = {"name": "lint", "runner": "ruff", "kind": "lint", "command": "ruff check ."}
SUITES = [PYTEST, VITEST, LINT]


@pytest.mark.parametrize(
    ("command", "kind"),
    [
        ("uv run --frozen --project . pytest . -q -n 4", "whole"),  # 宣言の command と一致
        ("cd sub && npm --prefix factory run check", "whole"),  # 最後の単純コマンドが一致
        ("python3 -m pytest a.py -n 3", "scope"),  # 並列の指定
        ("timeout 600 pytest tests/x.py --numprocesses=2 2>&1 | tail", "scope"),
        ("uv run pytest -q", "whole"),  # 対象が無い
        ("/usr/bin/env FOO=1 pytest .", "whole"),
        ("npx vitest", "whole"),
        ("pytest tests/test_a.py -q", None),  # 並列の指定の無い 1 ファイル
        ("grep pytest .", None),  # コマンドの位置に無い
        ("echo pytest -n 4", None),
        ("ruff check .", None),  # lint は数えない
        ("pytest 'unbalanced", None),  # 読めない文字列は重くない
    ],
)
def test_classify(command, kind):
    assert weight.classify(command, SUITES) == kind


def test_word_list_is_read_as_one_command():
    assert weight.is_heavy(["python", "-m", "pytest", "-n", "2", "a.py"], SUITES)
    assert not weight.is_heavy(["pytest", "a.py"], SUITES)


def test_no_declaration_means_not_heavy():
    assert not weight.is_heavy("pytest . -n 8", [])


def test_split_commands_drops_redirects_and_separators():
    assert weight.split_commands("a b > out.txt 2>&1; c && d | e\nf") == [["a", "b"], ["c"], ["d"], ["e"], ["f"]]


# --- 現状固定テスト（split_commands: リダイレクト・記述子の番号・区切り・引用・読めない文字列） ---


@pytest.mark.parametrize(
    ("text", "commands"),
    [
        ("", []),
        ("a>b", [["a"]]),  # 空白の無いリダイレクトも先の語ごと読み飛ばす
        ("a>", [["a"]]),
        ("a 2>err.txt b", [["a", "b"]]),
        ("a 2 > f", [["a"]]),  # 離れた番号も、次がリダイレクトなら記述子として読み飛ばす
        ("a 1>&2 b", [["a", "b"]]),
        ("a &> log b", [["a", "b"]]),
        ("a >| f b", [["a", "b"]]),
        ("a >>f b", [["a", "b"]]),
        ("a <> f b", [["a", "b"]]),
        ("a <<< 'x' b", [["a", "b"]]),
        ("a & b", [["a"], ["b"]]),
        ("a |& b", [["a"], ["b"]]),
        ("x;;y", [["x"], ["y"]]),
        ("(a; b)", [["a"], ["b"]]),
        ("a\n\nb", [["a"], ["b"]]),  # 空のコマンドは残さない
        ("a ';' b", [["a"], ["b"]]),  # 引用した区切りも区切りとして読む（今の振る舞い）
        ("a 'b c' \"d\"", [["a", "b c", "d"]]),
        ("a\tb\r c", [["a", "b", "c"]]),
        ("FOO=1 a", [["FOO=1", "a"]]),
        ("a # c", [["a"]]),  # 注釈は読まない
        ("echo 'x", []),  # 閉じない引用は読めない
    ],
)
def test_split_commands_current_behavior(text, commands):
    assert weight.split_commands(text) == commands
