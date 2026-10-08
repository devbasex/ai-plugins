"""lib/closing.py の referenced_numbers（課題の参照の読み取り、#767）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from closing import referenced_numbers  # noqa: E402


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Refs #554", [554]),
        ("関連 #550", [550]),
        ("課題#5", [5]),
        ("- 課題: #1743", [1743]),
        ("https://github.com/o/r/issues/77", [77]),
        ("HTTPS://GitHub.com/O/R/issues/78", [78]),
        ("x/y#3", []),
        ("o/r#9", []),
        ("https://github.com/x/y/issues/4", []),
        ("https://github.com/o/r/pull/8", []),
        ("#123abc", []),
        ("a#6 a.#7 _#8 -#9", []),
    ],
)
def test_reference_forms(text, expected):
    assert referenced_numbers(text, "o/r") == expected


def test_order_of_appearance_without_duplicates():
    assert referenced_numbers("#3 https://github.com/o/r/issues/1 #3 Fixes #2 #1", "o/r") == [3, 1, 2]
