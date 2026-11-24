"""始める前に担当すべてを起動できるかを確かめ、できなければレビューを流さず precondition で止める（#3907）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ndf.adapters import git, training
from ndf.core.converge import location
from ndf.domains.crossreview import changes, cli, scope_decide, store
from ndf.domains.records import mvv

from ._repos import BRANCH, Repos, commit, run_cli, sh
from .test_crossreview_run import PR, Fake, _run


@pytest.fixture
def wt(repos: Repos, tmp_path: Path) -> Path:
    commit(repos.other, "doc.md", "# 設計\n\n## セクション B\n\n足した\n", "セクション B")
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    sh(repos.main, "fetch", "-q", "origin")
    return repos.add_worktree(tmp_path / "wt")


def test_runtime_that_cannot_launch_stops_before_any_review(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    refused = "学習に使わない設定と確かめられていないので codex を起動しない（確かめられなかった: トークンが無い）"
    monkeypatch.setattr(training, "ensure", lambda runtime, env=None, now=None, read=None: refused if runtime == "codex" else None)
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})

    code, got = _run(capsys, monkeypatch, fake, wt)

    assert (got["status"], got["cause"]["kind"]) == ("stopped", "precondition")
    assert refused in got["cause"]["detail"]
    assert fake.calls == []


def test_unreadable_runtime_policy_is_not_treated_as_absent(tmp_path) -> None:
    """PR 3944 の r6-3: `.ndf/runtimes.json` が無ければ制限しないが、リンク切れ・通常のファイルでないパスは設定が無いと
    みなさず、起動の前に止める。"""
    from ndf.core.converge import participants

    main = tmp_path / "main"
    (main / ".ndf").mkdir(parents=True)
    assert participants.declared_allowed(main) is None
    path = main / ".ndf" / "runtimes.json"
    path.symlink_to(tmp_path / "missing.json")
    with pytest.raises(participants.UnknownParticipant, match="リンク切れ"):
        participants.declared_allowed(main)
    path.unlink()
    path.mkdir()
    with pytest.raises(participants.UnknownParticipant, match="通常のファイルでなく"):
        participants.declared_allowed(main)
    path.rmdir()
    (main / ".ndf").rmdir()
    (main / ".ndf").symlink_to(tmp_path / "gone")
    with pytest.raises(participants.UnknownParticipant, match="リンク切れ"):
        participants.declared_allowed(main)


# --- 現状固定テスト: `run`（cli._ready）と `review-scope`（cli.cmd_scope）の止まり方と、範囲の決め方（I-007） ---


def _argv(command: str, wt: Path | str, state: Path, *extra: str) -> list[str]:
    """`run` か `review-scope` の引数。`--base` は後に書いた方が効く。"""
    common = ["--pr", str(PR), "--state-dir", str(state), "--base", "origin/main", "--purpose", "check", *extra]
    return [command, str(wt), *common] if command == "review-scope" else [command, "--worktree", str(wt), *common]


def _setting(repos: Repos, name: str, value: object) -> None:
    (repos.main / ".ndf").mkdir(exist_ok=True)
    (repos.main / ".ndf" / name).write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def no_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "LAUNCH", lambda spec: pytest.fail("担当を起動した"))


@pytest.mark.parametrize("command", ["run", "review-scope"])
@pytest.mark.parametrize(
    ("setting", "extra", "kind", "named"),
    [
        (None, ("--reviewer", "nobody"), "invalid", "相手役 nobody"),
        (("runtimes.json", {"allowed": "codex"}), (), "invalid", "runtimes.json"),
        (("review.json", {"version": 2}), (), "invalid", "version 1"),
        (("runtimes.json", {"allowed": ["codex"]}), (), "precondition", "修正担当 claude"),
    ],
    ids=["unknown_reviewer", "broken_policy", "broken_settings", "fixer_not_allowed"],
)
def test_unusable_settings_stop_run_and_scope_in_the_same_way(
    command: str, setting, extra, kind: str, named: str, repos: Repos, wt: Path, tmp_path: Path, capsys, no_launch
) -> None:
    """名指しと設定の形の誤りは invalid、修正担当を起動してよいと設定されていなければ precondition。items は無い。"""
    if setting is not None:
        _setting(repos, *setting)
    code, got = run_cli(capsys, *_argv(command, wt, tmp_path / "st", *extra))
    assert code != 0 and (got["status"], got["cause"]["kind"]) == ("stopped", kind)
    assert named in got["cause"]["detail"] and not got.get("items")


@pytest.mark.parametrize("command", ["run", "review-scope"])
def test_unknown_main_directory_stops_run_and_scope_as_a_precondition(
    command: str, wt: Path, tmp_path: Path, capsys, monkeypatch, no_launch
) -> None:
    def broken(g: git.Git) -> Path:
        raise git.GitError("common dir を読めない")

    monkeypatch.setattr(location, "main_worktree", broken)
    _, got = run_cli(capsys, *_argv(command, wt, tmp_path / "st"))
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "precondition")
    assert got["cause"]["detail"].startswith(f"{wt} のメインディレクトリを決められず")


@pytest.mark.parametrize("command", ["run", "review-scope"])
def test_unregistered_worktree_stops_run_and_scope_with_the_worktree_item(
    command: str, repos: Repos, tmp_path: Path, capsys, monkeypatch, no_launch
) -> None:
    """管理情報の無い worktree は precondition で、items に worktree を載せる。パスは `run` が絶対パス、`review-scope` は渡した形。"""
    wt = repos.add_worktree(tmp_path / "lost")
    gitdir = Path(sh(wt, "rev-parse", "--absolute-git-dir").strip())
    for name in ("HEAD", "commondir", "gitdir"):
        (gitdir / name).unlink()
    monkeypatch.chdir(tmp_path)
    _, got = run_cli(capsys, *_argv(command, "lost", tmp_path / "st"))
    path = os.path.join(Path.cwd(), "lost") if command == "run" else "lost"
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "precondition")
    assert got["cause"]["detail"].startswith(f"worktree の管理情報が無い（{path}、")
    assert got["items"] == [{"kind": "worktree", "path": path, "points_to": str(gitdir)}]


def test_malformed_handed_findings_stop_run_as_invalid(wt: Path, tmp_path: Path, capsys, no_launch) -> None:
    """足すべき内容の形の誤りも、`run` の準備では設定の誤りと同じ invalid。"""
    handed = tmp_path / "rebuild.json"
    handed.write_text(json.dumps({"items": [1]}), encoding="utf-8")
    _, got = run_cli(capsys, *_argv("run", wt, tmp_path / "st", "--findings", str(handed)))
    assert (got["cause"]["kind"], got["cause"]["detail"]) == ("invalid", "足すべき内容 0 の形が違う（文字列か辞書）")


def test_scope_reads_the_state_before_the_settings(wt: Path, tmp_path: Path, capsys) -> None:
    """状態ファイルも名指しも誤っていれば、`review-scope` は状態ファイルの誤りで止まる。"""
    path = tmp_path / "st" / store.STATE_NAME.format(pr=PR)
    path.parent.mkdir()
    path.write_text("{壊れた", encoding="utf-8")
    _, got = run_cli(capsys, *_argv("review-scope", wt, tmp_path / "st", "--reviewer", "nobody"))
    assert got["cause"]["kind"] == "invalid"
    assert path.name in got["cause"]["detail"] and "nobody" not in got["cause"]["detail"]


def test_scope_reports_wrong_settings_before_a_wrong_base(wt: Path, tmp_path: Path, capsys) -> None:
    argv = _argv("review-scope", wt, tmp_path / "st", "--base", "no-such-ref", "--reviewer", "nobody")
    _, got = run_cli(capsys, *argv)
    assert (got["cause"]["kind"], "nobody" in got["cause"]["detail"]) == ("invalid", True)


def test_scope_stops_at_a_wrong_base_before_reading_the_sections(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(changes, "changed_sections", lambda *a: pytest.fail("`--base` の誤りの前にセクションを読んだ"))
    monkeypatch.setattr(mvv, "identity_at", lambda *a: pytest.fail("`--base` の誤りの前に MVV を読んだ"))
    _, got = run_cli(capsys, *_argv("review-scope", wt, tmp_path / "st", "--base", "no-such-ref"))
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "precondition")
    assert got["cause"]["detail"].startswith("git の操作が失敗した: ") and "no-such-ref" in got["cause"]["detail"]


@pytest.mark.parametrize("rounds", [0, 1])
def test_scope_warns_of_an_unknown_mvv_only_after_the_first_round(rounds: int, wt: Path, tmp_path: Path, capsys) -> None:
    """MVV を読むブランチを決められない注意は、前のラウンドがあるときだけ載せる。範囲はどちらも全差分。"""
    sha = sh(wt, "merge-base", "origin/main", "HEAD").strip()
    path = tmp_path / "st" / store.STATE_NAME.format(pr=PR)
    done = [{"round": 1, "head": sha, "base": sha, "reviewers": ["codex"]}] * rounds
    store.write_object(path, {store.ORIGIN_KEY: PR, store.ROUNDS: done})
    code, got = run_cli(capsys, *_argv("review-scope", wt, tmp_path / "st", "--base", sha, "--reviewer", "codex"))
    assert (code, got["items"][0]["scope"]) == (0, scope_decide.FULL)
    assert got["summary"].startswith(f"round {rounds + 1} の範囲は full")
    assert len(got.get("warnings", [])) == rounds and all("MVV を読むブランチを決められない" in w for w in got.get("warnings", []))


@pytest.mark.parametrize("rounds", [0, 1])
def test_scope_warns_of_unread_sections_from_the_first_round(rounds: int, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """セクションを読めない注意は、前のラウンドが無くても載せる。設計の PR でも未レビューのセクションは並べない。"""
    path = tmp_path / "st" / store.STATE_NAME.format(pr=PR)
    store.write_object(path, {store.ORIGIN_KEY: PR, store.ROUNDS: [{"round": 1}] * rounds})

    def missing(*_: object) -> list[str]:
        raise git.GitError("promisor")

    monkeypatch.setattr(changes, "changed_sections", missing)
    code, got = run_cli(capsys, *_argv("review-scope", wt, tmp_path / "st", "--purpose", "design"))
    assert (code, len(got["warnings"])) == (0, 1)
    assert got["items"] == [{"kind": "scope", "scope": scope_decide.FULL, "base": None, "unreviewed": []}]
