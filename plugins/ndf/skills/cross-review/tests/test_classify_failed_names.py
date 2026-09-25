"""失敗名の一覧を受ける薄い入口 `_classify_failed_names` の現状固定テスト（R5-005）。

`cmd_merge_fix` は申告された失敗名を `[{"name", "status": "completed",
"conclusion": "failure"}]` へ写して `_classify_ci` へ渡していた。この写像を
`_classify_failed_names` へ寄せても、code-related / meta-only の分岐が変わらないことを
固定する。分岐は `_classify_ci` を直接呼んだ結果と一致していなければならない。
"""
from __future__ import annotations


def test_failed_names_split_matches_the_explicit_mapping(state_mod):
    """失敗名の写像を寄せても、`_classify_ci` を直接呼んだ結果と一致する。"""
    names = ["pytest", "check_pr_requirements", "我々の知らないチェック", "labels"]

    got = state_mod._classify_failed_names(names)

    expected = state_mod._classify_ci(
        [{"name": n, "status": "completed", "conclusion": "failure"} for n in names]
    )

    assert got == expected
    # 分岐そのものも固定する（申告された失敗はすべて completed 扱い）。
    assert got.code_failed == ["pytest", "我々の知らないチェック"]
    assert got.meta_failed == ["check_pr_requirements", "labels"]
    assert got.pending == []


def test_a_code_related_failure_alone_is_code_failed(state_mod):
    """コード関連の失敗だけなら code_failed に入り、meta_failed は空。"""
    got = state_mod._classify_failed_names(["pytest"])

    assert got.code_failed == ["pytest"]
    assert got.meta_failed == []


def test_a_meta_only_failure_alone_is_meta_failed(state_mod):
    """メタチェックだけの失敗なら meta_failed に入り、code_failed は空。"""
    got = state_mod._classify_failed_names(["labels"])

    assert got.code_failed == []
    assert got.meta_failed == ["labels"]


def test_an_empty_list_yields_no_failures(state_mod):
    got = state_mod._classify_failed_names([])

    assert got.code_failed == []
    assert got.meta_failed == []
    assert got.pending == []
