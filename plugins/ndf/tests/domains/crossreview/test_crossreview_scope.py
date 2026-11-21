"""ラウンドのレビューの範囲（#3181）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.core.converge import participants
from ndf.domains.crossreview import scope_decide, store

from ._repos import Repos, commit, run_cli, sh

A, B = "issues/d.md#セクション A", "issues/d.md#セクション B"
# ラウンドの記録の比較基点（`--base` と HEAD の merge-base の SHA。PR 3944 の r2-1）。状態ファイルを書く前に本物の SHA へ置き換える
MB = "<merge-base>"


def _with_merge_base(data, sha: str):
    """状態ファイルの中身の `MB` を本物の merge-base の SHA へ置き換え、`run` と同じく既定の相手役をラウンドに記録する
    （`review-scope` も今回の相手役で決める。PR 3944 の r3-4）。"""
    if isinstance(data, dict) and isinstance(data.get("rounds"), list):
        seats = list(participants.assign())
        rounds = [{"reviewers": seats, **r, "base": sha} if isinstance(r, dict) and r.get("base") == MB else r for r in data["rounds"]]
        return {**data, "rounds": rounds}
    return data


def test_sections_skip_headings_inside_code_blocks() -> None:
    text = "# 題\n\n```\n# コメント\n```\n## セクション A\n"
    assert scope_decide.sections_of("d.md", text) == ["d.md#題", "d.md#セクション A"]


def test_issue_3181_design_pr_keeps_the_full_diff_until_every_section_had_a_detailed_review() -> None:
    """#3181: 設計の PR は、詳細レビューを通らないセクションが残る間は 2 ラウンド目以降も全差分を見る。"""
    got = scope_decide.review_scope(True, 2, "abc1234", [A, B], {A})
    assert got == scope_decide.Scope(scope_decide.FULL, None, (B,))
    assert scope_decide.review_scope(True, 3, "abc1234", [A, B], {A, B}) == scope_decide.Scope(scope_decide.SINCE, "abc1234", ())


@pytest.mark.parametrize(
    ("round_no", "prev", "kind"), [(1, None, scope_decide.FULL), (2, None, scope_decide.FULL), (2, "abc", scope_decide.SINCE)]
)
def test_other_prs_narrow_to_the_previous_head_from_round_two(round_no: int, prev, kind: str) -> None:
    assert scope_decide.review_scope(False, round_no, prev, [A, B], set()).kind == kind


def test_issue_3181_cli_reads_sections_of_changed_documents(repos: Repos, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """#3181: 設計のプランの purpose の PR で、変わった文書のセクションが前のラウンドで詳細レビューを通っていなければ全差分を見る。"""
    wt = repos.add_worktree(tmp_path / "pr8")
    commit(wt, "issues/d.md", "# 設計\n\n## セクション A\n\n## セクション B\n", "設計")
    state = tmp_path / "state"
    sha = sh(wt, "merge-base", "origin/main", "HEAD").strip()
    store.write_object(
        state / store.STATE_NAME.format(pr=8),
        _with_merge_base(
            {store.ORIGIN_KEY: 8, "rounds": [{"round": 1, "head": sha, "base": MB}], store.REVIEWED: ["issues/d.md#設計", A]}, sha
        ),
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
    commit(wt, "issues/d.md", "# 設計\n\n## セクション A\n\n## セクション B\n", "設計")
    commit(wt, "a.txt", "x", "文書でない")
    state = tmp_path / "state"
    state.mkdir()
    path = state / store.STATE_NAME.format(pr=8)
    sha = sh(wt, "merge-base", "origin/main", "HEAD").strip()
    # 前のラウンドの head の名前から、今の HEAD の祖先の本物の SHA（前の head からの差分は祖先のときだけ使う。PR 3944 の r6-7）
    heads = {"abc": sha, "def": sh(wt, "rev-parse", "HEAD~1").strip()}

    def run(purpose: str, data) -> tuple[int, dict]:
        if data is not None:
            data = _with_merge_base(data, sha)
            if isinstance(data, dict) and isinstance(data.get("rounds"), list):
                data["rounds"] = [
                    {**r, "head": heads.get(r.get("head"), r.get("head"))} if isinstance(r, dict) else r for r in data["rounds"]
                ]
            store.write_object(path, data)
        return run_cli(
            capsys, "review-scope", str(wt), "--state-dir", str(state), "--pr", "8", "--purpose", purpose, "--base", "origin/main"
        )

    run.heads = heads  # type: ignore[attr-defined]
    return run


def _scope(kind: str, base, unreviewed: list[str]) -> dict:
    return {"kind": "scope", "scope": kind, "base": base, "unreviewed": unreviewed}


def test_scope_without_a_state_file_is_round_one_full(scope_run) -> None:
    """状態ファイルが無ければ round 1 の全差分。設計の PR は変わった文書のセクションをすべて未レビューに挙げる。"""
    code, got = scope_run("design", None)
    assert (code, got["status"]) == (0, "ok")
    assert got["summary"] == "round 1 の範囲は full（詳細レビューを通らないセクション 3 件）"
    assert got["items"] == [_scope(scope_decide.FULL, None, _SECTIONS)]
    code, got = scope_run("check", None)
    assert got["summary"] == "round 1 の範囲は full（詳細レビューを通らないセクション 0 件）"
    assert got["items"] == [_scope(scope_decide.FULL, None, [])]


@pytest.mark.parametrize(
    ("data", "summary", "item"),
    [
        (
            {"pr": 8, "rounds": [{"round": 1, "head": "abc", "base": MB}]},
            "round 2 の範囲は since（詳細レビューを通らないセクション 0 件）",
            _scope(scope_decide.SINCE, "abc", []),
        ),
        # 前のラウンドの比較基点（merge-base の SHA）が違う（記録が無い）なら PR の全差分が変わり得るので全差分（PR 3944 の r1-10）
        (
            {"pr": 8, "rounds": [{"round": 1, "head": "abc", "base": "origin/develop"}]},
            "round 2 の範囲は full（詳細レビューを通らないセクション 0 件）",
            _scope(scope_decide.FULL, None, []),
        ),
        (
            {"pr": 8, "rounds": [{"round": 1, "head": "abc"}]},
            "round 2 の範囲は full（詳細レビューを通らないセクション 0 件）",
            _scope(scope_decide.FULL, None, []),
        ),
        # 最後のラウンドに head が無い・ラウンドが辞書でないときは前の head が無い扱いで全差分
        (
            {"pr": 8, "rounds": [{"round": 1}]},
            "round 2 の範囲は full（詳細レビューを通らないセクション 0 件）",
            _scope(scope_decide.FULL, None, []),
        ),
        ({"pr": 8, "rounds": ["x"]}, "round 2 の範囲は full（詳細レビューを通らないセクション 0 件）", _scope(scope_decide.FULL, None, [])),
        # rounds が並びでなければラウンドが無い扱い
        ({"pr": 8, "rounds": "x"}, "round 1 の範囲は full（詳細レビューを通らないセクション 0 件）", _scope(scope_decide.FULL, None, [])),
    ],
)
def test_scope_reads_rounds_of_the_state_file(scope_run, data: dict, summary: str, item: dict) -> None:
    code, got = scope_run("check", data)
    item = {**item, "base": scope_run.heads.get(item["base"], item["base"])}
    assert (code, got["summary"], got["items"]) == (0, summary, [item])


def test_scope_of_a_design_pr_treats_a_broken_reviewed_list_as_empty(scope_run) -> None:
    """reviewed_sections が並びでなければ、どのセクションも詳細レビューを通っていない扱いで全差分を見る。"""
    code, got = scope_run("design", {"pr": 8, "rounds": [{"round": 1, "head": "abc"}], store.REVIEWED: "x"})
    assert got["summary"] == "round 2 の範囲は full（詳細レビューを通らないセクション 3 件）"
    assert got["items"] == [_scope(scope_decide.FULL, None, _SECTIONS)]


def test_scope_of_a_design_pr_narrows_to_the_last_head_once_every_section_was_reviewed(scope_run) -> None:
    rounds = [{"round": 1, "head": "abc", "base": MB}, {"round": 2, "head": "def", "base": MB}]
    code, got = scope_run("design", {"pr": 8, "rounds": rounds, store.REVIEWED: _SECTIONS})
    assert got["summary"] == "round 3 の範囲は since（詳細レビューを通らないセクション 0 件）"
    assert got["items"] == [_scope(scope_decide.SINCE, scope_run.heads["def"], [])]


def test_scope_is_full_when_the_last_head_is_not_an_ancestor(repos: Repos, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """PR 3944 の r6-7: 前の head の変更を取り除いて巻き直した（前の head が今の HEAD の祖先でない）なら、前の head からの
    差分では削除が相手役に渡らないので、PR の全差分を返す。前の head のコミットが手元に無いときも全差分。"""
    wt = repos.add_worktree(tmp_path / "pr8")
    commit(wt, "a.txt", "x", "残す")
    commit(wt, "b.txt", "y", "取り除く")
    dropped = sh(wt, "rev-parse", "HEAD").strip()
    sh(wt, "reset", "-q", "--hard", "HEAD~1")
    commit(wt, "c.txt", "z", "巻き直した後")
    state = tmp_path / "state"
    state.mkdir()
    sha = sh(wt, "merge-base", "origin/main", "HEAD").strip()
    base = ["review-scope", str(wt), "--state-dir", str(state), "--pr", "8", "--base", "origin/main", "--purpose", "check"]
    for head in (dropped, "0" * 40):
        rounds = [{"round": 1, "head": head, "base": MB, "verdict": "changes_required"}]
        store.write_object(state / store.STATE_NAME.format(pr=8), _with_merge_base({store.ORIGIN_KEY: 8, "rounds": rounds}, sha))
        assert run_cli(capsys, *base)[1]["items"][0] == _scope(scope_decide.FULL, None, [])
    rounds = [{"round": 1, "head": sh(wt, "rev-parse", "HEAD~1").strip(), "base": MB, "verdict": "changes_required"}]
    store.write_object(state / store.STATE_NAME.format(pr=8), _with_merge_base({store.ORIGIN_KEY: 8, "rounds": rounds}, sha))
    assert run_cli(capsys, *base)[1]["items"][0]["scope"] == scope_decide.SINCE


def test_round_some_reviewers_could_not_read_is_not_the_next_base() -> None:
    """PR 3944 の r1-1: 相手役の一部が読めなかったラウンドの head を起点にせず、全員が読めた最後のラウンドから。無ければ全差分。"""
    from ndf.domains.crossreview import finish_decide as fin

    only_incomplete = {"rounds": [{"round": 1, "head": "h1", "verdict": fin.INCOMPLETE_REVIEWERS}]}
    assert scope_decide.state_scope(only_incomplete, False, 2, []).kind == scope_decide.FULL
    after_full = {
        "rounds": [
            {"round": 1, "head": "h1", "verdict": "changes_required"},
            {"round": 2, "head": "h2", "verdict": fin.INCOMPLETE_REVIEWERS},
        ]
    }
    assert scope_decide.state_scope(after_full, False, 3, []) == scope_decide.Scope(scope_decide.SINCE, "h1", ())


def test_scope_uses_the_same_reviewers_as_run(repos: Repos, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """PR 3944 の r3-4: 前のラウンドを読んでいない相手役を足すと、`run` と同じく全差分を返す（担当の既定も `run` と同じ）。"""
    wt = repos.add_worktree(tmp_path / "pr8")
    commit(wt, "a.txt", "x", "文書でない")
    state = tmp_path / "state"
    state.mkdir()
    sha = sh(wt, "merge-base", "origin/main", "HEAD").strip()
    rounds = [{"round": 1, "head": sha, "base": sha, "reviewers": ["codex"], "verdict": "changes_required"}]
    store.write_object(state / store.STATE_NAME.format(pr=8), {store.ORIGIN_KEY: 8, "rounds": rounds})
    base = ["review-scope", str(wt), "--state-dir", str(state), "--pr", "8", "--base", "origin/main", "--purpose", "check"]
    assert run_cli(capsys, *base, "--reviewer", "codex")[1]["items"][0]["scope"] == scope_decide.SINCE
    assert run_cli(capsys, *base, "--reviewer", "codex", "--reviewer", "kiro")[1]["items"][0]["scope"] == scope_decide.FULL
    assert run_cli(capsys, *base)[1]["items"][0]["scope"] == scope_decide.FULL


def test_linked_markdown_is_not_followed(repos: Repos, tmp_path: Path) -> None:
    """PR 3944 の r6-1: PR が足した Markdown のシンボリックリンクはたどらず（worktree の外のファイルの見出しを相手役へ
    渡さない）、通常のファイルはコミットの blob と同じ本文を読む。作業場所で書き換えた本文でなくコミットの本文を読む。"""
    from ndf.adapters import git
    from ndf.domains.crossreview import drive

    outside = tmp_path / "outside.md"
    outside.write_text("# 利用者の秘密の見出し\n", encoding="utf-8")
    wt = repos.add_worktree(tmp_path / "pr8")
    (wt / "link.md").symlink_to(outside)
    (wt / "docs").mkdir()
    (wt / "docs" / "real.md").write_text("# 本物\n", encoding="utf-8")
    sh(wt, "add", "link.md", "docs/real.md")
    sh(wt, "commit", "-q", "-m", "リンクと文書")
    (wt / "docs" / "real.md").write_text("# 手元だけ\n", encoding="utf-8")
    assert drive.changed_sections(git.Git(wt), "origin/main", wt) == ["docs/real.md#本物"]
