"""セッション開始時の入口を検証する（受け入れ条件 9、11〜15、#610 の AC1〜AC9）。

逸脱検知（主ディレクトリに残った変更の提示）とブランチ追従を扱う。追従に
失敗しても作業を止めないため、どの経路でも終了コードは 0 になる。

**追従は既定で行わない**（#610）。並列に動くエージェントのどれが開始・再開しても
主ディレクトリの HEAD が動かないようにするためで、宣言に `follow_branch: true` を
書いたときだけ従来の追従が動く。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from worktree_helpers import (
    SESSION,
    add_origin,
    drop_remote_tracking,
    git,
    write_declaration,
)


def run_payload(cwd: Path, payload: dict, env_extra: dict | None = None) -> dict:
    """入口を `payload` の入力で実行する。終了コードは経路によらず 0 である（AC9）。"""
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        ["bash", str(SESSION)],
        input=json.dumps(payload),
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return {"rc": proc.returncode, "out": proc.stdout, "err": proc.stderr}


def run_session(cwd: Path, session: str = "s1", tmpdir: Path | None = None) -> dict:
    env_extra = {"TMPDIR": str(tmpdir)} if tmpdir is not None else None
    payload = {"session_id": session, "cwd": str(cwd), "hook_event_name": "SessionStart"}
    return run_payload(cwd, payload, env_extra)


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


def declared(main_repo: Path, follow: object = None, **extra: object) -> None:
    """宣言を書く。`follow` を渡すと `follow_branch` にその値をそのまま書く。"""
    body: dict = {"version": 1, **extra}
    if follow is not None:
        body["follow_branch"] = follow
    write_declaration(main_repo, json.dumps(body))


def head_of(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def branch_of(repo: Path) -> str:
    """ブランチ名。detached HEAD なら空文字。"""
    proc = subprocess.run(
        ["git", "symbolic-ref", "--short", "-q", "HEAD"],
        cwd=str(repo), capture_output=True, text=True,
    )
    return proc.stdout.strip()


def checkout_count(repo: Path) -> int:
    """reflog にある `checkout:` の行数。追従は必ずこの行を増やす。"""
    log = git(repo, "reflog").stdout
    return sum(1 for line in log.splitlines() if "checkout:" in line)


def position_of(repo: Path) -> tuple[str, str, int]:
    return branch_of(repo), head_of(repo), checkout_count(repo)


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
#
# 追従は `follow_branch: true` のときだけ動く（#610 の AC7）。以下は宣言にそれを足した
# だけで、期待値は変えていない。


def test_single_worktree_is_followed_detached(main_repo: Path, worktree: Path) -> None:
    declared(main_repo, True)
    git(worktree, "commit", "-q", "--allow-empty", "-m", "work")
    expected = head_of(worktree)

    run_session(main_repo)

    assert head_of(main_repo) == expected
    symbolic = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"],
        cwd=str(main_repo), capture_output=True, text=True,
    )
    assert symbolic.returncode != 0, "detached HEAD であること"


def test_already_at_target_commit_skips_checkout_and_guidance(
    main_repo: Path, worktree: Path,
) -> None:
    """既に追従先のコミットにいるときは checkout を行わず案内も出さない。"""
    declared(main_repo, True)
    git(worktree, "commit", "-q", "--allow-empty", "-m", "work")
    target = head_of(worktree)
    git(main_repo, "checkout", "-q", "--detach", target)

    before_head = head_of(main_repo)
    before_checkouts = checkout_count(main_repo)

    result = run_session(main_repo)

    assert head_of(main_repo) == before_head
    assert checkout_count(main_repo) == before_checkouts
    assert "合わせました" not in context_of(result)


def test_two_worktrees_fall_back_to_default(main_repo: Path, worktree: Path) -> None:
    declared(main_repo, True)
    second = main_repo / ".worktrees" / "fix" / "y"
    git(main_repo, "worktree", "add", "-q", "-b", "fix/y", str(second))
    git(worktree, "commit", "-q", "--allow-empty", "-m", "work")
    before = head_of(main_repo)

    run_session(main_repo)

    assert head_of(main_repo) == before


def test_two_worktrees_follow_declared_base_branch(
    main_repo: Path, worktree: Path,
) -> None:
    expected = put_develop_on_origin_only(main_repo)
    declared(main_repo, True, base_branch="develop")
    second = main_repo / ".worktrees" / "fix" / "y"
    git(main_repo, "worktree", "add", "-q", "-b", "fix/y", str(second))

    run_session(main_repo)

    assert head_of(main_repo) == expected
    assert branch_of(main_repo) == ""


def test_dirty_main_dir_is_not_followed(main_repo: Path, worktree: Path) -> None:
    declared(main_repo, True)
    git(worktree, "commit", "-q", "--allow-empty", "-m", "work")
    (main_repo / "README.md").write_text("changed\n", encoding="utf-8")
    before = head_of(main_repo)

    result = run_session(main_repo)

    assert head_of(main_repo) == before
    assert "README.md" in context_of(result)


def test_review_worktree_is_not_followed(main_repo: Path, tmp_path: Path) -> None:
    """レビュー用の作業ツリーへは追従しない（受け入れ条件 15）。"""
    declared(main_repo, True)
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
    declared(main_repo, True, base_branch="develop")

    run_session(main_repo)

    assert head_of(main_repo) == expected


def put_develop_on_origin_only(main_repo: Path) -> str:
    """origin にだけ `develop` を置き、そのコミットを返す。ローカルにブランチは残さない。

    起点を移した直後の主ディレクトリがこの形になる。既定ブランチとは別のコミットを
    指させるため、追従したかどうかが HEAD で判別できる。
    """
    add_origin(main_repo)
    git(main_repo, "checkout", "-q", "-b", "develop")
    git(main_repo, "commit", "-q", "--allow-empty", "-m", "develop work")
    expected = head_of(main_repo)
    git(main_repo, "push", "-q", "origin", "develop")
    git(main_repo, "checkout", "-q", "main")
    git(main_repo, "branch", "-D", "develop")
    return expected


def test_base_branch_without_local_branch_is_followed(main_repo: Path) -> None:
    """起点のローカルブランチが無くても、取得済みの追跡参照へ合わせる。

    名前をそのまま `git rev-parse` へ渡すと、`refs/remotes/origin/develop` へは解決されない。
    しかも失敗時に名前を標準出力へ書くため、空判定では失敗を拾えず、続く checkout が
    「合わせられませんでした」を毎回出していた。
    """
    expected = put_develop_on_origin_only(main_repo)
    declared(main_repo, True, base_branch="develop")

    result = run_session(main_repo)

    assert head_of(main_repo) == expected
    assert "合わせられませんでした" not in context_of(result), context_of(result)


def test_unfetched_base_branch_is_fetched_and_followed(main_repo: Path) -> None:
    """origin にあるだけで取得していない起点も、取得してから合わせる。"""
    expected = put_develop_on_origin_only(main_repo)
    drop_remote_tracking(main_repo, "develop")
    declared(main_repo, True, base_branch="develop")

    result = run_session(main_repo)

    assert head_of(main_repo) == expected
    assert "合わせられませんでした" not in context_of(result), context_of(result)


def test_unresolvable_base_branch_does_not_follow(main_repo: Path) -> None:
    """宣言した起点が実在しないときは、既定ブランチへ合わせずそのままにする。"""
    add_origin(main_repo)
    start = head_of(main_repo)
    git(main_repo, "commit", "-q", "--allow-empty", "-m", "more")
    git(main_repo, "checkout", "-q", "--detach", start)
    declared(main_repo, True, base_branch="develop")

    result = run_session(main_repo)

    assert result["rc"] == 0
    assert head_of(main_repo) == start


# --- 既定で追従しない（#610 の AC1〜AC6） ----------------------------------


def test_default_keeps_head_with_single_worktree(main_repo: Path, worktree: Path) -> None:
    """`follow_branch` を書かない宣言では、作業ツリーが 1 つでも HEAD も reflog も変わらない（AC1）。"""
    declared(main_repo)
    git(worktree, "commit", "-q", "--allow-empty", "-m", "work")
    before = position_of(main_repo)

    run_session(main_repo)

    assert position_of(main_repo) == before
    assert before[0] == "main"


@pytest.mark.parametrize("worktrees", [0, 2])
def test_default_keeps_detached_head(main_repo: Path, worktrees: int) -> None:
    """起点の古いコミットで detached でも、作業ツリーが 0 個でも 2 個でも動かない（AC2）。"""
    add_origin(main_repo)
    start = head_of(main_repo)
    git(main_repo, "commit", "-q", "--allow-empty", "-m", "newer")
    git(main_repo, "checkout", "-q", "--detach", start)
    for i in range(worktrees):
        git(main_repo, "worktree", "add", "-q", "-b", f"fix/w{i}",
            str(main_repo / ".worktrees" / "fix" / f"w{i}"))
    declared(main_repo)
    before = position_of(main_repo)

    run_session(main_repo)

    assert position_of(main_repo) == before
    assert before[1] == start


def test_default_never_checks_out_while_worktrees_change(main_repo: Path) -> None:
    """作業ツリーを 0 → 1 → 2 → ブランチ持ち 1 + detached 1 と変えて 4 回実行しても、
    reflog に `checkout:` が 1 行も増えない（AC3。issue の並列の事象の再現）。"""
    declared(main_repo)
    before = checkout_count(main_repo)
    wt_dir = main_repo / ".worktrees"

    run_session(main_repo)  # 0 個

    first = wt_dir / "feature" / "a"
    git(main_repo, "worktree", "add", "-q", "-b", "feature/a", str(first))
    git(first, "commit", "-q", "--allow-empty", "-m", "a")
    run_session(main_repo)  # 1 個

    second = wt_dir / "fix" / "b"
    git(main_repo, "worktree", "add", "-q", "-b", "fix/b", str(second))
    run_session(main_repo)  # 2 個

    git(main_repo, "worktree", "remove", "--force", str(second))
    git(main_repo, "worktree", "add", "-q", "--detach", str(wt_dir / "tmp"))
    run_session(main_repo)  # ブランチ持ち 1 個 + detached 1 個

    assert checkout_count(main_repo) == before
    assert branch_of(main_repo) == "main"


@pytest.mark.parametrize(
    "payload_extra",
    [
        {"invocationNum": 0, "conversationId": "c1", "initialNumSteps": 4},
        {},
    ],
    ids=["agy-PreInvocation", "no-event-name"],
)
def test_default_keeps_head_for_other_runtimes(
    main_repo: Path, worktree: Path, payload_extra: dict,
) -> None:
    """agy の通し番号 0 と、事象名を持たない入力でも動かない（AC4）。"""
    declared(main_repo)
    git(worktree, "commit", "-q", "--allow-empty", "-m", "work")
    before = position_of(main_repo)
    payload = {"cwd": str(main_repo), "workspacePaths": [str(main_repo)], **payload_extra}

    run_payload(main_repo, payload)

    assert position_of(main_repo) == before


@pytest.mark.parametrize("value", [False, "true", 1, None], ids=["false", "str-true", "one", "null"])
def test_non_boolean_true_does_not_follow(main_repo: Path, worktree: Path, value: object) -> None:
    """`follow_branch` が真偽値の true でなければ、書かないときと同じく動かない（AC5）。"""
    write_declaration(main_repo, json.dumps({"version": 1, "follow_branch": value}))
    git(worktree, "commit", "-q", "--allow-empty", "-m", "work")
    before = position_of(main_repo)

    run_session(main_repo)

    assert position_of(main_repo) == before


def test_default_does_not_contact_origin(main_repo: Path, tmp_path: Path) -> None:
    """追従しないとき、origin へ通信しない（AC6）。

    到達できない origin への `fetch` は失敗しても空の `FETCH_HEAD` を残す（実測）。ただし
    変更前でも `ls-remote` で止まり `fetch` へ届かない形があるため、`GIT_TRACE` で git の
    呼び出しを記録し、origin へ向かう副命令が 1 つも無いことも見る。
    """
    git(main_repo, "remote", "add", "origin", str(tmp_path / "unreachable" / "origin.git"))
    declared(main_repo, base_branch="develop")
    trace = tmp_path / "git-trace.log"

    run_session_traced = run_payload(
        main_repo,
        {"session_id": "s1", "cwd": str(main_repo), "hook_event_name": "SessionStart"},
        {"GIT_TRACE": str(trace)},
    )

    assert run_session_traced["rc"] == 0
    assert not (main_repo / ".git" / "FETCH_HEAD").exists()
    recorded = trace.read_text(encoding="utf-8") if trace.exists() else ""
    remote_calls = [
        line for line in recorded.splitlines()
        if any(word in line for word in ("ls-remote", "fetch", "upload-pack"))
    ]
    assert remote_calls == [], recorded


# --- 追従の有無によらず変わらないこと（#610 の AC8・AC9） -------------------


@pytest.mark.parametrize("follow", [None, True, False], ids=["unset", "true", "false"])
def test_dirty_changes_are_reported_regardless_of_follow(
    main_repo: Path, worktree: Path, follow: object,
) -> None:
    """追跡対象の未コミット変更の件数と一覧は、`follow_branch` の値によらず出る（AC8）。"""
    declared(main_repo, follow)
    git(worktree, "commit", "-q", "--allow-empty", "-m", "work")
    (main_repo / "README.md").write_text("changed\n", encoding="utf-8")
    before = head_of(main_repo)

    result = run_session(main_repo)

    text = context_of(result)
    assert "1 件" in text, text
    assert "README.md" in text, text
    assert head_of(main_repo) == before


@pytest.mark.parametrize(("follow", "noted"), [(True, True), (None, False)], ids=["true", "unset"])
def test_follow_note_is_added_only_when_follow_enabled(
    main_repo: Path, follow: object, noted: bool,
) -> None:
    """逸脱の案内に付く追従の断りは、`follow_branch: true` のときだけ出る（現状固定）。

    文言の完全一致は見ず、断りが付くか付かないかの分岐だけを固定する。
    """
    declared(main_repo, follow)
    (main_repo / "README.md").write_text("changed\n", encoding="utf-8")

    result = run_session(main_repo)

    text = context_of(result)
    assert "README.md" in text, text
    assert ("追従" in text) is noted, text
