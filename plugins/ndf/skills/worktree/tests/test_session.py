"""セッション開始時の入口を検証する（受け入れ条件 9、11〜15）。

逸脱検知（主ディレクトリに残った変更の提示）とブランチ追従を扱う。追従に
失敗しても作業を止めないため、どの経路でも終了コードは 0 になる。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from worktree_helpers import SESSION, add_origin, git, write_declaration


def run_session(cwd: Path, session: str = "s1", tmpdir: Path | None = None) -> dict:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    if tmpdir is not None:
        env["TMPDIR"] = str(tmpdir)
    payload = {"session_id": session, "cwd": str(cwd), "hook_event_name": "SessionStart"}
    proc = subprocess.run(
        ["bash", str(SESSION)],
        input=json.dumps(payload),
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    return {"rc": proc.returncode, "out": proc.stdout, "err": proc.stderr}


def context_of(result: dict) -> str:
    """JSON の additionalContext と、素の標準出力の両方を 1 つの文字列で返す。"""
    text = result["out"]
    for line in text.splitlines():
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            text += payload.get("hookSpecificOutput", {}).get("additionalContext", "")
    return text


def declared(main_repo: Path) -> None:
    write_declaration(main_repo, json.dumps({"version": 1}))


def head_of(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").stdout.strip()


# --- 逸脱検知（受け入れ条件 9） ---------------------------------------------


def test_clean_main_dir_reports_no_change(main_repo: Path) -> None:
    declared(main_repo)
    result = run_session(main_repo)
    assert result["rc"] == 0
    assert "未コミット" not in context_of(result)


def test_uncommitted_changes_are_listed(main_repo: Path) -> None:
    declared(main_repo)
    (main_repo / "README.md").write_text("changed\n", encoding="utf-8")
    result = run_session(main_repo)
    text = context_of(result)
    assert "README.md" in text, text
    assert "1" in text, text


def test_staged_changes_are_listed(main_repo: Path) -> None:
    """`git add` 済みの変更も残った変更として数える。"""
    declared(main_repo)
    (main_repo / "README.md").write_text("changed\n", encoding="utf-8")
    git(main_repo, "add", "README.md")
    result = run_session(main_repo)
    assert "README.md" in context_of(result)


def test_untracked_files_are_not_counted(main_repo: Path) -> None:
    """追跡対象の変更だけを数える（受け入れ条件 9）。"""
    declared(main_repo)
    (main_repo / "scratch.txt").write_text("x\n", encoding="utf-8")
    result = run_session(main_repo)
    assert "scratch.txt" not in context_of(result)


def test_no_declaration_is_silent(main_repo: Path) -> None:
    (main_repo / "README.md").write_text("changed\n", encoding="utf-8")
    result = run_session(main_repo)
    assert result["out"].strip() == "", result["out"]


def test_inside_worktree_is_silent(main_repo: Path, worktree: Path) -> None:
    declared(main_repo)
    result = run_session(worktree)
    assert result["out"].strip() == "", result["out"]


# --- ブランチ追従（受け入れ条件 11〜15） ------------------------------------


def test_single_worktree_is_followed_detached(main_repo: Path, worktree: Path) -> None:
    declared(main_repo)
    git(worktree, "commit", "-q", "--allow-empty", "-m", "work")
    expected = head_of(worktree)

    run_session(main_repo)

    assert head_of(main_repo) == expected
    symbolic = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"],
        cwd=str(main_repo), capture_output=True, text=True,
    )
    assert symbolic.returncode != 0, "detached HEAD であること"


def test_two_worktrees_fall_back_to_default(main_repo: Path, worktree: Path) -> None:
    declared(main_repo)
    second = main_repo / ".worktrees" / "fix" / "y"
    git(main_repo, "worktree", "add", "-q", "-b", "fix/y", str(second))
    git(worktree, "commit", "-q", "--allow-empty", "-m", "work")
    before = head_of(main_repo)

    run_session(main_repo)

    assert head_of(main_repo) == before


def test_dirty_main_dir_is_not_followed(main_repo: Path, worktree: Path) -> None:
    declared(main_repo)
    git(worktree, "commit", "-q", "--allow-empty", "-m", "work")
    (main_repo / "README.md").write_text("changed\n", encoding="utf-8")
    before = head_of(main_repo)

    result = run_session(main_repo)

    assert head_of(main_repo) == before
    assert "README.md" in context_of(result)


def test_review_worktree_is_not_followed(main_repo: Path, tmp_path: Path) -> None:
    """レビュー用の作業ツリーへは追従しない（受け入れ条件 15）。"""
    declared(main_repo)
    outside = tmp_path / "review-worktree"
    git(main_repo, "worktree", "add", "-q", "-b", "review/z", str(outside))
    git(outside, "commit", "-q", "--allow-empty", "-m", "work")
    before = head_of(main_repo)

    run_session(main_repo)

    assert head_of(main_repo) == before


def test_broken_stdin_does_not_fail(main_repo: Path) -> None:
    proc = subprocess.run(
        ["bash", str(SESSION)],
        input="not json",
        cwd=str(main_repo),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0


# --- 出力の形（事象ごとに 1 つだけ書く） ------------------------------------


def run_session_event(cwd: Path, event: str) -> dict:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    payload = {"session_id": "e1", "cwd": str(cwd), "hook_event_name": event}
    proc = subprocess.run(
        ["bash", str(SESSION)],
        input=json.dumps(payload),
        cwd=str(cwd), env=env, capture_output=True, text=True,
    )
    return {"rc": proc.returncode, "out": proc.stdout}


def test_session_start_emits_json_only(main_repo: Path) -> None:
    """平文と JSON を同時に書くと、標準出力全体が JSON として読めなくなる。"""
    declared(main_repo)
    (main_repo / "README.md").write_text("changed\n", encoding="utf-8")
    result = run_session_event(main_repo, "SessionStart")
    payload = json.loads(result["out"])
    assert "README.md" in payload["hookSpecificOutput"]["additionalContext"]


def test_agent_spawn_emits_plain_text_only(main_repo: Path) -> None:
    """Kiro CLI は標準出力をそのまま文脈へ入れる。"""
    declared(main_repo)
    (main_repo / "README.md").write_text("changed\n", encoding="utf-8")
    result = run_session_event(main_repo, "agentSpawn")
    assert "README.md" in result["out"]
    assert "hookSpecificOutput" not in result["out"], result["out"]


def test_many_changes_are_rounded(main_repo: Path) -> None:
    """変更が多いときは先頭だけを見せ、残りは件数へ丸める。"""
    declared(main_repo)
    for i in range(25):
        path = main_repo / f"f{i:02d}.txt"
        path.write_text("x\n", encoding="utf-8")
    git(main_repo, "add", "-A")
    git(main_repo, "commit", "-q", "-m", "add files")
    for i in range(25):
        (main_repo / f"f{i:02d}.txt").write_text("y\n", encoding="utf-8")

    result = run_session(main_repo)
    text = context_of(result)
    assert "25 件" in text, text
    assert "他 5 件" in text, text


# --- 起点ブランチへの追従（issue #202） -------------------------------------


def test_declared_base_branch_is_followed(main_repo: Path) -> None:
    """稼働中の開発用作業ツリーが無いときは、宣言した起点ブランチへ合わせる。"""
    add_origin(main_repo)
    git(main_repo, "checkout", "-q", "-b", "develop")
    git(main_repo, "commit", "-q", "--allow-empty", "-m", "develop work")
    expected = head_of(main_repo)
    git(main_repo, "checkout", "-q", "main")
    write_declaration(main_repo, json.dumps({"version": 1, "base_branch": "develop"}))

    run_session(main_repo)

    assert head_of(main_repo) == expected


def test_unresolvable_base_branch_does_not_follow(main_repo: Path) -> None:
    """宣言した起点が実在しないときは、既定ブランチへ合わせずそのままにする。"""
    add_origin(main_repo)
    start = head_of(main_repo)
    git(main_repo, "commit", "-q", "--allow-empty", "-m", "more")
    git(main_repo, "checkout", "-q", "--detach", start)
    write_declaration(main_repo, json.dumps({"version": 1, "base_branch": "develop"}))

    result = run_session(main_repo)

    assert result["rc"] == 0
    assert head_of(main_repo) == start
