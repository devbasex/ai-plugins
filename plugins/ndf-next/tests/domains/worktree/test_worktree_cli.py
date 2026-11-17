"""`ndf worktree` のサブコマンドが結果 JSON を 1 行出す。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest

from ndf import cli
from ndf.contracts import result


def _run(capsys: pytest.CaptureFixture[str], last_result: Callable, *argv: str) -> tuple[int, dict]:
    code = cli.main(["worktree", *argv])
    out = last_result(capsys.readouterr().out)
    assert result.errors(out) == []
    return code, out


def test_create_outputs_context_keys(main_repo: Path, capsys: pytest.CaptureFixture[str], last_result: Callable) -> None:
    code, out = _run(capsys, last_result, "--cwd", str(main_repo), "create", "feat/a")
    assert code == 0 and out["status"] == result.OK
    assert out["outputs"] == {"worktree": str(main_repo.resolve() / ".worktrees" / "feat" / "a"), "branch": "feat/a"}
    code, out = _run(capsys, last_result, "--cwd", str(main_repo), "list")
    assert out["metrics"] == {"count": 1}


def test_create_refusal_is_stopped(main_repo: Path, capsys: pytest.CaptureFixture[str], last_result: Callable) -> None:
    code, out = _run(capsys, last_result, "--cwd", str(main_repo), "create", "bad..name")
    assert code != 0 and out["cause"]["kind"] == "invalid"


def test_outside_repository_is_precondition(tmp_path: Path, capsys: pytest.CaptureFixture[str], last_result: Callable) -> None:
    code, out = _run(capsys, last_result, "--cwd", str(tmp_path), "list")
    assert out["cause"]["kind"] == "precondition"


def test_guard_and_session(main_repo: Path, capsys: pytest.CaptureFixture[str], last_result: Callable) -> None:
    code, out = _run(capsys, last_result, "--cwd", str(main_repo), "guard", "src/a.py", "issues/x.md")
    assert code == 0 and {"path": "src/a.py"} in out["items"] and {"path": "issues/x.md"} not in out["items"]
    _, out = _run(capsys, last_result, "--cwd", str(main_repo), "guard", "issues/x.md")
    assert out["items"] == []
    _, out = _run(capsys, last_result, "--cwd", str(main_repo), "session")
    assert out["items"] == [] and out["metrics"] == {"dirty": 0, "followed": False}
    (main_repo / ".gitignore").write_text(".worktrees/\nx\n", encoding="utf-8")
    _, out = _run(capsys, last_result, "--cwd", str(main_repo), "session")
    assert out["metrics"] == {"dirty": 1, "followed": False} and "1 件" in out["items"][0]["context"]


def test_guard_inside_worktree_is_silent(main_repo: Path, capsys: pytest.CaptureFixture[str], last_result: Callable) -> None:
    _run(capsys, last_result, "--cwd", str(main_repo), "create", "feat/a")
    wt = main_repo / ".worktrees" / "feat" / "a"
    _, out = _run(capsys, last_result, "--cwd", str(wt), "guard", "--unread", str(main_repo / "src" / "a.py"))
    assert out["items"] == []


def test_broken_ndf_settings_is_precondition(main_repo: Path, capsys: pytest.CaptureFixture[str], last_result: Callable) -> None:
    (main_repo / ".ndf" / "worktree.json").write_text("{", encoding="utf-8")
    _, out = _run(capsys, last_result, "--cwd", str(main_repo), "session")
    assert out["cause"]["kind"] == "precondition"


def test_init_check_and_env_subcommands(make_repo: Callable, capsys: pytest.CaptureFixture[str], last_result: Callable) -> None:
    main = make_repo(None)
    code, out = _run(capsys, last_result, "--cwd", str(main), "check")
    assert code == 0 and out["metrics"] == {"present": False}
    code, out = _run(capsys, last_result, "--cwd", str(main), "init")
    assert code == 0 and out["items"][0]["path"].endswith("worktree.json") and "outputs" not in out
    # セクションの無い設定では環境のサブコマンドは何もしない
    for argv in (("deps", str(main)), ("localenv", "mode"), ("testenv", "compose-env")):
        code, out = _run(capsys, last_result, "--cwd", str(main), *argv)
        assert code == 0 and out["status"] == result.OK


def test_check_and_create_read_the_branch_strategy(
    main_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], last_result: Callable
) -> None:
    """check はセクションのベースブランチ・本番ブランチ・スプリントの起点を出し、create --state-dir はスナップショットを控える。"""
    code, out = _run(capsys, last_result, "--cwd", str(main_repo), "check")
    assert code == 0 and {"base": "develop", "production": "main", "sprint_start": "develop"} in out["items"]
    state = tmp_path / "state"
    code, out = _run(capsys, last_result, "--cwd", str(main_repo), "create", "sprint/s1", "--state-dir", str(state))
    assert code == 0 and out["items"][0]["snapshot"]["written"] and (state / "branch-strategy.json").is_file()


def test_create_with_legacy_keys_is_stopped(main_repo: Path, capsys: pytest.CaptureFixture[str], last_result: Callable) -> None:
    """旧いキーを置いたリポジトリでは worktree を用意せず、旧いキーの位置と移行のコマンドを出す（#3672 のテスト設計の 22）。"""
    (main_repo / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "base_branch": "develop"}), encoding="utf-8")
    code, out = _run(capsys, last_result, "--cwd", str(main_repo), "create", "sprint/s1")
    assert code != 0 and out["cause"]["kind"] == "precondition"
    assert "`.ndf/worktree.json` の `/base_branch`" in out["summary"] and "ndf project branch-strategy migrate" in out["summary"]
    assert not (main_repo / ".worktrees" / "sprint" / "s1").exists()


def test_trash_requires_approved_names(main_repo: Path, capsys: pytest.CaptureFixture[str], last_result: Callable) -> None:
    code, out = _run(capsys, last_result, "--cwd", str(main_repo), "trash", "--ref", "develop", "--yes")
    assert code != 0 and out["cause"]["kind"] == "invalid"
    code, out = _run(capsys, last_result, "--cwd", str(main_repo), "trash", "--ref", "develop")
    assert code == 0 and out["metrics"] == {"removed": 0, "candidates": 0}
