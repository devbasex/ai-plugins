"""project.yml の行単位の読み書き（AC6）。3 つのキー以外は 1 バイトも変えない。"""
import difflib
from pathlib import Path

import pytest

import serena_lsp_testlib  # noqa: F401  （import の経路を足す）
from serena_lsp import project_yml as py

TEMPLATE = (Path(__file__).parent / "fixtures/serena-1.7.0-project.yml").read_text()
KEYS = ("language_servers", "ignored_paths", "mcp_serena_excluded")


def _changed_lines(before: str, after: str) -> list:
    return [line for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
            if line[:1] in "+-" and not line.startswith(("+++", "---"))]


def _only_keys_changed(before, after):
    """変わった行が 3 つのキーのブロックの中にしかないことを照らす。"""
    kept_before = py.strip_blocks(before, KEYS)
    kept_after = py.strip_blocks(after, KEYS)
    assert kept_before == kept_after


def test_read_template():
    assert py.read_list(TEMPLATE, "language_servers") == ["python"]
    assert py.read_list(TEMPLATE, "ignored_paths") == []
    assert py.read_list(TEMPLATE, "mcp_serena_excluded") is None


def test_write_template_changes_only_three_keys():
    out = py.write_list(TEMPLATE, "language_servers", ["python", "bash"])
    out = py.write_list(out, "ignored_paths", [".worktrees/**"])
    out = py.write_list(out, "mcp_serena_excluded", ["typescript health_check_exit_1"])
    assert py.read_list(out, "language_servers") == ["python", "bash"]
    assert py.read_list(out, "ignored_paths") == [".worktrees/**"]
    assert py.read_list(out, "mcp_serena_excluded") == ["typescript health_check_exit_1"]
    _only_keys_changed(TEMPLATE, out)
    # 注釈はすべて残る
    assert [l for l in TEMPLATE.splitlines() if l.startswith("#")] == \
        [l for l in out.splitlines() if l.startswith("#")]


def test_write_is_idempotent_and_empty_list_is_flow():
    once = py.write_list(TEMPLATE, "language_servers", [])
    assert "language_servers: []" in once.splitlines()
    assert py.read_list(once, "language_servers") == []
    assert py.write_list(once, "language_servers", []) == once
    back = py.write_list(once, "language_servers", ["python"])
    assert back == TEMPLATE


def test_commented_and_quoted_items_are_read():
    text = "# head\nlanguage_servers: # 注釈\n- \"python\"\n  # 途中の注釈\n- 'bash'\n# 次のキーの注釈\nencoding: utf-8\n"
    assert py.read_list(text, "language_servers") == ["python", "bash"]
    out = py.write_list(text, "language_servers", ["php"])
    assert out == "# head\nlanguage_servers:\n- php\n# 次のキーの注釈\nencoding: utf-8\n"


def test_indented_block_items_are_read():
    text = "ignored_paths:\n  - a/**\n  - b\nread_only: false\n"
    assert py.read_list(text, "ignored_paths") == ["a/**", "b"]


@pytest.mark.parametrize("text", [
    "language_servers: [python, bash]\n",
    "language_servers: &ls\n- python\n",
    "language_servers:\n- *ls\n",
    "language_servers: python\n",
    "language_servers:\n- python\n- {a: 1}\n",
])
def test_unknown_shapes_are_refused(text):
    with pytest.raises(py.UnsupportedShape):
        py.read_list(text, "language_servers")
    with pytest.raises(py.UnsupportedShape):
        py.write_list(text, "language_servers", ["python"])


def test_missing_key_is_appended():
    text = "encoding: utf-8\n"
    out = py.write_list(text, "mcp_serena_excluded", [])
    assert out == "encoding: utf-8\nmcp_serena_excluded: []\n"
    out2 = py.write_list("encoding: utf-8", "mcp_serena_excluded", ["x y"])
    assert out2 == "encoding: utf-8\nmcp_serena_excluded:\n- x y\n"


def test_appending_keeps_the_comments_and_quotes_of_the_existing_items():
    """既存の要素の行は 1 バイトも変えず、足す要素だけをブロックの末尾へ置く（#984）。"""
    text = ("ignored_paths: # 注釈\n- data/**\n- \"build/**\"   # 生成物\n"
            "# 次のキーの注釈\nencoding: utf-8\n")
    out = py.append_list(text, "ignored_paths", [".serena/**"])
    assert out == ("ignored_paths: # 注釈\n- data/**\n- \"build/**\"   # 生成物\n- .serena/**\n"
                   "# 次のキーの注釈\nencoding: utf-8\n")
    assert py.read_list(out, "ignored_paths") == ["data/**", "build/**", ".serena/**"]


def test_appending_follows_the_indent_of_the_existing_items():
    out = py.append_list("ignored_paths:\n  - a/**\nread_only: false\n", "ignored_paths", ["b"])
    assert out == "ignored_paths:\n  - a/**\n  - b\nread_only: false\n"


@pytest.mark.parametrize(("text", "expected"), [
    ("ignored_paths: []\nencoding: utf-8\n", "ignored_paths:\n- b\nencoding: utf-8\n"),
    ("encoding: utf-8\n", "encoding: utf-8\nignored_paths:\n- b\n"),
    ("ignored_paths:\n- b\n", "ignored_paths:\n- b\n"),
])
def test_appending_to_an_empty_or_missing_key(text, expected):
    assert py.append_list(text, "ignored_paths", ["b"]) == expected
