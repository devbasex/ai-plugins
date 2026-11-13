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
