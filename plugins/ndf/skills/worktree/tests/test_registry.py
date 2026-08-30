"""テスト環境の採番と台帳を検証する（受け入れ条件 26 の前提）。

割り当てを解放しても行を消さず、解放の時刻を書き込む（詳細設計 06 の決定 7）。
同じ番号を別の作業ツリーが使った履歴と、外部公開の記録を残すためである。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from worktree_helpers import run_lib


def registry_path(main_repo: Path) -> Path:
    return main_repo / ".git" / "ndf" / "worktree-registry.json"


def read_registry(main_repo: Path) -> dict:
    return json.loads(registry_path(main_repo).read_text(encoding="utf-8"))


def acquire(main_repo: Path, worktree: str, branch: str = "feature/x") -> str:
    got = run_lib(
        f'wt_slot_acquire "{main_repo}" "{worktree}" "{branch}" "env-{branch}"',
        cwd=main_repo,
    )
    return got.stdout.strip()


# --- 環境名 -----------------------------------------------------------------


def test_env_name_is_deterministic(main_repo: Path) -> None:
    first = run_lib(f'wt_env_name "{main_repo}" "feature/x"', cwd=main_repo).stdout.strip()
    second = run_lib(f'wt_env_name "{main_repo}" "feature/x"', cwd=main_repo).stdout.strip()
    assert first == second
    assert first.startswith("main-wt-feature-x-"), first


def test_env_name_uses_only_lowercase_and_dashes(main_repo: Path) -> None:
    name = run_lib(f'wt_env_name "{main_repo}" "Feature/Fix_ISSUE 146"', cwd=main_repo).stdout.strip()
    assert name == name.lower()
    assert all(c.isalnum() or c == "-" for c in name), name


def test_env_name_is_capped_at_40_characters(main_repo: Path) -> None:
    long_branch = "feature/" + "a" * 80
    name = run_lib(f'wt_env_name "{main_repo}" "{long_branch}"', cwd=main_repo).stdout.strip()
    assert len(name) == 40, name


def test_env_name_differs_per_branch(main_repo: Path) -> None:
    a = run_lib(f'wt_env_name "{main_repo}" "feature/a"', cwd=main_repo).stdout.strip()
    b = run_lib(f'wt_env_name "{main_repo}" "feature/b"', cwd=main_repo).stdout.strip()
    assert a != b


# --- ポート -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("band", "slot", "role", "expected"),
    [
        (20000, 0, 0, 20000),
        (20000, 0, 1, 20001),
        (20000, 1, 0, 20020),
        (20000, 3, 6, 20066),
    ],
)
def test_port_numbering(band: int, slot: int, role: int, expected: int) -> None:
    got = run_lib(f"wt_port_for {band} {slot} {role}")
    assert got.stdout.strip() == str(expected), got.stderr


def test_port_rejects_non_numeric() -> None:
    got = run_lib("wt_port_for 20000 a 0; echo rc=$?")
    assert got.stdout.strip() == "rc=1", got.stdout


# --- 台帳 -------------------------------------------------------------------


def test_first_acquire_takes_slot_zero(main_repo: Path) -> None:
    assert acquire(main_repo, "/wt/a") == "0"


def test_same_worktree_keeps_its_slot(main_repo: Path) -> None:
    first = acquire(main_repo, "/wt/a")
    second = acquire(main_repo, "/wt/a")
    assert first == second
    assert len(read_registry(main_repo)["assignments"]) == 1


def test_second_worktree_takes_the_next_slot(main_repo: Path) -> None:
    acquire(main_repo, "/wt/a")
    assert acquire(main_repo, "/wt/b", "feature/y") == "1"


def test_release_keeps_the_row_and_records_the_time(main_repo: Path) -> None:
    acquire(main_repo, "/wt/a")
    run_lib(f'wt_slot_release "{main_repo}" "/wt/a"', cwd=main_repo)

    rows = read_registry(main_repo)["assignments"]
    assert len(rows) == 1, "行は消さない"
    assert rows[0]["released_at"] is not None, "解放の時刻を書き込む"


def test_released_slots_are_reusable(main_repo: Path) -> None:
    """空きの判定は解放済みの行を見ない。"""
    acquire(main_repo, "/wt/a")
    run_lib(f'wt_slot_release "{main_repo}" "/wt/a"', cwd=main_repo)
    assert acquire(main_repo, "/wt/b", "feature/y") == "0"


def test_reassignment_adds_a_row_and_keeps_the_past(main_repo: Path) -> None:
    acquire(main_repo, "/wt/a")
    run_lib(f'wt_slot_release "{main_repo}" "/wt/a"', cwd=main_repo)
    acquire(main_repo, "/wt/a")

    rows = read_registry(main_repo)["assignments"]
    assert len(rows) == 2, "新しい行を足す"
    assert rows[0]["released_at"] is not None, "過去の行は変わらない"
    assert rows[1]["released_at"] is None


def test_slot_of_returns_nothing_after_release(main_repo: Path) -> None:
    acquire(main_repo, "/wt/a")
    run_lib(f'wt_slot_release "{main_repo}" "/wt/a"', cwd=main_repo)
    got = run_lib(f'wt_slot_of "{main_repo}" "/wt/a"; echo rc=$?', cwd=main_repo)
    assert got.stdout.strip() == "rc=1", got.stdout


def test_registry_lives_outside_the_worktree(main_repo: Path, worktree: Path) -> None:
    """作業ツリーを消しても割り当ての記録が残るよう、共通の git ディレクトリへ置く。"""
    got = run_lib(f'wt_registry_path "{main_repo}"', cwd=worktree)
    path = Path(got.stdout.strip())
    assert path.parent.parent.name == ".git", got.stdout
    assert str(worktree) not in str(path)


def test_ports_are_recorded(main_repo: Path) -> None:
    acquire(main_repo, "/wt/a")
    run_lib(
        f"""wt_slot_set_ports "{main_repo}" "/wt/a" '{{"http":20000,"db":20001}}'""",
        cwd=main_repo,
    )
    rows = read_registry(main_repo)["assignments"]
    assert rows[0]["ports"] == {"http": 20000, "db": 20001}


def test_broken_registry_is_treated_as_empty(main_repo: Path) -> None:
    path = registry_path(main_repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    assert acquire(main_repo, "/wt/a") == "0"
