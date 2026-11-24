from __future__ import annotations

import ast

import pytest

from ndf.checks import copy_dup

CONF = {"min_files": 4, "min_chars": 8, "max_chars": 80, "allow_names": ["check", "main"]}


def trees(*texts: str) -> dict[str, ast.Module]:
    return {f"src/{name}.py": ast.parse(text) for name, text in zip("abcdef", texts)}


def found(findings):
    return [(f.rule, f.path, f.line) for f in findings]


def test_the_same_function_name_in_two_files_is_one_finding():
    given = trees("x = 1\n", "x = 1\n\ndef _load(): ...\n", "async def _load(): ...\n")
    assert found(copy_dup.top_functions(given, CONF)) == [("copy-dup", "src/b.py", 3)]
    assert found(copy_dup.top_functions(dict(reversed(given.items())), CONF)) == [("copy-dup", "src/b.py", 3)]


@pytest.mark.parametrize(
    "texts",
    [
        ("def f():\n    def g(): ...\ndef h():\n    def g(): ...\n",),  # 同じファイルの中の入れ子
        ("class A:\n    def f(self): ...\n", "class B:\n    def f(self): ...\n"),  # 最上位でない
        ("def check(): ...\ndef main(): ...\n", "def check(): ...\ndef main(): ...\n"),  # 同名を認める名前
        ("def f(): ...\ndef f(): ...\n", "def g(): ...\n"),  # 1 つのファイルにしか無い
        (),
    ],
)
def test_what_is_not_the_same_top_level_function(texts):
    assert copy_dup.top_functions(trees(*texts), CONF) == []


@pytest.mark.parametrize(("files", "chars", "count"), [(4, 20, 1), (3, 20, 0), (4, 7, 0), (4, 8, 1), (4, 80, 1), (4, 81, 0)])
def test_a_string_counts_by_its_files_and_length(files, chars, count):
    text = f"x = 1\ny = [{'s' * chars!r}, {'s' * chars!r}]\n"
    expected = [("copy-dup", "src/a.py", 2)] * count
    assert found(copy_dup.repeated_strings(trees(*[text] * files), CONF)) == expected


def test_docstrings_are_not_counted():
    doc = '"""同じ説明の文章です"""\n'
    text = f"{doc}class A:\n    {doc}    def f(self):\n        {doc}async def g():\n    {doc}"
    assert copy_dup.repeated_strings(trees(*[text] * 4), CONF) == []


def test_a_string_equal_to_a_docstring_is_counted():
    text = '"""同じ説明の文章です"""\nx = "同じ説明の文章です"\n'
    assert found(copy_dup.repeated_strings(trees(*[text] * 4), CONF)) == [("copy-dup", "src/a.py", 2)]


def test_the_rule_reads_only_the_product_code(plugin, rules):
    plugin.write("src/ndf/a.py", "def twice(): ...\n")
    plugin.write("tests/test_a.py", "def twice(): ...\n")
    assert copy_dup.check(plugin.scope(), rules) == []
    plugin.write("src/ndf/b.py", "def twice(): ...\n")
    for name in "cdef":
        plugin.write(f"src/ndf/{name}.py", "x = 'a repeated string'\n")
    rules["copy_dup"] = {**CONF, "min_files": 4}
    assert [f.path.rsplit("/", 1)[-1] for f in copy_dup.check(plugin.scope(), rules)] == ["a.py", "c.py"]
