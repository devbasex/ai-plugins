"""宣言ファイルの作成を検証する。

作業ツリー運用の仕組みは、リポジトリ側に宣言ファイルがあるときだけ動く。
このスクリプトだけが、宣言が無い状態で意味を持つ。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from worktree_helpers import SCRIPTS_DIR, git, write_declaration

SETUP = SCRIPTS_DIR / "worktree-setup.sh"


def run(args: list[str], cwd: Path) -> dict:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    proc = subprocess.run(
        ["bash", str(SETUP), *args],
        cwd=str(cwd), env=env, capture_output=True, text=True,
    )
    return {"rc": proc.returncode, "out": proc.stdout, "err": proc.stderr}


def declaration(main_repo: Path) -> Path:
    return main_repo / ".ndf" / "localenv.json"


def test_init_creates_a_readable_declaration(main_repo: Path) -> None:
    result = run(["init"], cwd=main_repo)

    assert result["rc"] == 0, result
    body = json.loads(declaration(main_repo).read_text(encoding="utf-8"))
    assert body["version"] == 1
    assert body["$schema"].endswith("localenv.schema.json")


def test_init_makes_the_guard_active(main_repo: Path) -> None:
    """作った直後から、主ディレクトリの編集で案内が出る。"""
    from worktree_helpers import GUARD

    run(["init"], cwd=main_repo)

    payload = {
        "session_id": "setup1",
        "cwd": str(main_repo),
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(main_repo / "plugins" / "ndf" / "README.md")},
    }
    proc = subprocess.run(
        ["bash", str(GUARD)], input=json.dumps(payload),
        cwd=str(main_repo), capture_output=True, text=True,
    )
    assert "plugins/ndf/README.md" in proc.stdout, proc.stdout


def test_init_does_not_overwrite(main_repo: Path) -> None:
    """書き加えた内容を消さない。"""
    write_declaration(
        main_repo,
        json.dumps({"version": 1, "guard": {"allow_paths": ["notes/"]}}),
    )

    result = run(["init"], cwd=main_repo)

    assert result["rc"] == 0, result
    assert "既にあります" in result["out"], result["out"]
    body = json.loads(declaration(main_repo).read_text(encoding="utf-8"))
    assert body["guard"]["allow_paths"] == ["notes/"]


def test_force_overwrites(main_repo: Path) -> None:
    write_declaration(main_repo, json.dumps({"version": 1, "guard": {"allow_paths": ["notes/"]}}))

    result = run(["init", "--force"], cwd=main_repo)

    assert result["rc"] == 0, result
    body = json.loads(declaration(main_repo).read_text(encoding="utf-8"))
    assert "guard" not in body


def test_init_runs_from_inside_a_worktree(main_repo: Path, worktree: Path) -> None:
    """作業ツリーの中から呼んでも、主ディレクトリへ置く。"""
    result = run(["init"], cwd=worktree)

    assert result["rc"] == 0, result
    assert declaration(main_repo).exists()
    assert not (worktree / ".ndf" / "localenv.json").exists()


def test_init_outside_a_repository_fails(tmp_path: Path) -> None:
    outside = tmp_path / "plain"
    outside.mkdir()
    result = run(["init"], cwd=outside)
    assert result["rc"] == 1, result


def test_status_reports_a_missing_declaration(main_repo: Path) -> None:
    result = run(["status"], cwd=main_repo)
    assert result["rc"] == 0, result
    assert "宣言ファイル: なし" in result["out"], result["out"]


def test_status_reports_a_broken_declaration(main_repo: Path) -> None:
    """読めない宣言は「なし」と区別して伝える。気づけないと直せない。"""
    write_declaration(main_repo, "{ not json")
    result = run(["status"], cwd=main_repo)
    assert "読めません" in result["out"], result["out"]


def test_status_counts_worktrees(main_repo: Path, worktree: Path) -> None:
    run(["init"], cwd=main_repo)
    result = run(["status"], cwd=main_repo)
    assert "開発用の作業ツリー: 1 個" in result["out"], result["out"]


def test_status_reports_the_gitignore_registration(main_repo: Path) -> None:
    result = run(["status"], cwd=main_repo)
    assert ".worktrees/ の登録: なし" in result["out"], result["out"]

    (main_repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    git(main_repo, "add", ".gitignore")
    git(main_repo, "commit", "-q", "-m", "ignore")

    result = run(["status"], cwd=main_repo)
    assert ".worktrees/ の登録: あり" in result["out"], result["out"]
