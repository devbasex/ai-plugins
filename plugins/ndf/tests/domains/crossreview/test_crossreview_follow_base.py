"""`ndf cross-review run --reviewed-base-out`: `--base` でなく PR の今のマージ先を比較元にし、その名前を書き出す（#3702）。

相手役と修正担当は `test_crossreview_run` の偽物を使い、PR のマージ先は偽の Hub（`conftest.FakePort.base_ref`）が返す。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.domains.crossreview import reviewed_pr

from ._repos import Repos, commit, sh
from .test_crossreview_run import Fake, Recording, _push_to_pr, _run, _state

SPRINT = "sprint/y"


@pytest.fixture(autouse=True)
def _fetches_from_the_pr_repo(monkeypatch: pytest.MonkeyPatch, hub) -> None:
    """テストのリモートはローカルのパスなので、取得元を PR のベースリポジトリと同じと読ませる。"""
    monkeypatch.setattr(reviewed_pr, "fetch_repo", lambda g, remote: hub.port.repo_name)


def _sprint_branch(repos: Repos) -> None:
    """main に 1 コミットを足したブランチを origin に作る（PR のブランチとの merge-base は main と同じ）。"""
    sh(repos.main, "checkout", "-q", "-b", SPRINT, "origin/main")
    commit(repos.main, "sprint.txt", "集めた\n", "スプリント")
    sh(repos.main, "push", "-q", "origin", f"HEAD:refs/heads/{SPRINT}")
    sh(repos.main, "checkout", "-q", "--detach")
    sh(repos.main, "fetch", "-q", "origin")


def test_issue_3702_reviewed_base_out_compares_with_the_pr_target_not_the_given_base(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub
) -> None:
    """`--reviewed-base-out` を渡すと、`--base` でなく PR の今のマージ先を取得して比較元にし、その名前を書く（前の値は消す）。"""
    _sprint_branch(repos)
    hub.port.base_ref = SPRINT
    base_out = tmp_path / "reviewed-base"
    base_out.write_text("main\n", encoding="utf-8")
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt, "--reviewed-out", str(tmp_path / "head"), "--reviewed-base-out", str(base_out))
    assert (code, got["status"]) == (0, "ok")
    assert base_out.read_text(encoding="utf-8") == f"{SPRINT}\n"
    last = _state(fake.state_dir)["rounds"][-1]
    assert last["base_branch"] == SPRINT
    assert last["base"] == sh(wt, "merge-base", f"origin/{SPRINT}", "HEAD").strip()


def test_issue_3702_changed_target_reviews_the_full_diff_against_the_new_target(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub
) -> None:
    """起動の間にマージ先が変われば、前回の収束を返さず、前の head からの差分でなく新しいマージ先との全差分を読ませる。"""
    _sprint_branch(repos)
    state_dir = tmp_path / "st"
    base_out = tmp_path / "reviewed-base"
    extra = ("--reviewed-out", str(tmp_path / "head"), "--reviewed-base-out", str(base_out))
    _run(capsys, monkeypatch, Fake(state_dir, {"codex": [[]], "kiro": [[]]}), wt, *extra)
    assert base_out.read_text(encoding="utf-8") == "main\n"
    commit(repos.other, "more.md", "# 足した\n", "足す")
    _push_to_pr(repos)
    hub.port.base_ref = SPRINT
    fake = Recording(state_dir, {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt, *extra)
    assert (code, got["status"]) == (0, "ok") and fake.prompts
    assert all("範囲: full" in p for _, _, p in fake.prompts)
    assert base_out.read_text(encoding="utf-8") == f"{SPRINT}\n"
    assert _state(state_dir)["rounds"][-1]["base_branch"] == SPRINT


def test_issue_3702_same_target_reuses_the_converged_round(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """マージ先が同じなら、前回の収束をそのまま返し、そのマージ先の名前を書く。"""
    state_dir = tmp_path / "st"
    base_out = tmp_path / "reviewed-base"
    extra = ("--reviewed-out", str(tmp_path / "head"), "--reviewed-base-out", str(base_out))
    _run(capsys, monkeypatch, Fake(state_dir, {"codex": [[]], "kiro": [[]]}), wt, *extra)
    again = Recording(state_dir, {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, again, wt, *extra)
    assert (code, got["status"], again.prompts) == (0, "ok", [])
    assert base_out.read_text(encoding="utf-8") == "main\n"


def test_issue_3702_without_reviewed_base_out_the_given_base_is_kept(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub
) -> None:
    """引数が無ければ今のまま `--base` のブランチを比較元にし、PR のマージ先を読まない。"""
    _sprint_branch(repos)
    hub.port.base_ref = SPRINT
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"], hub.port.base_reads) == (0, "ok", 0)
    assert _state(fake.state_dir)["rounds"][-1]["base_branch"] == "main"


def test_issue_3702_unreadable_target_stops_before_any_launch(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """PR のマージ先を読めなければ、担当を起動する前に precondition で止まる。"""
    hub.port.base_ref = None
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt, "--reviewed-out", str(tmp_path / "h"), "--reviewed-base-out", str(tmp_path / "b"))
    assert (got["cause"]["kind"], fake.calls) == ("precondition", [])


def test_issue_3702_unwritable_reviewed_base_out_is_precondition(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """`--reviewed-base-out` を書けなければ、相手役を起動する前に precondition で止まる（レビューの後に分かっても遅い）。"""
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    missing = tmp_path / "no-such-dir" / "base"
    code, got = _run(capsys, monkeypatch, fake, wt, "--reviewed-out", str(tmp_path / "h"), "--reviewed-base-out", str(missing))
    assert (got["cause"]["kind"], fake.calls) == ("precondition", [])
    assert str(missing) in got["cause"]["detail"]


@pytest.mark.parametrize("fetched", ["fork/repo", None], ids=["other-repo", "unknown"])
def test_issue_3702_target_fetched_from_another_repo_stops_before_any_launch(
    wt: Path, tmp_path: Path, capsys, monkeypatch, fetched
) -> None:
    """取得するリモートが PR のベースリポジトリと違うか確かめられなければ、担当を起動する前に precondition で止まる。"""
    monkeypatch.setattr(reviewed_pr, "fetch_repo", lambda g, remote: fetched)
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt, "--reviewed-out", str(tmp_path / "h"), "--reviewed-base-out", str(tmp_path / "b"))
    assert (got["cause"]["kind"], fake.calls) == ("precondition", [])
    assert not (tmp_path / "b").exists()


def test_issue_3702_reviewed_base_out_without_reviewed_out_is_invalid(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """`--reviewed-base-out` だけを渡すと、レビューを始める前に invalid で止まる。"""
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt, "--reviewed-base-out", str(tmp_path / "b"))
    assert (got["cause"]["kind"], fake.calls) == ("invalid", [])


def test_issue_3702_target_resolved_from_the_remote_is_not_read_again(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """対象のリポジトリを作業場所のリモートの URL から決めていれば、その値を取得元として使い、取得元を読み直さない。"""
    hub.port.fetched_from = hub.port.repo_name
    monkeypatch.setattr(reviewed_pr, "fetch_repo", lambda g, remote: pytest.fail("取得元を読み直した"))
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt, "--reviewed-out", str(tmp_path / "h"), "--reviewed-base-out", str(tmp_path / "b"))
    assert (code, got["status"]) == (0, "ok")
