"""unified diff の解釈とコメントの除去の包み（lib/textparse.py・#1142 の決定 19）。外部パッケージは全体テストの環境（根の pyproject.toml）が入れる。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import unidiff  # noqa: E402,F401  包みの外部パッケージ。無ければ集めるところで落とす
import pygments  # noqa: E402,F401  包みの外部パッケージ。無ければ集めるところで落とす
import textparse  # noqa: E402

DIFF = """diff --git a/a.md b/a.md
index 1..2 100644
--- a/a.md
+++ b/a.md
@@ -1,0 +2,2 @@
+two
+three
@@ -9 +11 @@
-old
+new
diff --git a/gone.md b/gone.md
deleted file mode 100644
--- a/gone.md
+++ /dev/null
@@ -1 +0,0 @@
-x
diff --git a/new.md b/new.md
new file mode 100644
--- /dev/null
+++ b/new.md
@@ -0,0 +1 @@
+first
diff --git a/only-removed.md b/only-removed.md
--- a/only-removed.md
+++ b/only-removed.md
@@ -3 +2,0 @@
-y
"""


def test_added_lines_per_file_on_the_new_side():
    assert textparse.diff_added_lines(DIFF) == {"a.md": {2, 3, 11}, "new.md": {1}}
    assert textparse.diff_added_lines("") == {}


def test_unreadable_diff_is_an_error():
    with pytest.raises(textparse.DiffParseError):
        textparse.diff_added_lines("--- a/x\n+++ b/x\n@@ -1,3 +1,3 @@\n+only one\n")


def test_comments_become_spaces_and_strings_stay():
    py = ['x = "#a"  # c', 's = """', "# docstring", '"""']
    assert textparse.blank_comments(py, ".py") == ['x = "#a"     ', 's = """', "# docstring", '"""']
    assert textparse.blank_comments(["#!/bin/sh", 'echo "#x" # c', "a#b"], ".sh") == ["         ", 'echo "#x"    ', "a#b"]
    js = ["a = '//s' // c /* d", "/* b", "c */ x", "`#{t}`"]
    assert textparse.blank_comments(js, ".ts") == ["a = '//s'          ", "    ", "     x", "`#{t}`"]
    assert textparse.blank_comments(["# keep"], ".unknownext") == ["# keep"]
    assert textparse.blank_comments([], ".py") == []
