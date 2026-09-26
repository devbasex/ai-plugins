"""パスのパターン照合の包み（lib/pathmatch.py・#1142 の決定 19）。uv の環境の外では test_wrappers_uv_env.py が流し直す。

今の `glossary.declared_path_matches` と `collect._matches_file_pattern`（fnmatch）から変わる入力を固定する:
`*` は `/` をまたがない（`*.md` は `docs/a.md` に当たらない）。
"""
from __future__ import annotations

import fnmatch
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
pytest.importorskip("pathspec")
import pathmatch  # noqa: E402


@pytest.mark.parametrize("pattern,path,hit", [
    ("*.md", "README.md", True),
    ("docs/*.md", "docs/a.md", True),
    ("docs/*.md", "docs/x/a.md", False),
    ("docs/**", "docs/x/a.md", True),
    ("docs/**", "docs", False),
    ("**/*.md", "README.md", True),
    ("**/*.md", "docs/x/a.md", True),
    ("docs/", "docs/a.md", True),
    ("docs/", "docsx/a.md", False),
    ("plugins/ndf/scripts/lib/?.py", "plugins/ndf/scripts/lib/a.py", True),
    ("./README.md", "README.md", True),
])
def test_patterns_match_the_whole_path_from_the_root(pattern, path, hit):
    assert pathmatch.path_matches(path, [pattern]) is hit


def test_star_no_longer_crosses_a_slash():
    assert fnmatch.fnmatchcase("docs/a.md", "*.md")  # 今の照合（fnmatch）は当たる
    assert not pathmatch.path_matches("docs/a.md", ["*.md"])
    assert pathmatch.path_matches("./docs/a.md", ["docs/*.md"])


def test_negation_comments_and_filter():
    pats = ["docs/**", "!docs/private/**", "# 注記", ""]
    assert pathmatch.filter_paths(["docs/a.md", "docs/private/b.md", "src/c.py"], pats) == ["docs/a.md"]
    assert not pathmatch.path_matches("a.md", [])
