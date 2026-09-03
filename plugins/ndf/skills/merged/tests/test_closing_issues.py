"""Pull Request の本文から閉じる語が指す番号を取り出すことのテスト（#259）。

起点ブランチが既定ブランチでないリポジトリでは、マージしても GitHub の自動クローズが
働かない。完了した課題が課題の一覧に残り、次に着手する担当が完了済みの課題を拾う。

**閉じる語は番号ごとに要る。** 公式ドキュメントが "you must use the keyword before each
issue you reference" と定めており、`Fixes #12, #13` は 12 だけを閉じる。既定ブランチへ
マージしたときの GitHub の振る舞いと同じ結果になるようにする。
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from closing_issues_helpers import DEFAULT_REPO, entries, numbers, read


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


# --- 閉じる先のリポジトリ（#229） -------------------------------------------


def test_a_bare_number_belongs_to_the_repository_it_ran_in() -> None:
    """番号だけの形は、これまでどおり実行したリポジトリの issue を指す。"""
    assert entries("Closes #12\n") == [(DEFAULT_REPO, "12")]


def test_a_number_with_a_repository_is_read() -> None:
    """`Fixes devbasex/ai-plugins#283` の形を落とさない。"""
    assert entries("Fixes other-owner/other-repo#283\n") == [("other-owner/other-repo", "283")]


def test_an_issue_url_is_read() -> None:
    body = "Closes https://github.com/other-owner/other-repo/issues/283\n"

    assert entries(body) == [("other-owner/other-repo", "283")]


def test_the_three_forms_are_read_from_one_body() -> None:
    """#229-1: 3 つの形が、いずれも所有者とリポジトリと番号に分かれて出る。"""
    body = (
        "Fixes #12\n"
        "Closes devbasex/other#283\n"
        "Resolves https://github.com/devbasex/third/issues/7\n"
    )

    assert entries(body) == [
        (DEFAULT_REPO, "12"),
        ("devbasex/other", "283"),
        ("devbasex/third", "7"),
    ]


def test_the_same_issue_in_another_repository_is_not_merged_into_one() -> None:
    """番号が同じでもリポジトリが違えば別の課題である。"""
    assert entries("Fixes #12\nCloses other/repo#12\n") == [(DEFAULT_REPO, "12"), ("other/repo", "12")]


def test_the_same_entry_is_not_repeated() -> None:
    assert entries("Fixes other/repo#12\nCloses other/repo#12\n") == [("other/repo", "12")]


def test_a_url_without_a_keyword_is_not_read() -> None:
    assert entries("関連: https://github.com/devbasex/other/issues/283\n") == []


def test_a_pull_request_url_is_not_read() -> None:
    """閉じる対象は issue である。Pull Request の URL は拾わない。"""
    assert entries("Closes https://github.com/devbasex/other/pull/283\n") == []


def test_the_repository_comes_from_the_remote_when_it_is_not_given(tmp_path) -> None:
    """`--repo` を渡さないときは、いま開いているリポジトリの origin から決める。"""
    if shutil.which("git") is None:
        pytest.skip("git が無い")
    repo = tmp_path / "main"
    repo.mkdir()
    for args in (
        ["init", "-q"],
        ["remote", "add", "origin", "git@github.com:devbasex/from-remote.git"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    assert entries("Closes #12\n", repo=None, cwd=repo) == [("devbasex/from-remote", "12")]


def test_without_a_repository_the_bare_number_is_skipped(tmp_path) -> None:
    """リポジトリを決められないときは、番号だけの形を落とす。"""
    outside = tmp_path / "plain"
    outside.mkdir()

    assert entries("Closes #12\nFixes other/repo#7\n", repo=None, cwd=outside) == [("other/repo", "7")]
