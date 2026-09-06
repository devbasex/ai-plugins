from __future__ import annotations

import shlex
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "lib" / "worktree-common.sh"


def write_targets(command: str, base: Path) -> list[str]:
    script = f"source {shlex.quote(str(SCRIPT))}; wt_extract_write_target {shlex.quote(command)} {shlex.quote(str(base))}"
    result = subprocess.run(["bash", "-lc", script], capture_output=True, text=True, timeout=10)
    if result.returncode not in (0, 1):
        raise AssertionError(result.stderr)
    return [line for line in result.stdout.splitlines() if line]


def test_write_target_tracks_cd(tmp_path: Path):
    assert write_targets("cd src && echo hi > out.txt", tmp_path) == [
        str(tmp_path / "src" / "out.txt")
    ]


def test_write_target_keeps_subshell_cd_local(tmp_path: Path):
    assert write_targets("( cd src; echo hi > out.txt ); echo root > root.txt", tmp_path) == [
        str(tmp_path / "src" / "out.txt"),
        str(tmp_path / "root.txt"),
    ]


def test_write_target_handles_and_or_exit_guard(tmp_path: Path):
    assert write_targets("cd src || exit 1; echo hi > out.txt", tmp_path) == [
        str(tmp_path / "src" / "out.txt")
    ]


def test_write_target_resets_case_branch_cwd(tmp_path: Path):
    command = "case $x in a) cd src; echo a > a.txt ;; b) echo b > b.txt ;; esac"
    assert write_targets(command, tmp_path) == [
        str(tmp_path / "src" / "a.txt"),
        str(tmp_path / "b.txt"),
    ]


def test_write_target_keeps_function_cd_from_leaking(tmp_path: Path):
    command = "move(){ cd src; echo hi > inner.txt; }; echo root > root.txt"
    assert write_targets(command, tmp_path) == [
        str(tmp_path / "inner.txt"),
        str(tmp_path / "root.txt"),
    ]


def test_write_target_ignores_heredoc_body_redirects(tmp_path: Path):
    command = "cat <<'EOF' > out.txt\nnot > target\nEOF\n"
    assert write_targets(command, tmp_path) == [str(tmp_path / "out.txt")]


def test_write_target_handles_redirect_and_fd_prefix(tmp_path: Path):
    assert write_targets("echo hi 2> err.log >> out.log", tmp_path) == [
        str(tmp_path / "err.log"),
        str(tmp_path / "out.log"),
    ]
