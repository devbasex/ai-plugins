"""Pull Request の本文から閉じる語が指す番号を取り出すことのテスト（#259）。

起点ブランチが既定ブランチでないリポジトリでは、マージしても GitHub の自動クローズが
働かない。完了した課題が課題の一覧に残り、次に着手する担当が完了済みの課題を拾う。

**閉じる語は番号ごとに要る。** 公式ドキュメントが "you must use the keyword before each
issue you reference" と定めており、`Fixes #12, #13` は 12 だけを閉じる。既定ブランチへ
マージしたときの GitHub の振る舞いと同じ結果になるようにする。
"""
from __future__ import annotations

import pytest

from closing_issues_helpers import numbers, read


def test_a_single_keyword_is_read() -> None:
    assert numbers("Closes #12\n") == ["12"]


def test_a_keyword_before_each_number_reads_both() -> None:
    assert numbers("Fixes #12, fixes #13\n") == ["12", "13"]


def test_a_bare_number_after_a_keyword_is_not_read() -> None:
    """`Fixes #12, #13` は 12 だけを対象にする。`#13` は閉じる語を伴わない参照である。"""
    assert numbers("Fixes #12, #13\n") == ["12"]


@pytest.mark.parametrize(
    "body",
    ["Resolves #12\nclose #13\n", "RESOLVES #12\nCLOSE #13\n", "resolved: #12\nFixed #13\n"],
)
def test_the_case_of_the_keyword_does_not_matter(body: str) -> None:
    assert numbers(body) == ["12", "13"]


@pytest.mark.parametrize(
    "keyword", ["close", "closes", "closed", "fix", "fixes", "fixed", "resolve", "resolves", "resolved"]
)
def test_every_keyword_github_accepts_is_read(keyword: str) -> None:
    assert numbers(f"{keyword} #42\n") == ["42"]


def test_a_number_without_a_keyword_is_not_read() -> None:
    assert numbers("関連: #12 と #13 を参照\n") == []


def test_a_body_without_any_keyword_ends_with_zero() -> None:
    """1 件も見つからないことは正常な結果である。失敗と読める値を返さない。"""
    result = read("閉じる語のない本文 #12\n")

    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_the_same_number_is_not_repeated() -> None:
    assert numbers("Closes #12\nFixes #12\n") == ["12"]


def test_the_order_of_the_body_is_kept() -> None:
    assert numbers("Fixes #30\nCloses #7\nResolves #19\n") == ["30", "7", "19"]


def test_a_keyword_inside_a_longer_word_is_not_read() -> None:
    assert numbers("unclosed #12\n") == []
