"""依存の用意（`worktree-deps.sh prepare` と `worktree-setup.sh create`）を検証する（#1337）。

仮のリポジトリの `vendor/bin/pint` を依存物に見立て、宣言（`.ndf/worktree.json` の `deps`）に従って
worktree から実行できるか・失敗と壊れた宣言の扱い・メインディレクトリを壊さないことを見る。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from worktree_helpers import SCRIPTS_DIR, add_origin, git, write_declaration

DEPS = SCRIPTS_DIR / "worktree-deps.sh"
SETUP = SCRIPTS_DIR / "worktree-setup.sh"
MERGED = SCRIPTS_DIR / "merged-steps.py"


def run(script: Path, args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "LC_ALL": "C.UTF-8"}
    return subprocess.run(["bash", str(script), *args], cwd=str(cwd), env=env, capture_output=True, text=True)


def declare(main: Path, deps: object | None = None, **extra: object) -> None:
    body: dict = {"version": 1, **extra}
    if deps is not None:
        body["deps"] = deps
    write_declaration(main, json.dumps(body))


@pytest.fixture()
def repo(main_repo: Path) -> Path:
    """`vendor/` と `.env` を無視し、メインディレクトリに依存物を持つリポジトリ。"""
    (main_repo / ".gitignore").write_text(".worktrees/\nvendor/\n.env\n", encoding="utf-8")
    git(main_repo, "add", ".gitignore")
    git(main_repo, "commit", "-q", "-m", "ignore")
    pint = main_repo / "vendor" / "bin" / "pint"
    pint.parent.mkdir(parents=True)
    pint.write_text("#!/bin/sh\necho pint-ok\n", encoding="utf-8")
    pint.chmod(0o755)
    (main_repo / "vendor" / "autoload.php").write_text("<?php\n", encoding="utf-8")
    (main_repo / ".env").write_text("APP=1\n", encoding="utf-8")
    add_origin(main_repo)
    return main_repo


def add_worktree(main: Path, name: str = "wt") -> Path:
    target = main / ".worktrees" / name
    git(main, "worktree", "add", "-q", "--detach", str(target), "HEAD")
    return target


def mark_of(worktree: Path) -> Path:
    gitdir = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--absolute-git-dir"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return Path(gitdir) / "ndf-deps"


def snapshot(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# --- 受け入れ条件 1: create が依存物を用意する -------------------------------


def test_create_prepares_declared_dependencies(repo: Path) -> None:
    declare(repo, {"copy_from_main": ["vendor"], "copy_as_real": [".env"]})

    r = run(SETUP, ["create", "feat/x"], repo)

    assert r.returncode == 0, r.stderr
    target = repo / ".worktrees" / "feat" / "x"
    assert r.stdout.splitlines() == [f"作業ツリー: {target}", "起点: main"]
    out = subprocess.run([str(target / "vendor" / "bin" / "pint")], capture_output=True, text=True)
    assert out.stdout.strip() == "pint-ok"
    assert "依存の用意: 済み（2 件・" in r.stderr
    # .env は実体、vendor はハードリンク
    assert (target / ".env").stat().st_ino != (repo / ".env").stat().st_ino
    assert (target / "vendor" / "autoload.php").stat().st_ino == (repo / "vendor" / "autoload.php").stat().st_ino
    assert git(target, "status", "--porcelain").stdout == ""
    assert mark_of(target).is_file()


# --- 受け入れ条件 3: 宣言が無ければ今と同じ -----------------------------------


@pytest.mark.parametrize("deps", [None, {}])
def test_create_without_deps_does_nothing_more(repo: Path, deps: object) -> None:
    declare(repo, deps)

    r = run(SETUP, ["create", "feat/x"], repo)

    assert r.returncode == 0, r.stderr
    target = repo / ".worktrees" / "feat" / "x"
    assert r.stdout.splitlines() == [f"作業ツリー: {target}", "起点: main"]
    assert r.stderr == ""
    assert not (target / "vendor").exists()
    assert not mark_of(target).exists()
    tracked = set(git(target, "ls-files").stdout.split())
    assert {str(p.relative_to(target)) for p in target.rglob("*") if p.is_file() and ".git" != p.name} == tracked


def test_prepare_without_declaration_file_is_silent(repo: Path) -> None:
    wt = add_worktree(repo)
    r = run(DEPS, ["prepare", str(wt)], repo)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    assert not mark_of(wt).exists()


# --- 受け入れ条件 4: 用意の失敗 ----------------------------------------------


def test_create_fails_when_run_fails_and_keeps_worktree(repo: Path) -> None:
    declare(repo, {"run": ["echo line-one; echo boom >&2; exit 4"]})

    r = run(SETUP, ["create", "feat/x"], repo)

    assert r.returncode == 1
    assert "依存の用意: 失敗（run[0] echo line-one; echo boom >&2; exit 4・" in r.stderr
    assert "line-one" in r.stderr and "boom" in r.stderr
    target = repo / ".worktrees" / "feat" / "x"
    assert (target / "README.md").is_file()
    assert not mark_of(target).exists()


def test_missing_source_fails(repo: Path) -> None:
    declare(repo, {"copy_from_main": ["node_modules"]})
    wt = add_worktree(repo)

    r = run(DEPS, ["prepare", str(wt)], repo)

    assert r.returncode == 1
    assert "copy_from_main[0] node_modules" in r.stderr
    assert "複製元がありません" in r.stderr
    assert r.stdout == ""


# --- 受け入れ条件 5: 壊れた宣言 -----------------------------------------------


@pytest.mark.parametrize(
    ("body", "part"),
    [
        ('{"version": 1, "deps": {"run": ["x"]', "parse error"),
        ('{"version": 1, "deps": {"run": "touch RAN"}}', "deps.run"),
        ('{"version": 1, "deps": ["vendor"]}', "deps が object"),
        ('{"version": 1, "deps": {"copy_from_main": [1]}}', "deps.copy_from_main"),
    ],
)
def test_broken_declaration_runs_nothing(repo: Path, body: str, part: str) -> None:
    path = write_declaration(repo, body)
    wt = add_worktree(repo)

    r = run(DEPS, ["prepare", str(wt)], repo)

    assert r.returncode == 3
    assert f"宣言が壊れています（{path}: " in r.stderr
    assert part in r.stderr
    assert not (wt / "RAN").exists()
    assert not (wt / "vendor").exists()


def test_create_with_broken_declaration_exits_3(repo: Path) -> None:
    write_declaration(repo, '{"version": 1, "deps": {"run": "touch RAN"}}')
    r = run(SETUP, ["create", "feat/x"], repo)
    assert r.returncode == 3
    assert not (repo / ".worktrees" / "feat" / "x" / "RAN").exists()


# --- 受け入れ条件 6: 消した後もメインディレクトリの依存物は変わらない -----------


def test_removing_prepared_worktree_keeps_main_dependencies(repo: Path) -> None:
    declare(repo, {"copy_from_main": ["vendor"], "copy_as_real": [".env", "vendor/autoload.php"]})
    before = snapshot(repo / "vendor"), (repo / ".env").read_bytes()
    a = add_worktree(repo, "a")
    b = add_worktree(repo, "b")
    assert run(DEPS, ["prepare", str(a)], repo).returncode == 0
    assert run(DEPS, ["prepare", str(b)], repo).returncode == 0
    (a / ".env").write_text("CHANGED=1\n", encoding="utf-8")
    (a / "vendor" / "autoload.php").write_text("changed\n", encoding="utf-8")

    git(repo, "worktree", "remove", "--force", str(a))
    # /ndf:merged の後片付け（force なしで試し、断られたら退避して force で外す）
    sys.path.insert(0, str(SCRIPTS_DIR))
    import importlib.util

    spec = importlib.util.spec_from_file_location("merged_steps_for_deps", MERGED)
    merged = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(merged)
    ok, why = merged.remove_worktree(str(repo), str(b), "b")

    assert ok, why
    assert (snapshot(repo / "vendor"), (repo / ".env").read_bytes()) == before


# --- 不変条件 --------------------------------------------------------------------


def test_symlink_to_outside_is_refused(repo: Path, tmp_path: Path) -> None:
    """I2: 途中の symlink をたどって外へ書かない。"""
    declare(repo, {"copy_from_main": ["vendor/bin"]})
    wt = add_worktree(repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    (wt / "vendor").symlink_to(outside)

    r = run(DEPS, ["prepare", str(wt)], repo)

    assert r.returncode == 1
    assert "copy_from_main[0] vendor/bin" in r.stderr
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize(
    "deps",
    [
        {"run": ["touch not-ignored.txt"]},
        {"run": ["echo changed >> README.md"]},
    ],
)
def test_git_state_change_fails_without_mark(repo: Path, deps: dict) -> None:
    """I3: 用意は git の状態を変えない。"""
    declare(repo, deps)
    wt = add_worktree(repo)

    r = run(DEPS, ["prepare", str(wt)], repo)

    assert r.returncode == 1
    assert "失敗（git の状態" in r.stderr
    assert not mark_of(wt).exists()


def test_if_unprepared_skips_when_marked(repo: Path, tmp_path: Path) -> None:
    """I6: 印があれば --if-unprepared は何も走らせない。引数なしはやり直す。"""
    counter = tmp_path / "count"
    declare(repo, {"run": [f"echo x >> {counter}"]})
    wt = add_worktree(repo)

    assert run(DEPS, ["prepare", str(wt), "--if-unprepared"], repo).returncode == 0
    assert run(DEPS, ["prepare", str(wt), "--if-unprepared"], repo).returncode == 0
    assert counter.read_text().count("x") == 1

    assert run(DEPS, ["prepare", str(wt)], repo).returncode == 0
    assert counter.read_text().count("x") == 2


def test_failed_redo_removes_mark(repo: Path) -> None:
    """引数なしの prepare が失敗したら印は残らない。"""
    declare(repo, {"run": ["true"]})
    wt = add_worktree(repo)
    assert run(DEPS, ["prepare", str(wt)], repo).returncode == 0
    declare(repo, {"run": ["false"]})
    assert run(DEPS, ["prepare", str(wt)], repo).returncode == 1
    assert not mark_of(wt).exists()


def test_in_place_rewrite_of_hardlink_fails(repo: Path) -> None:
    """I7: run がハードリンクをその場で書き換えたら失敗にする。"""
    declare(repo, {"copy_from_main": ["vendor"], "run": ["echo more >> vendor/autoload.php"]})
    wt = add_worktree(repo)

    r = run(DEPS, ["prepare", str(wt)], repo)

    assert r.returncode == 1
    assert "copy_from_main の書き換え vendor" in r.stderr
    assert "vendor/autoload.php" in r.stderr
    assert not mark_of(wt).exists()


def test_local_declaration_deps_is_not_applied(repo: Path) -> None:
    """I8: 個人の宣言の deps は反映せず、status の反映しない項目に出る。"""
    declare(repo)
    (repo / ".ndf" / "worktree.local.json").write_text(
        json.dumps({"version": 1, "deps": {"run": ["touch RAN"]}}), encoding="utf-8"
    )
    wt = add_worktree(repo)

    r = run(DEPS, ["prepare", str(wt)], repo)
    status = run(SETUP, ["status"], repo)

    assert r.returncode == 0
    assert not (wt / "RAN").exists()
    assert "反映しない項目: deps" in status.stdout
