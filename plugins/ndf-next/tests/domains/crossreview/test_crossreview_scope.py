"""ラウンドのレビューの範囲（#3181）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.domains.crossreview import scope_decide, store

from ._repos import Repos, commit, run_cli

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
        {store.ORIGIN_KEY: 8, "rounds": [{"round": 1, "head": "abc1234"}], store.REVIEWED: ["issues/d.md#設計", A]},
    )
    base = ["review-scope", str(wt), "--state-dir", str(state), "--pr", "8", "--base", "origin/main"]
    code, got = run_cli(capsys, *base, "--purpose", "design")
    assert code == 0
    item = got["items"][0]
    assert (item["scope"], item["unreviewed"]) == (scope_decide.FULL, [B])
    code, got = run_cli(capsys, *base, "--purpose", "check")
    assert code == 0
    assert got["items"][0]["scope"] == scope_decide.SINCE


# --- 現状固定テスト: `review-scope`（cli.cmd_scope）が状態ファイルから読む範囲（I-006） ---

_SECTIONS = ["issues/d.md#設計", A, B]


@pytest.fixture
def scope_run(repos: Repos, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """文書と文書でないファイルを変えた PR で、状態ファイルの中身ごとに review-scope を打って結果を返す。"""
    wt = repos.add_worktree(tmp_path / "pr8")
    commit(wt, "issues/d.md", "# 設計\n\n## 節 A\n\n## 節 B\n", "設計")
    commit(wt, "a.txt", "x", "文書でない")
    state = tmp_path / "state"
    state.mkdir()
    path = state / store.STATE_NAME.format(pr=8)

    def run(purpose: str, data) -> tuple[int, dict]:
        if data is not None:
            store.write_object(path, data)
        return run_cli(
            capsys, "review-scope", str(wt), "--state-dir", str(state), "--pr", "8", "--purpose", purpose, "--base", "origin/main"
        )

    return run


def _scope(kind: str, base, unreviewed: list[str]) -> dict:
    return {"kind": "scope", "scope": kind, "base": base, "unreviewed": unreviewed}


def test_scope_without_a_state_file_is_round_one_full(scope_run) -> None:
    """状態ファイルが無ければ round 1 の全差分。設計の PR は変わった文書の節をすべて未レビューに挙げる。"""
    code, got = scope_run("design", None)
    assert (code, got["status"]) == (0, "ok")
    assert got["summary"] == "round 1 の範囲は full（詳細レビューを通らない節 3 件）"
    assert got["items"] == [_scope(scope_decide.FULL, None, _SECTIONS)]
    code, got = scope_run("check", None)
    assert got["summary"] == "round 1 の範囲は full（詳細レビューを通らない節 0 件）"
    assert got["items"] == [_scope(scope_decide.FULL, None, [])]


@pytest.mark.parametrize(
    ("data", "summary", "item"),
    [
        (
            {"pr": 8, "rounds": [{"round": 1, "head": "abc"}]},
            "round 2 の範囲は since（詳細レビューを通らない節 0 件）",
            _scope(scope_decide.SINCE, "abc", []),
        ),
        # 最後のラウンドに head が無い・ラウンドが辞書でないときは前の head が無い扱いで全差分
        (
            {"pr": 8, "rounds": [{"round": 1}]},
            "round 2 の範囲は full（詳細レビューを通らない節 0 件）",
            _scope(scope_decide.FULL, None, []),
        ),
        ({"pr": 8, "rounds": ["x"]}, "round 2 の範囲は full（詳細レビューを通らない節 0 件）", _scope(scope_decide.FULL, None, [])),
        # rounds が並びでなければラウンドが無い扱い
        ({"pr": 8, "rounds": "x"}, "round 1 の範囲は full（詳細レビューを通らない節 0 件）", _scope(scope_decide.FULL, None, [])),
    ],
)
def test_scope_reads_rounds_of_the_state_file(scope_run, data: dict, summary: str, item: dict) -> None:
    code, got = scope_run("check", data)
    assert (code, got["summary"], got["items"]) == (0, summary, [item])


def test_scope_of_a_design_pr_treats_a_broken_reviewed_list_as_empty(scope_run) -> None:
    """reviewed_sections が並びでなければ、どの節も詳細レビューを通っていない扱いで全差分を見る。"""
    code, got = scope_run("design", {"pr": 8, "rounds": [{"round": 1, "head": "abc"}], store.REVIEWED: "x"})
    assert got["summary"] == "round 2 の範囲は full（詳細レビューを通らない節 3 件）"
    assert got["items"] == [_scope(scope_decide.FULL, None, _SECTIONS)]


def test_scope_of_a_design_pr_narrows_to_the_last_head_once_every_section_was_reviewed(scope_run) -> None:
    rounds = [{"round": 1, "head": "abc"}, {"round": 2, "head": "def"}]
    code, got = scope_run("design", {"pr": 8, "rounds": rounds, store.REVIEWED: _SECTIONS})
    assert got["summary"] == "round 3 の範囲は since（詳細レビューを通らない節 0 件）"
    assert got["items"] == [_scope(scope_decide.SINCE, "def", [])]
