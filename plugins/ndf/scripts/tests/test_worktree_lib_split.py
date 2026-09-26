"""`lib/worktree-common.sh` を分けた形を固定する（#1142 の C5）。

呼び出し側は `worktree-common.sh` だけを source する。`worktree-common.sh` は同じディレクトリの
3 本を決まった順に source し、1 本でも読めなければ 1 を返す（呼び出し側は `|| exit 0` で抜ける）。
書き込み先の推定（字句解析と走査の 4 本）は、#1142 の決定 20 で hook の判定（`hook_lib/write_target.py`）へ移した。
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
COMMON = LIB / "worktree-common.sh"
PARTS = (
    "worktree-declaration.sh",
    "worktree-branch.sh",
    "worktree-registry.sh",
)

# 各ファイルが定義する最上位の関数の代表。source の後にすべて引けること。
REPRESENTATIVES = {
    "worktree-common.sh": ("wt_main_dir", "wt_normalize_path"),
    "worktree-declaration.sh": ("_wt_local_overrides", "wt_declaration"),
    "worktree-branch.sh": ("wt_dev_worktrees", "wt_base_branch", "wt_dirty_paths"),
    "worktree-registry.sh": ("wt_registry_update", "wt_lock_acquire", "wt_slot_acquire", "ndf_lock_acquire"),
}

def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("name", ("worktree-common.sh",) + PARTS)
def test_each_file_stays_within_500_lines(name: str) -> None:
    lines = (LIB / name).read_text(encoding="utf-8").count("\n")
    assert lines <= 500, f"{name} は {lines} 行"


def test_sourcing_the_common_file_defines_every_part() -> None:
    names = [n for group in REPRESENTATIVES.values() for n in group]
    probe = " ".join(names)
    got = _bash(f'. "{COMMON}" || exit 9\nfor f in {probe}; do declare -F "$f" >/dev/null || echo "missing $f"; done')
    assert got.returncode == 0, got.stderr
    assert got.stdout == "", got.stdout


def _copy_lib(dst: pathlib.Path) -> pathlib.Path:
    shutil.copytree(LIB, dst, symlinks=True)
    return dst / "worktree-common.sh"


@pytest.mark.parametrize("missing", PARTS)
def test_a_missing_part_makes_the_source_fail(tmp_path: pathlib.Path, missing: str) -> None:
    common = _copy_lib(tmp_path / "lib")
    (tmp_path / "lib" / missing).unlink()
    got = _bash(f'. "{common}" 2>/dev/null; echo "rc=$?"')
    assert "rc=1" in got.stdout, (missing, got.stdout, got.stderr)


def test_the_parts_are_found_relative_to_the_common_file(tmp_path: pathlib.Path) -> None:
    """呼び出し側の作業ディレクトリに依らず、`worktree-common.sh` の隣から読む。"""
    common = _copy_lib(tmp_path / "lib")
    got = subprocess.run(
        ["bash", "-c", f'. "{common}" || exit 9\ndeclare -F wt_slot_acquire >/dev/null && echo ok'],
        capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
    )
    assert got.stdout.strip() == "ok", (got.stdout, got.stderr)


def test_declaration_get_reads_a_filter_from_the_declaration() -> None:
    """`worktree-localenv.sh` と `worktree-testenv.sh` が別々に持っていた `decl_get` の置き換え先。"""
    got = _bash(f'. "{COMMON}" || exit 9\nwt_declaration_get \'{{"a":{{"b":"x"}}}}\' ".a.b"; echo "rc=$?"\n'
                'wt_declaration_get "not json" ".a"; echo "rc=$?"')
    assert got.stdout.split() == ["x", "rc=0", "rc=5"], got.stdout


def test_current_branch_names_the_checked_out_branch(tmp_path: pathlib.Path) -> None:
    """`target_branch` の置き換え先。detached HEAD では何も出さず 1 を返す。"""
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", "-b", "feat/x", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@e",
                    "commit", "-q", "--allow-empty", "-m", "c"], check=True)
    got = _bash(f'. "{COMMON}" || exit 9\nwt_current_branch "{repo}"; echo "rc=$?"\n'
                f'git -C "{repo}" checkout -q --detach\nwt_current_branch "{repo}"; echo "rc=$?"')
    assert got.stdout.split() == ["feat/x", "rc=0", "rc=1"], got.stdout
