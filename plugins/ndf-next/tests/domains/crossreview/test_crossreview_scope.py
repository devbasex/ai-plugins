"""ラウンドのレビューの範囲（#3181）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.domains.crossreview import cli, scope_decide, store

from ._repos import Repos, commit, last_result

A, B = "issues/d.md#節 A", "issues/d.md#節 B"


def test_sections_skip_headings_inside_code_blocks() -> None:
    text = "# 題\n\n```\n# コメント\n```\n## 節 A\n"
    assert scope_decide.sections_of("d.md", text) == ["d.md#題", "d.md#節 A"]


def test_issue_3181_design_pr_keeps_the_full_diff_until_every_section_had_a_detailed_review() -> None:
    """#3181: 設計の PR は、詳細レビューを通らない節が残る間は 2 ラウンド目以降も全差分を見る。"""
    got = scope_decide.review_scope(True, 2, "abc1234", [A, B], {A})
    assert got == scope_decide.Scope(scope_decide.FULL, None, (B,))
    assert scope_decide.review_scope(True, 3, "abc1234", [A, B], {A, B}) == scope_decide.Scope(scope_decide.SINCE, "abc1234", ())


@pytest.mark.parametrize(
    ("round_no", "prev", "kind"), [(1, None, scope_decide.FULL), (2, None, scope_decide.FULL), (2, "abc", scope_decide.SINCE)]
)
def test_other_prs_narrow_to_the_previous_head_from_round_two(round_no: int, prev, kind: str) -> None:
    assert scope_decide.review_scope(False, round_no, prev, [A, B], set()).kind == kind


def test_issue_3181_cli_reads_sections_of_changed_documents(repos: Repos, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """#3181: 設計のプランの purpose の PR で、変わった文書の節が前のラウンドで詳細レビューを通っていなければ全差分を見る。"""
    wt = repos.add_worktree(tmp_path / "pr8")
    commit(wt, "issues/d.md", "# 設計\n\n## 節 A\n\n## 節 B\n", "設計")
    state = tmp_path / "state"
    store.write_object(
        state / store.STATE_NAME.format(pr=8),
        {store.ORIGIN_KEY: 8, "rounds": [{"round": 1, "head": "abc1234"}], cli.REVIEWED: ["issues/d.md#設計", A]},
    )
    base = ["review-scope", str(wt), "--state-dir", str(state), "--pr", "8", "--base", "origin/main"]
    assert cli.main([*base, "--purpose", "design"]) == 0
    item = last_result(capsys.readouterr().out)["items"][0]
    assert (item["scope"], item["unreviewed"]) == (scope_decide.FULL, [B])
    assert cli.main([*base, "--purpose", "check"]) == 0
    assert last_result(capsys.readouterr().out)["items"][0]["scope"] == scope_decide.SINCE
