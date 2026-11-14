"""パスのパターン照合のアダプター（`ndf.adapters.paths`）。"""

from __future__ import annotations

import pytest

from ndf.adapters import paths


@pytest.mark.parametrize(
    ("pattern", "hit", "miss"),
    [
        ("src/ndf/adapters/**", ["src/ndf/adapters/a.py", "src/ndf/adapters/x/b.py"], ["src/ndf/core/a.py"]),
        ("*.md", ["README.md"], ["docs/a.md"]),
        ("docs/*.md", ["docs/a.md"], ["docs/x/a.md"]),
        ("**/.env*", [".env", "a/b/c/.env.local"], ["a/env"]),
        ("docs/", ["docs/a.md", "docs/x/a.md"], ["docsx/a.md"]),
        ("./docs/**", ["./docs/a.md", "docs/a.md"], ["docs"]),
    ],
    ids=["tree", "star-no-slash", "dir-star", "deep-dotenv", "dir-contents", "dot-slash"],
)
def test_path_matches(pattern: str, hit: list[str], miss: list[str]) -> None:
    assert [p for p in hit if not paths.path_matches(p, [pattern])] == []
    assert [p for p in miss if paths.path_matches(p, [pattern])] == []


def test_negation_removes_earlier_match() -> None:
    assert paths.path_matches("docs/a.md", ["docs/", "!docs/x/"])
    assert not paths.path_matches("docs/x/a.md", ["docs/", "!docs/x/"])


def test_no_patterns_match_nothing() -> None:
    assert not paths.path_matches("a.py", [])
    assert not paths.path_matches("a.py", ["", "  "])


def test_matching_keeps_order() -> None:
    assert paths.matching(["b.md", "x/a.md", "a.md"], ["*.md"]) == ["b.md", "a.md"]
