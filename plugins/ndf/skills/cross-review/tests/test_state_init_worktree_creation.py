"""新規 init で worktree 作成がロールバックする経路を固定する（`_create_worktree`、現状固定）。

`origin/<head>` の取得に失敗し（フォーク PR 等）、`git worktree add --detach ... HEAD` で
一旦作成した後、`gh pr checkout --detach` によるフォールバックも失敗した場合、作成済みの
worktree を `git worktree remove --force` で取り除いてから中断する（`_create_worktree`）。

この経路は今後の worktree 作成処理の構造改善で副作用が変わりやすいため、`cmd_init` の
公開入口から通して固定する。`subprocess.run` を記録スタブに差し替え、実際の git/gh を
呼ばない。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess

import pytest

PR = 7300
REPO = "o/r"
HEAD_BRANCH = "feature/from-fork"


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod) -> pathlib.Path:
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


class _RecordingRun:
    """`subprocess.run` の呼び出しを記録し、規則に沿った結果を返すスタブ。"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []

    def __call__(self, cmd, *args, **kwargs):
        argv = list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)]
        self.calls.append(argv)
        self.kwargs.append(kwargs)

        if argv[:3] == ["gh", "api", "user"]:
            return subprocess.CompletedProcess(argv, 0, stdout="takemi", stderr="")
        if argv[:3] == ["git", "worktree", "prune"]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:3] == ["git", "fetch", "origin"]:
            # origin に head_branch が無い（フォーク PR）。
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="fatal: couldn't find remote ref")
        if argv[:3] == ["git", "worktree", "add"] and "--detach" in argv:
            # HEAD 指向の detached worktree 作成は成功する。
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:2] == ["gh", "pr"] and "checkout" in argv:
            # gh pr checkout --detach フォールバックも失敗する。
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr="gh: could not determine base repo")
        if argv[:3] == ["git", "worktree", "remove"]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if "fetch-pr-comments.sh" in argv[0]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        raise AssertionError(f"想定外の呼び出し: {argv}")


def _sh_via_recorder(recorder: _RecordingRun):
    """`_sh()` の代替。記録スタブへ委譲し、失敗時は `die` 相当で SystemExit する。"""
    def _sh(cmd, check=True):
        result = recorder(cmd)
        if check and result.returncode != 0:
            raise SystemExit(1)
        return result.stdout.strip()
    return _sh


@pytest.fixture()
def stub_init_scaffolding(monkeypatch, state_mod, tmp_path):
    """`_create_worktree` 以外の副作用をスタブし、失敗経路だけを実際に通す。"""
    worktree = tmp_path / "wt-not-created-yet"  # 存在しないパス（新規作成扱い）

    monkeypatch.setattr(
        state_mod, "_fetch_pr_metadata",
        lambda pr, repo=None: state_mod.PrMetadata(
            REPO, "takemi", HEAD_BRANCH, "abc123", "develop", True, 4000, None),
    )
    monkeypatch.setattr(state_mod, "_fetch_changed_files", lambda pr, repo: [])
    monkeypatch.setattr(state_mod, "_repo_from_git", lambda: REPO)
    monkeypatch.setenv("NDF_SKIP_AUTH_CHECK", "1")

    recorder = _RecordingRun()
    monkeypatch.setattr(state_mod.subprocess, "run", recorder)
    monkeypatch.setattr(state_mod, "_sh", _sh_via_recorder(recorder))
    return worktree, recorder


def _init_args(worktree: pathlib.Path) -> argparse.Namespace:
    return argparse.Namespace(
        pr=PR, max_rounds=12, rotate_after=8, only=None, worktree=str(worktree),
        focus=None, extra_instructions_file=None, host="claude")


def test_a_failed_fallback_checkout_removes_the_worktree_and_aborts(
        state_mod, tmp_dir, stub_init_scaffolding, capsys) -> None:
    worktree, recorder = stub_init_scaffolding

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_init(_init_args(worktree))

    assert e.value.code == 1
    remove_calls = [c for c in recorder.calls if c[:3] == ["git", "worktree", "remove"]]
    assert remove_calls == [["git", "worktree", "remove", "--force", str(worktree)]]

    err = capsys.readouterr().err
    assert "gh pr checkout --detach" in err

    # state ファイルは作られない（中断したため）。
    assert not (tmp_dir / f"cross-review-pr{PR}-state.json").exists()


def test_the_checkout_is_attempted_inside_the_worktree(
        state_mod, tmp_dir, stub_init_scaffolding) -> None:
    """`gh pr checkout --detach` は worktree 内で（cwd を伴って）実行される。"""
    worktree, recorder = stub_init_scaffolding

    with pytest.raises(SystemExit):
        state_mod.cmd_init(_init_args(worktree))

    checkout_idx = [
        i for i, c in enumerate(recorder.calls)
        if c[:2] == ["gh", "pr"] and "checkout" in c
    ]
    assert [recorder.calls[i] for i in checkout_idx] == [
        ["gh", "pr", "checkout", str(PR), "--detach"]
    ]
    assert recorder.kwargs[checkout_idx[0]].get("cwd") == str(worktree)
