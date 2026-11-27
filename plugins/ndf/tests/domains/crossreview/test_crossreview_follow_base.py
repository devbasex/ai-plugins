"""`ndf cross-review run --reviewed-base-out`: `--base` でなく PR の今のマージ先を比較元にし、その名前を書き出す（#3702）。

相手役と修正担当は `test_crossreview_run` の偽物を使い、PR のマージ先は偽の Hub（`conftest.FakePort.base_ref`）が返す。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ndf.domains.crossreview import reviewed_pr, threads

from ._repos import BRANCH, Repos, commit, sh
from .test_crossreview_run import PR, Fake, Recording, _push_to_pr, _run, _state

SPRINT = "sprint/y"


@pytest.fixture(autouse=True)
def _fetches_from_the_pr_repo(monkeypatch: pytest.MonkeyPatch, hub, repos: Repos) -> None:
    """テストのリモートはローカルのパスなので、取得元を PR のベースリポジトリと同じと読ませる。`--reviewed-base-out` の起動は
    PR の確認を求める（PR 4638 の r1-7）ので、読んだ PR の head は PR のブランチの origin の先頭にする。"""
    monkeypatch.setattr(reviewed_pr, "fetch_repo", lambda g, remote: hub.port.repo_name)
    port = hub.port
    monkeypatch.setattr(
        port,
        "read_pr",
        lambda: threads.ReadPr(f"{port.repo_name}#{PR}", BRANCH, sh(repos.origin, "rev-parse", BRANCH).strip(), port.repo_name),
    )


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
    """`--reviewed-base-out` を渡すと、`--base` でなく PR の今のマージ先を取得して比較元にし、その名前を書く。"""
    _sprint_branch(repos)
    hub.port.base_ref = SPRINT
    base_out = tmp_path / "reviewed-base"
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt, "--reviewed-out", str(tmp_path / "head"), "--reviewed-base-out", str(base_out))
    assert (code, got["status"]) == (0, "ok")
    assert base_out.read_text(encoding="utf-8") == f"{SPRINT}\n"
    last = _state(fake.state_dir)["rounds"][-1]
    assert last["mvv"]["mvv_branch"] == SPRINT
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
    assert _state(state_dir)["rounds"][-1]["mvv"]["mvv_branch"] == SPRINT


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


def test_issue_3702_changed_base_with_the_same_merge_base_reviews_again(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch
) -> None:
    """PR 4638 の r1-1: HEAD・merge-base・MVV が同じでも、`--base` を別のブランチへ替えれば前回の収束を返さず、新しい比較元で
    全差分を読ませる（`follow_base` でない起動も同じ）。"""
    _sprint_branch(repos)
    state_dir = tmp_path / "st"
    _run(capsys, monkeypatch, Fake(state_dir, {"codex": [[]], "kiro": [[]]}), wt)
    fake = Recording(state_dir, {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt, "--base", f"origin/{SPRINT}")
    assert (code, got["status"]) == (0, "ok") and fake.prompts
    assert all("範囲: full" in p for _, _, p in fake.prompts)
    rounds = _state(state_dir)["rounds"]
    assert rounds[-1]["base"] == rounds[0]["base"] and rounds[-1]["mvv"]["mvv_branch"] == SPRINT


def test_issue_3702_without_reviewed_base_out_the_given_base_is_kept(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub
) -> None:
    """引数が無ければ今のまま `--base` のブランチを比較元にし、PR のマージ先を読まない。"""
    _sprint_branch(repos)
    hub.port.base_ref = SPRINT
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"], hub.port.base_reads) == (0, "ok", 0)
    assert _state(fake.state_dir)["rounds"][-1]["mvv"]["mvv_branch"] == "main"


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


@pytest.mark.parametrize("other", ["--reviewed-out", "--reviewed-pr-out"])
def test_issue_3702_shared_output_is_invalid_and_kept(wt: Path, tmp_path: Path, capsys, monkeypatch, other: str) -> None:
    """PR 4638 の r1-3: 出力先を重ねると、消す前に invalid で止まる（後に書く値で先の値が失われる）。"""
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    shared = tmp_path / "out"
    shared.write_text("前の値\n", encoding="utf-8")
    head = shared if other == "--reviewed-out" else tmp_path / "h"
    extra = ("--reviewed-out", str(head), "--reviewed-base-out", str(shared))
    if other == "--reviewed-pr-out":
        extra += ("--reviewed-pr-out", str(tmp_path / "." / "out"))
    code, got = _run(capsys, monkeypatch, fake, wt, *extra)
    assert (got["cause"]["kind"], fake.calls, shared.read_text(encoding="utf-8")) == ("invalid", [], "前の値\n")


def test_issue_3702_target_resolved_from_the_remote_is_not_read_again(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """対象のリポジトリを作業場所のリモートの URL から決めていれば、その値を取得元として使い、取得元を読み直さない。"""
    hub.port.fetched_from = hub.port.repo_name
    monkeypatch.setattr(reviewed_pr, "fetch_repo", lambda g, remote: pytest.fail("取得元を読み直した"))
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt, "--reviewed-out", str(tmp_path / "h"), "--reviewed-base-out", str(tmp_path / "b"))
    assert (code, got["status"]) == (0, "ok")


def test_existing_output_not_written_by_ndf_is_kept_and_stops(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR 4638 の C4: 前の起動が書いたと記録に無い既存のファイルは消さず、相手役を起動する前に precondition で止まる。"""
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    mine = tmp_path / "mine.txt"
    mine.write_text("利用者のデータ\n", encoding="utf-8")
    code, got = _run(capsys, monkeypatch, fake, wt, "--reviewed-out", str(tmp_path / "h"), "--reviewed-base-out", str(mine))
    assert (got["cause"]["kind"], fake.calls, mine.read_text(encoding="utf-8")) == ("precondition", [], "利用者のデータ\n")


def test_findings_input_given_as_an_output_is_invalid_and_kept(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR 4638 の C4: 入力の --findings を出力先にも渡すと、読む前に消さず invalid で止まる。"""
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    given = tmp_path / "findings.json"
    given.write_text('{"items": []}', encoding="utf-8")
    code, got = _run(capsys, monkeypatch, fake, wt, "--findings", str(given), "--reviewed-out", str(given))
    assert (got["cause"]["kind"], fake.calls, given.is_file()) == ("invalid", [], True)


def test_pr_4638_r1_4_recreated_output_is_not_taken_for_the_previous_one(tmp_path: Path) -> None:
    """PR 4638 の r1-4: 消すと書いた出力の記録も消え、同じパスへ同じ中身で作り直したファイルは NDF の出力とみなさない。"""
    from ndf.domains.crossreview import outputs

    out = tmp_path / "head"
    outputs.write_output(out, "a" * 40 + "\n")
    outputs.clear_stale(out)
    assert not out.exists() and not outputs.owner_record(out).exists()
    outputs.write_output(out, "a" * 40 + "\n")
    out.unlink()
    out.write_text("a" * 40 + "\n", encoding="utf-8")
    # 時刻の刻みの粗いファイルシステムでも、後から作り直したファイルとして更新時刻を変える
    os.utime(out, ns=(1, 1))
    with pytest.raises(outputs.Unowned):
        outputs.clear_stale(out)
    assert out.read_text(encoding="utf-8") == "a" * 40 + "\n"


def test_pr_4638_r1_11_empty_output_made_in_advance_is_kept_and_written(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR 4638 の r1-11: 呼び手が先に作った空の出力先は消さずに使い、レビューした HEAD を書く。"""
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    out = tmp_path / "h"
    out.touch()
    code, got = _run(capsys, monkeypatch, fake, wt, "--reviewed-out", str(out))
    assert (code, got["status"]) == (0, "ok") and len(out.read_text(encoding="utf-8").strip()) == 40


def test_pr_4638_r1_7_base_out_checks_the_pr_head(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """PR 4638 の r1-7: `--reviewed-pr-out` が無くても、`--reviewed-base-out` の起動は PR の head がレビューするブランチの先頭と
    違えば、相手役を起動する前に precondition で止まり、比較元を書かない。"""
    port = hub.port
    monkeypatch.setattr(port, "read_pr", lambda: threads.ReadPr(f"{port.repo_name}#{PR}", "other/branch", "0" * 40, port.repo_name))
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt, "--reviewed-out", str(tmp_path / "h"), "--reviewed-base-out", str(tmp_path / "b"))
    assert (got["cause"]["kind"], fake.calls, (tmp_path / "b").exists()) == ("precondition", [], False)


def test_pr_4638_r2_4_record_location_is_checked_before_any_launch(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR 4638 の r2-4: 出力の記録の保存先に同名のディレクトリがあれば、相手役を起動する前に precondition で止まる。"""
    from ndf.domains.crossreview import outputs

    out = tmp_path / "h"
    outputs.owner_record(out).mkdir()
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt, "--reviewed-out", str(out))
    assert (got["cause"]["kind"], fake.calls, out.exists()) == ("precondition", [], False)


def test_pr_4638_r2_4_output_is_not_left_when_the_record_cannot_be_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PR 4638 の r2-4: 記録を残せなければ出力も残さない（次の起動が記録の無い出力で止まらない）。"""
    from ndf.domains.crossreview import outputs, store

    def broken(path, value):
        raise OSError("書けない")

    monkeypatch.setattr(store, "write_object", broken)
    out = tmp_path / "h"
    with pytest.raises(OSError):
        outputs.write_output(out, "a" * 40 + "\n")
    assert sorted(p.name for p in tmp_path.iterdir()) == []


def test_pr_4638_r2_5_output_of_an_earlier_version_is_cleared(tmp_path: Path) -> None:
    """PR 4638 の r2-5: 記録の無い出力は、中身が状態ファイルに残る値と同じときだけ、前のバージョンが書いた出力として消す。"""
    from ndf.domains.crossreview import outputs

    out = tmp_path / "h"
    out.write_text("a" * 40 + "\n", encoding="utf-8")
    with pytest.raises(outputs.Unowned):
        outputs.clear_stale(out, {"b" * 40 + "\n"})
    outputs.clear_stale(out, {"a" * 40 + "\n"})
    assert not out.exists()
