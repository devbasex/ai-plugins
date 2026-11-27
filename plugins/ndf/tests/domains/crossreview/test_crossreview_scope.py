"""ラウンドのレビューの範囲（#3181）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.core.converge import participants
from ndf.domains.crossreview import scope_decide, store
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.records import mvv

from ._repos import Repos, commit, count_git, run_cli, sh

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


def test_scope_that_is_full_does_not_ask_git_for_the_last_head(scope_run, monkeypatch: pytest.MonkeyPatch) -> None:
    """#4285: 詳細レビューを通らないセクションが残る設計の PR は、前の head によらず全差分なので、祖先かを git に尋ねない。"""
    from ndf.adapters import git

    asked: list = []
    original = git.Git.is_ancestor
    monkeypatch.setattr(git.Git, "is_ancestor", lambda self, *a: asked.append(a) or original(self, *a))
    rounds = [{"round": 1, "head": "abc", "base": MB}]
    assert scope_run("design", {"pr": 8, "rounds": rounds})[1]["items"] == [_scope(scope_decide.FULL, None, _SECTIONS)]
    assert asked == []
    assert scope_run("design", {"pr": 8, "rounds": rounds, store.REVIEWED: _SECTIONS})[1]["items"][0]["scope"] == scope_decide.SINCE
    assert len(asked) == 1


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
    only_incomplete = {"rounds": [{"round": 1, "head": "h1", "verdict": fin.INCOMPLETE_REVIEWERS}]}
    assert scope_decide.read_by_all_head(only_incomplete) is None
    after_full = {
        "rounds": [
            {"round": 1, "head": "h1", "verdict": "changes_required"},
            {"round": 2, "head": "h2", "verdict": fin.INCOMPLETE_REVIEWERS},
        ]
    }
    assert scope_decide.read_by_all_head(after_full) == "h1"


APPROVED = "approved:11:aaa:ccc"


def _read_with(mvv: object) -> dict:
    """MVV の記録が `mvv`（None なら記録が無い）の 1 ラウンドを持つ状態ファイルの中身。"""
    row = {"round": 1, "head": "h1", "verdict": "converged", fin.MVV_CHECKED: True}
    return {"rounds": [row if mvv is None else {**row, scope_decide.MVV: mvv}]}


@pytest.mark.parametrize(
    ("recorded", "now", "head"),
    [
        ({"id": APPROVED, "mvv_branch": "main"}, APPROVED, "h1"),
        ({"id": "absent:::"}, "absent:::", "h1"),
        # MVV の記録が無いラウンド（この記録を足す前のバージョンが書いた）は、今も MVV が無いときだけ同じとみなす
        (None, "absent:::", "h1"),
        ("壊れた値", "absent:::", "h1"),
        (None, APPROVED, None),
        (None, "unapproved:::", None),
        # バージョン・本文・共通原則・承認の状態のどれか 1 つでも違えば、前のラウンドを使わない
        ({"id": APPROVED}, "approved:12:aaa:ccc", None),
        ({"id": APPROVED}, "approved:11:bbb:ccc", None),
        ({"id": APPROVED}, "approved:11:aaa:ddd", None),
        ({"id": APPROVED}, "absent:::", None),
        ({"id": "absent:::"}, APPROVED, None),
        # 今の MVV を確かめられなければ、どの記録とも違うとみなす
        ({"id": APPROVED}, scope_decide.UNKNOWN_MVV, None),
        ({"id": scope_decide.UNKNOWN_MVV}, scope_decide.UNKNOWN_MVV, None),
        (None, scope_decide.UNKNOWN_MVV, None),
    ],
)
def test_previous_round_is_reused_only_under_the_same_mvv(recorded: object, now: str, head: str | None) -> None:
    state = _read_with(recorded)
    assert scope_decide.read_by_all_head(state, scope_decide.Basis(mvv=now)) == head


@pytest.mark.parametrize("checked", [True, False, None, "true"])
def test_round_is_reused_under_an_approved_mvv_only_when_its_citations_were_checked(checked: object) -> None:
    """承認済みの MVV があれば、根拠の記述を確かめ終えたラウンドだけを使う。MVV が無ければ確かめの成否を見ない。"""
    for now in (APPROVED, "absent:::"):
        state = _read_with({"id": now})
        del state["rounds"][0][fin.MVV_CHECKED]
        if checked is not None:
            state["rounds"][0][fin.MVV_CHECKED] = checked
        reused = now != APPROVED or checked is True
        assert scope_decide.read_by_all_head(state, scope_decide.Basis(mvv=now)) == ("h1" if reused else None)


@pytest.mark.parametrize(
    ("recorded", "now", "reviewed"),
    [
        (APPROVED, APPROVED, {A, B}),
        (None, "absent:::", {A, B}),
        (None, APPROVED, set()),
        (APPROVED, "approved:12:bbb:ccc", set()),
        (APPROVED, scope_decide.UNKNOWN_MVV, set()),
        (APPROVED, None, {A, B}),
    ],
)
def test_sections_reviewed_under_another_mvv_are_reviewed_again(recorded: str | None, now: str | None, reviewed: set[str]) -> None:
    """前と違う MVV で詳細レビューを通ったセクションは、設計の PR ではまだ通っていないセクションとして並ぶ。"""
    state = {**_read_with({"id": recorded} if recorded else None), store.REVIEWED: [A, B]}
    if recorded:
        state[store.REVIEWED_MVV] = recorded
    assert scope_decide.reviewed_of(state, now) == reviewed
    basis = scope_decide.Basis(mvv=now)
    prev = scope_decide.read_by_all_head(state, basis)
    got = scope_decide.review_scope(True, 2, prev, [A, B], scope_decide.reviewed_of(state, basis.mvv))
    assert got == (scope_decide.Scope(scope_decide.SINCE, "h1", ()) if reviewed else scope_decide.Scope(scope_decide.FULL, None, (A, B)))


@pytest.mark.parametrize(
    ("recorded", "now", "reviewed"), [("main", "main", {A, B}), ("main", "sprint/y", set()), (None, "main", set()), ("main", None, {A, B})]
)
def test_issue_3702_sections_reviewed_against_another_target_are_reviewed_again(
    recorded: str | None, now: str | None, reviewed: set[str]
) -> None:
    """MVV が同じでも、前と違う比較元のブランチで詳細レビューを通ったセクションは、まだ通っていないセクションとして並ぶ。"""
    state = {store.REVIEWED: [A, B], store.REVIEWED_MVV: APPROVED}
    if recorded:
        state[store.REVIEWED_BASE_BRANCH] = recorded
    assert scope_decide.reviewed_of(state, APPROVED, now) == reviewed


def test_branch_the_mvv_was_read_from_is_the_last_recorded_one() -> None:
    rounds = [{scope_decide.MVV: {"id": APPROVED, "mvv_branch": "main"}}, {"round": 2}, "壊れたラウンド"]
    assert scope_decide.mvv_branch({"rounds": rounds}) == "main"
    assert scope_decide.mvv_branch({"rounds": [{"round": 1}]}) is None


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


def test_scope_is_full_when_the_sections_need_objects_that_are_not_local(
    repos: Repos, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """部分クローンで、変わった文書を手元のオブジェクトだけで読めなければ、取得せずに全差分を返し、理由を注意に載せる。
    範囲を変えない MVV は読まない。"""
    from ndf.adapters import git
    from ndf.domains.crossreview import changes

    wt = repos.add_worktree(tmp_path / "pr8")
    commit(wt, "a.txt", "x", "文書でない")
    state = tmp_path / "state"
    sha = sh(wt, "merge-base", "origin/main", "HEAD").strip()
    data = {store.ORIGIN_KEY: 8, "rounds": [{"round": 1, "head": sha, "base": MB}]}
    store.write_object(state / store.STATE_NAME.format(pr=8), _with_merge_base(data, sha))
    base = ["review-scope", str(wt), "--state-dir", str(state), "--pr", "8", "--base", "origin/main", "--purpose", "check"]
    assert run_cli(capsys, *base)[1]["items"][0]["scope"] == scope_decide.SINCE
    used: list[git.Git] = []

    def missing(g: git.Git, *args: object) -> list[str]:
        used.append(g)
        raise git.GitError("could not fetch from promisor remote")

    monkeypatch.setattr(changes, "changed_sections", missing)
    monkeypatch.setattr(mvv, "identity_at", lambda *a: pytest.fail("全差分に決まった後に MVV を読んだ"))
    code, got = run_cli(capsys, *base)
    assert (code, got["items"][0]["scope"], got["items"][0]["base"]) == (0, scope_decide.FULL, None)
    assert "promisor" in got["warnings"][0] and [g.env for g in used] == [git.NO_LAZY_FETCH]


def test_changed_sections_do_not_look_for_renames(tmp_path: Path) -> None:
    """変わったセクションは改名を調べずに読み（調べると部分クローンで前の本文の取得が要る）、改名前と消えたパスを除く。"""
    from ndf.adapters import git
    from ndf.domains.crossreview import changes

    calls: list[list[str]] = []

    def runner(args: list[str], cwd: Path | None) -> git.Completed:
        calls.append(args)
        return git.Completed(0, "a.txt\0", "")

    assert changes.changed_sections(git.Git(tmp_path, runner), "b", tmp_path) == []
    [call] = calls
    assert call[0] == "diff" and {"--no-renames", "--diff-filter=d"} <= set(call)


def test_committed_text_skipped_by_git_is_an_error_unless_the_file_is_empty(tmp_path: Path, monkeypatch) -> None:
    """手元に無い blob を git grep が飛ばしたら、本文の無い文書とみなさずに止める。空のファイルは行が無くてよい。"""
    from ndf.adapters import git
    from ndf.domains.crossreview import changes

    empty = mvv.blob_id(b"", 40)
    trees = {"empty.md": empty, "a.md": "a" * 40}
    monkeypatch.setattr(git.Git, "run_bytes", lambda self, *args, **kw: b"")

    def changed(*paths: str) -> list[str]:
        listed = "".join(f"100644 blob {trees[p]}\t{p}\0" for p in paths)
        out = {"diff": git.Completed(0, "\0".join(paths), ""), "ls-tree": git.Completed(0, listed, "")}
        return changes.changed_sections(git.Git(tmp_path, lambda args, cwd: out[args[0]]), "b", tmp_path)

    assert changed("empty.md") == []
    with pytest.raises(git.GitError, match=r"本文を読めない: a\.md"):
        changed("empty.md", "a.md")


def test_linked_markdown_is_not_followed(repos: Repos, tmp_path: Path) -> None:
    """PR 3944 の r6-1: PR が足した Markdown のシンボリックリンクはたどらず（worktree の外のファイルの見出しを相手役へ
    渡さない）、通常のファイルはコミットの blob と同じ本文を読む。作業場所で書き換えた本文でなくコミットの本文を読む。"""
    from ndf.adapters import git
    from ndf.domains.crossreview import changes

    outside = tmp_path / "outside.md"
    outside.write_text("# 利用者の秘密の見出し\n", encoding="utf-8")
    wt = repos.add_worktree(tmp_path / "pr8")
    (wt / "link.md").symlink_to(outside)
    (wt / "docs").mkdir()
    (wt / "docs" / "real.md").write_text("# 本物\n", encoding="utf-8")
    sh(wt, "add", "link.md", "docs/real.md")
    sh(wt, "commit", "-q", "-m", "リンクと文書")
    (wt / "docs" / "real.md").write_text("# 手元だけ\n", encoding="utf-8")
    assert changes.changed_sections(git.Git(wt), "origin/main", wt) == ["docs/real.md#本物"]


def test_markdown_bodies_cost_at_most_one_git_call(repos: Repos, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PR 3944 の r7-4: 改行を CRLF で取り出した Markdown は作業場所のファイルから読み（git を起動しない）、作業場所と
    違う Markdown は何件あってもコミットの本文を 1 回の git の起動で読む。"""
    from ndf.adapters import git
    from ndf.domains.crossreview import changes

    wt = repos.add_worktree(tmp_path / "pr8")
    for name in ("a.md", "b.md", "c.md", "empty.md"):
        (wt / name).write_text("" if name == "empty.md" else f"# {name}\n\n## セクション {name}\n", encoding="utf-8")
    sh(wt, "add", ".")
    sh(wt, "commit", "-q", "-m", "文書")
    calls = count_git(monkeypatch)
    g = git.Git(wt)
    (wt / "a.md").write_bytes("# a.md\r\n\r\n## セクション a.md\r\n".encode("utf-8"))
    assert changes.changed_sections(g, "origin/main", wt) == [
        "a.md#a.md",
        "a.md#セクション a.md",
        "b.md#b.md",
        "b.md#セクション b.md",
        "c.md#c.md",
        "c.md#セクション c.md",
    ]
    assert not any("grep" in c or "cat-file" in c for c in calls)
    calls.clear()
    for name in ("b.md", "c.md", "empty.md"):
        (wt / name).write_text("# 手元だけ\n", encoding="utf-8")
    assert changes.changed_sections(g, "origin/main", wt) == [
        "a.md#a.md",
        "a.md#セクション a.md",
        "b.md#b.md",
        "b.md#セクション b.md",
        "c.md#c.md",
        "c.md#セクション c.md",
    ]
    assert sum("grep" in c for c in calls) == 1 and not any("cat-file" in c for c in calls)


# ---------------------------------------------------------------- #3955: 埋めた席の記録と、前回の席への割り当て


# (席, 前回の席ごとに数えられる相手役, 割り当てられるか)
ASSIGNABLE = [
    (("codex", "kiro"), [{"codex"}, {"claude", "kiro"}], True),
    (("codex", "claude"), [{"codex"}, {"claude", "kiro"}], True),
    (("codex", "claude"), [{"kiro"}, {"claude", "codex"}], False),
    (("codex", "kiro", "claude"), [{"codex"}, {"claude", "kiro"}], False),
    ((), [], True),
]


def test_issue_3955_filled_seats_and_previous_seats() -> None:
    """埋めた席の記録は往復し、壊れた要素を飛ばす。前回の席は欠けた相手役の代わりにも数え、1 つの席は 1 者にしか割り当てない。
    全員が読んだ head は、代わりを数える起動でだけ埋めた席を欠けた相手役に数える。"""
    f = scope_decide.Filled("claude", "kiro", "failed: 落ちた")
    assert f.record() == {"seat": "claude", "for": "kiro", "reason": "failed: 落ちた"}
    none = scope_decide.Filled("claude-2", None, "allowed の相手役 0")
    record = {scope_decide.FILLED: [f.record(), none.record(), "x", {"for": "kiro"}, {"seat": 1}]}
    assert scope_decide.filled_of(record) == [f, none]
    assert scope_decide.filled_of({scope_decide.FILLED: "x"}) == []
    assert scope_decide.filled_of({}) == []

    rec = {"reviewers": ["codex", "claude"], "filled": [{"seat": "claude", "for": "kiro", "reason": "r"}]}
    assert scope_decide.stand_ins(rec) == [frozenset({"codex"}), frozenset({"claude", "kiro"})]
    before = {"reviewers": ["codex", "claude"], "filled": [{"seat": "claude", "for": None, "reason": "r"}]}
    assert scope_decide.stand_ins(before) == [frozenset({"codex"}), frozenset({"claude"})]

    assert [scope_decide.assignable(seats, options) for seats, options, _ in ASSIGNABLE] == [want for _, _, want in ASSIGNABLE]

    state = {"rounds": [{**rec, "head": "h1", "verdict": fin.CONVERGED}]}
    for seats in (("codex", "kiro"), ("codex", "claude")):
        assert scope_decide.read_by_all_head(state, scope_decide.Basis(reviewers=seats, stand_in=True)) == "h1"
    assert scope_decide.read_by_all_head(state, scope_decide.Basis(reviewers=("codex", "kiro"))) is None
    assert scope_decide.read_by_all_head(state, scope_decide.Basis(reviewers=("codex", "claude"))) == "h1"
    swapped = {**state["rounds"][0], "reviewers": ["kiro", "claude"], "filled": [{"seat": "claude", "for": "codex", "reason": "r"}]}
    assert scope_decide.read_by_all_head({"rounds": [swapped]}, scope_decide.Basis(reviewers=("codex", "claude"), stand_in=True)) is None
