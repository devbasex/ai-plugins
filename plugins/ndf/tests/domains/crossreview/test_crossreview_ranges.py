"""`ndf cross-review run` の 2 ラウンド目以降に相手役へ渡す範囲（#3780）。相手役は偽物に差し替え、git は一時の origin で本物を打つ。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ndf.domains.crossreview import changes, prior_decide, reviewing, round_scope, scope_decide

from ._repos import BRANCH, Repos, commit, sh
from .test_crossreview_run import Fake, Recording, _focus_line, _run, _state


def _push_commits(repos: Repos, *names: str, ref: str = BRANCH) -> None:
    """別の書き手がファイルを 1 つずつ足して `ref` へ送り、主の clone で fetch する。"""
    for name in names:
        commit(repos.other, name, f"{name} の中身\n", name)
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{ref}")
    sh(repos.main, "fetch", "-q", "origin")


def _second_round(capsys, monkeypatch, state_dir: Path, wt: Path, *extra: str) -> tuple[dict, str, dict]:
    """打ち直しの 1 ラウンド。ラウンドの記録・相手役へ渡した差分・結果を返す。"""
    fake = Fake(state_dir, {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt, *extra)
    assert (code, got["status"]) == (0, "ok")
    return _state(state_dir)["rounds"][-1], fake.inputs["round2.diff"], got


def _read_against_target(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> tuple[Path, str]:
    """比較先 `target` との差分（`read.txt`）を 1 回レビューして収束し、`fix.txt` を足す。状態の保存先と読んだ head を返す。"""
    _push_commits(repos, "unread.txt")
    _push_commits(repos, ref="target")
    _push_commits(repos, "read.txt")
    state_dir = tmp_path / "st"
    _run(capsys, monkeypatch, Fake(state_dir, {"codex": [[]], "kiro": [[]]}), wt, "--base", "origin/target")
    head = _state(state_dir)["rounds"][0]["head"]
    _push_commits(repos, "fix.txt")
    return state_dir, head


def test_issue_3780_changed_base_reads_from_the_previous_head_and_the_new_pr_files(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch
) -> None:
    """#3780: 比較先を変えた 2 ラウンド目は前の head からの差分を読み、新しく PR の変更になったファイルを足す。"""
    state_dir, head = _read_against_target(repos, wt, tmp_path, capsys, monkeypatch)
    record, diff, got = _second_round(capsys, monkeypatch, state_dir, wt)
    assert record["scope"] == "since" and "warnings" not in got
    assert "+fix.txt の中身" in diff and "+unread.txt の中身" in diff and "+足した" in diff
    assert "read.txt の中身" not in diff.replace("unread.txt の中身", "")


def test_issue_3780_bases_that_cannot_be_compared_give_the_full_diff(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    state_dir, _ = _read_against_target(repos, wt, tmp_path, capsys, monkeypatch)
    monkeypatch.setattr(changes, "new_in_pr", lambda *a: None)
    record, diff, got = _second_round(capsys, monkeypatch, state_dir, wt)
    assert record["scope"] == "full" and "+read.txt の中身" in diff
    assert any("全差分" in w for w in got["warnings"])


def test_issue_3780_merged_base_changes_outside_the_pr_are_not_read(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """#3780: ベースブランチをマージした 2 ラウンド目は、PR が変えるファイルの前の head からの差分だけを読む。"""
    state_dir = tmp_path / "st"
    _run(capsys, monkeypatch, Fake(state_dir, {"codex": [[]], "kiro": [[]]}), wt)
    sh(repos.other, "checkout", "-q", "-B", "base-work", "origin/main")
    _push_commits(repos, "outside.txt", ref="main")
    sh(repos.other, "checkout", "-q", BRANCH)
    sh(repos.other, "pull", "-q", "--rebase", "origin", BRANCH)
    sh(repos.other, "fetch", "-q", "origin")
    sh(repos.other, "merge", "-q", "--no-edit", "origin/main")
    commit(repos.other, "doc.md", "# 設計\n\n## セクション A\n\n本文\n\n## セクション B\n\n直した\n", "直し")
    _push_commits(repos, "fix.txt")
    record, diff, got = _second_round(capsys, monkeypatch, state_dir, wt)
    assert record["scope"] == "since" and "warnings" not in got
    assert "+直した" in diff and "+fix.txt の中身" in diff and "outside.txt" not in diff


def test_issue_3780_comments_are_chosen_by_the_files_of_the_handed_diff() -> None:
    """#3780: PR に既にあるインラインのコメントは、渡す差分と同じファイル（足して渡すファイルを含み、比較基点が変わった
    ラウンドのベース側だけの変更を除く）に付いたものを相手役へ渡す。"""
    names = ("fix.txt", "added.txt", "read.txt", "outside.txt")
    comments = [prior_decide.Remark("review_comment", "u", f"{p} の指摘", p, 1) for p in names]
    run = SimpleNamespace(remarks=comments, g=SimpleNamespace(changed_files=lambda *a: ["fix.txt", "outside.txt"]))
    scope = scope_decide.Scope(scope_decide.SINCE, "abc", ())
    entry = changes._Entry("M", "fix.txt", "fix.txt", ("100644", "100644"), ("1", "2"))
    listed: changes.Listings = {"old...abc": ({}, frozenset()), "ghi...def": ({"fix.txt": entry}, frozenset())}
    since = round_scope.Since(changes.Range("old", "abc"), ("added.txt",), listed)
    prep = reviewing.Prepared(scope, [], set(), "def", Path("d"), (), "ghi", None, since)
    handed = "\n".join(reviewing.remarks(run, prep))
    assert "fix.txt の指摘" in handed and "added.txt の指摘" in handed
    assert "read.txt の指摘" not in handed and "outside.txt の指摘" not in handed


@pytest.mark.parametrize(("purpose", "kind"), [("cross-review", "since"), ("design", "full")])
def test_changed_focus_runs_again_with_the_range_of_the_purpose(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, purpose: str, kind: str
) -> None:
    """PR 3944 の r4-3: 収束した PR を、重点を足して同じ head で打ち直すと、前回の収束を返さず足した重点で読ませる。
    範囲は、実装の PR が前の head からの差分（#3780）、設計の PR が全差分である。"""
    state_dir = tmp_path / "st"
    _run(capsys, monkeypatch, Fake(state_dir, {"codex": [[]], "kiro": [[]]}), wt, "--purpose", purpose)
    (repos.main / ".ndf").mkdir(exist_ok=True)
    (repos.main / ".ndf" / "review.json").write_text(json.dumps({"version": 1, "focus": ["足した重点"]}), encoding="utf-8")
    fake = Recording(state_dir, {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt, "--purpose", purpose)
    assert (code, got["status"]) == (0, "ok") and fake.prompts
    assert all(_focus_line("足した重点") in p and f"範囲: {kind}" in p for _, _, p in fake.prompts)
    assert _state(state_dir)["rounds"][-1]["focus"] == ["足した重点"]
    again = Recording(state_dir, {"codex": [[]], "kiro": [[]]})
    _run(capsys, monkeypatch, again, wt, "--purpose", purpose)
    assert again.prompts == []
