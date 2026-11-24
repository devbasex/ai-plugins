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


def test_a_finding_names_its_first_file_and_lists_every_file_in_text_order():
    """代表と並びはパスの文字列の順（`.` は `/` より前）で、行は代表のファイルの最初の行。"""
    given = {
        "src/b.py": ast.parse("def _load(): ...\n"),
        "src/a/z.py": ast.parse("def _load(): ...\n"),
        "src/a.py": ast.parse("x = 1\ny = 2\ndef _load(): ...\ndef _load(): ...\n"),
    }
    assert [(f.path, f.line, f.detail) for f in copy_dup.top_functions(given, CONF)] == [
        ("src/a.py", 3, "同じ名前の関数 '_load' が 3 ファイルにある: src/a.py, src/a/z.py, src/b.py")
    ]


def test_findings_keep_the_order_of_first_appearance():
    """指摘の順は代表のパスでなく、名前か文字列が最初に出た順。数に満たないものは並べない。"""
    names = trees("def f(): ...\ndef h(): ...\n", "def z(): ...\ndef g(): ...\ndef f(): ...\n", "def g(): ...\ndef z(): ...\n")
    backward = dict(reversed(names.items()))
    assert [f.detail for f in copy_dup.top_functions(backward, CONF)] == [
        "同じ名前の関数 'g' が 2 ファイルにある: src/b.py, src/c.py",
        "同じ名前の関数 'z' が 2 ファイルにある: src/b.py, src/c.py",
        "同じ名前の関数 'f' が 2 ファイルにある: src/a.py, src/b.py",
    ]
    strings = trees(*["x = 'the second'\n"] * 2, *["y = 'it\\'s the first'\nx = 'the second'\n"] * 4)
    assert [(f.path, f.line, f.detail) for f in copy_dup.repeated_strings(dict(reversed(strings.items())), CONF)] == [
        ("src/c.py", 1, '同じ文字列 "it\'s the first" が 4 ファイルにある: src/c.py, src/d.py, src/e.py, src/f.py'),
        ("src/a.py", 1, f"同じ文字列 'the second' が 6 ファイルにある: {', '.join(strings)}"),
    ]


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
