"""worktree の退避先（worktree-trash）の振る舞いのテスト（#824）。

作り直せる生成物は退避せずに捨て、退避先はそのブランチを含む版が本番へ出たときに消す。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "develop")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "config", "commit.gpgsign", "false")
    (root / ".gitignore").write_text(".venv/\n.env\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    git(root, "branch", "main")
    return root


@pytest.fixture
def merged():
    return load("merged_steps", "merged-steps.py")


def worktree(repo, tmp_path, branch):
    wt = tmp_path / branch.replace("/", "_")
    git(repo, "worktree", "add", "-q", "-b", branch, str(wt))
    (wt / "work.txt").write_text(branch + "\n", encoding="utf-8")
    git(wt, "add", "work.txt")
    git(wt, "commit", "-q", "-m", branch)
    return wt


def trash_dirs(repo):
    base = repo / ".git" / "ndf" / "worktree-trash"
    return sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []


def test_generated_dirs_are_discarded_not_evacuated(repo, tmp_path, merged):
    wt = worktree(repo, tmp_path, "feat/a")
    (wt / ".venv" / "lib").mkdir(parents=True)
    (wt / ".venv" / "lib" / "site.py").write_text("x\n", encoding="utf-8")
    (wt / "dbt" / "target").mkdir(parents=True)
    (wt / "dbt" / "target" / "manifest.json").write_text("{}\n", encoding="utf-8")
    (wt / "dbt" / "dbt_project.yml").write_text("name: x\n", encoding="utf-8")
    (wt / "pkg" / "__pycache__").mkdir(parents=True)
    (wt / "pkg" / "__pycache__" / "m.pyc").write_bytes(b"\0")
    (wt / ".env").write_text("TOKEN=hand-edited\n", encoding="utf-8")

    ok, why = merged.remove_worktree(str(repo), str(wt), "feat/a")

    assert ok is True and not wt.exists()
    assert why.startswith("退避先 ") and "捨てた: " in why
    for gen in (".venv", "dbt/target", "pkg/__pycache__"):
        assert gen in why
    [trash] = trash_dirs(repo)
    assert (trash / ".env").read_text(encoding="utf-8") == "TOKEN=hand-edited\n"
    assert (trash / "dbt" / "dbt_project.yml").is_file() and not (trash / "dbt" / "target").exists()
    assert not (trash / ".venv").exists() and not (trash / "pkg").exists()
    ledger = json.loads(trash.with_name(trash.name + ".json").read_text(encoding="utf-8"))
    assert ledger["branch"] == "feat/a" and ledger["head"] == git(repo, "rev-parse", "feat/a").strip()
    assert sorted(ledger["discarded"]) == [".venv", "dbt/target", "pkg/__pycache__"]


def test_nothing_left_to_evacuate_makes_no_trash(repo, tmp_path, merged):
    wt = worktree(repo, tmp_path, "feat/b")
    (wt / "node_modules" / "x").mkdir(parents=True)
    (wt / "node_modules" / "x" / "index.js").write_text("1\n", encoding="utf-8")

    ok, why = merged.remove_worktree(str(repo), str(wt), "feat/b")

    assert ok is True and why == "退避するものは無かった（捨てた: node_modules）"
    assert trash_dirs(repo) == []


def test_a_file_named_like_a_generated_dir_is_evacuated(repo, tmp_path, merged):
    wt = worktree(repo, tmp_path, "feat/c")
    (wt / "target").write_text("not a dir\n", encoding="utf-8")
    ok, why = merged.remove_worktree(str(repo), str(wt), "feat/c")
    assert ok is True and "捨てた" not in why
    assert (Path(why[len("退避先 ") :]) / "target").is_file()


def test_a_target_without_a_build_manifest_is_evacuated(repo, tmp_path, merged):
    # 作り直しの設定が隣に無い target は生成物と決められないため、捨てずに退避する
    wt = worktree(repo, tmp_path, "feat/t")
    (wt / "target").mkdir()
    (wt / "target" / "原稿.txt").write_text("draft\n", encoding="utf-8")
    (wt / "data" / "target").mkdir(parents=True)
    (wt / "data" / "target" / "input.csv").write_text("a,b\n", encoding="utf-8")
    ok, why = merged.remove_worktree(str(repo), str(wt), "feat/t")
    assert ok is True and "捨てた" not in why
    trash = Path(why[len("退避先 ") :])
    assert (trash / "target" / "原稿.txt").is_file() and (trash / "data" / "target" / "input.csv").is_file()


def test_a_target_next_to_cargo_toml_is_discarded(repo, tmp_path, merged):
    wt = worktree(repo, tmp_path, "feat/r")
    (wt / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    git(wt, "add", "Cargo.toml")
    git(wt, "commit", "-q", "-m", "cargo")
    (wt / "target" / "debug").mkdir(parents=True)
    (wt / "target" / "debug" / "app").write_bytes(b"\0")
    ok, why = merged.remove_worktree(str(repo), str(wt), "feat/r")
    assert ok is True and why == "退避するものは無かった（捨てた: target）"


def evacuated(repo, tmp_path, merged, branch, merge_commit=None):
    wt = worktree(repo, tmp_path, branch)
    (wt / "notes.txt").write_text("keep me\n", encoding="utf-8")
    ok, why = merged.remove_worktree(str(repo), str(wt), branch, merge_commit)
    assert ok is True
    return Path(why[len("退避先 ") :])


def sweep(repo, ref, *extra):
    p = subprocess.run(
        [sys.executable, str(SCRIPTS / "merged-steps.py"), "sweep-trash", "--ref", ref, "--root", str(repo), *extra],
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_sweep_removes_only_the_trash_whose_branch_reached_production(repo, tmp_path, merged):
    released = evacuated(repo, tmp_path, merged, "feat/released")
    pending = evacuated(repo, tmp_path, merged, "feat/pending")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "release", "feat/released")

    out = sweep(repo, "main", "--yes")

    assert not released.exists() and not released.with_name(released.name + ".json").exists()
    assert pending.is_dir() and (pending / "notes.txt").is_file()
    assert out["metrics"]["swept_trash"] == 1 and out["metrics"]["kept_trash"] == 1
    assert [i["name"] for i in out["items"] if i["result"] == "removed"] == [str(released)]


def test_sweep_uses_the_merge_commit_for_a_squashed_branch(repo, tmp_path, merged):
    git(repo, "checkout", "-q", "main")
    (repo / "squashed.txt").write_text("s\n", encoding="utf-8")
    git(repo, "add", "squashed.txt")
    git(repo, "commit", "-q", "-m", "squash")
    squash = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", "develop")
    trash = evacuated(repo, tmp_path, merged, "feat/squashed", merge_commit=squash)

    out = sweep(repo, "main", "--yes")

    assert not trash.exists() and out["metrics"]["swept_trash"] == 1


def test_sweep_without_approval_only_lists_candidates(repo, tmp_path, merged):
    # 退避先は Git に無い利用者のファイルを含むため、--yes（人の承認）が無ければ消さずに候補として挙げる
    released = evacuated(repo, tmp_path, merged, "feat/released")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "release", "feat/released")

    out = sweep(repo, "main")

    assert released.is_dir() and (released / "notes.txt").is_file()
    assert out["metrics"]["swept_trash"] == 0 and out["metrics"]["sweep_candidates"] == 1
    assert [i["name"] for i in out["items"] if i["result"] == "candidate"] == [str(released)]
    assert "--yes" in out["next"]


def test_sweep_keeps_trash_without_a_ledger(repo):
    old = repo / ".git" / "ndf" / "worktree-trash" / "feat__old-20260922170128"
    old.mkdir(parents=True)
    (old / "f.txt").write_text("x\n", encoding="utf-8")

    out = sweep(repo, "main")

    assert old.is_dir() and out["metrics"]["unledgered_trash"] == 1
    assert out["items"][0]["result"] == "kept" and "feat__old-20260922170128" in out["items"][0]["reason"]


def test_sweep_rejects_an_unknown_ref(repo):
    p = subprocess.run(
        [sys.executable, str(SCRIPTS / "merged-steps.py"), "sweep-trash", "--ref", "no-such-ref", "--root", str(repo)],
        capture_output=True,
        text=True,
    )
    assert p.returncode == 2
