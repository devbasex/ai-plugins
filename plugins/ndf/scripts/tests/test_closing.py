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


# --- 閉じる予定の参照（#2228） --------------------------------------------------------

import os  # noqa: E402
import subprocess  # noqa: E402

from closing import DEFERRED, closing_issues, closing_numbers, closing_refs, closing_words, defer_closing  # noqa: E402

SH = Path(__file__).resolve().parents[1] / "lib" / "closing-issues.sh"
BODY = (
    "## 閉じる課題\n\nCloses #12\nfixes: o/r#3, #4\nResolves https://github.com/o/r/issues/9\n"
    "最終工程の後に閉じる:#20\nRefs #30\nclosed #12\n"
)


def test_defer_closing_keeps_the_targets_and_leaves_no_closing_keywords():
    new, targets = defer_closing(BODY)
    assert targets == ["#12", "o/r#3", "https://github.com/o/r/issues/9", "#12"]
    assert closing_words(new) == []
    assert closing_issues(new) == closing_issues(BODY)
    assert f"{DEFERRED}: o/r#3" in new and "Refs #30" in new


def test_the_deferred_form_is_read_as_a_closing_target_in_order():
    text = "最終工程の後に閉じる: #20\nCloses #12\n最終工程の後に閉じる : https://github.com/o/r/issues/9"
    assert closing_issues(text) == ["#20", "#12", "https://github.com/o/r/issues/9"]
    assert closing_numbers(text) == [20, 12, 9]
    assert closing_refs(text, "a/b") == [("a/b", 20), ("a/b", 12), ("o/r", 9)]
    assert closing_words(text) == ["Closes #12"]


def test_the_deferred_form_needs_the_colon():
    assert closing_issues("最終工程の後に閉じる #20") == []


@pytest.mark.parametrize("locale", ["C", "C.UTF-8"])
def test_closing_issues_sh_reads_the_same_targets_as_closing_refs(locale):
    """2 つの読み手（closing-issues.sh と closing.closing_refs）が同じ本文から同じ指し先を返す。"""
    for text in (BODY, defer_closing(BODY)[0]):
        p = subprocess.run(
            ["bash", str(SH), "--repo", "a/b"], input=text, capture_output=True, text=True, env={**os.environ, "LC_ALL": locale}
        )
        assert p.returncode == 0, p.stderr
        got = [(r, int(n)) for r, n in (ln.split("\t") for ln in p.stdout.splitlines() if ln)]
        assert got == closing_refs(text, "a/b")
