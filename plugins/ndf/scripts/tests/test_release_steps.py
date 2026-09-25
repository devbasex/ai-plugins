"""配布の段の実行（`release-steps.py`、#893）。

一時ディレクトリに最小のリポジトリ（`git init` と `.ndf/release.json`）を作り、`--root` で渡す。
段のコマンドは `sys.executable -c ...` で書き、シェルを通さない。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "release-steps.py"
PY = sys.executable


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / "keep.txt").write_text("head\n", encoding="utf-8")
    (root / "out").mkdir()
    (root / "out" / "old.md").write_text("old\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "t")
    return root


def declare(root: Path, decl) -> None:
    path = root / ".ndf" / "release.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(decl if isinstance(decl, str) else json.dumps(decl, ensure_ascii=False), encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "decl")


def step(code: str, *, stage: str = "production", writes=("out/",), **extra) -> dict:
    return {"name": "記録", "stage": stage, "command": [PY, "-c", code], "writes": list(writes), **extra}


def run(root: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(SCRIPT), *args[:1], "--root", str(root), *args[1:]],
                          capture_output=True, text=True, cwd=str(cwd) if cwd else None)


def run_prod(root: Path, *extra: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return run(root, "run", "--stage", "production", "--version", "1.2.3", *extra, cwd=cwd)


WRITE_OUT = "import sys, pathlib; pathlib.Path('out/new.md').write_text(sys.argv[1])"


# --- AC2: 宣言が無い・段階・置き換え・作業ディレクトリ ---------------------------

def test_run_without_declaration_is_silent_and_zero(repo):
    p = run_prod(repo)
    assert (p.returncode, p.stdout, p.stderr) == (0, "", "")


def test_check_without_declaration_returns_two(repo):
    assert run(repo, "check").returncode == 2


def test_check_with_valid_declaration_returns_zero(repo):
    declare(repo, {"version": 1, "steps": [step("pass")]})
    assert run(repo, "check").returncode == 0


def test_only_matching_stage_runs_and_version_is_substituted(repo, tmp_path):
    declare(repo, {"version": 1, "steps": [
        {**step(WRITE_OUT), "command": [PY, "-c", WRITE_OUT, "{version}"]},
        step("import pathlib; pathlib.Path('out/verify.md').write_text('v')", stage="verification"),
    ]})
    p = run_prod(repo, cwd=tmp_path)  # --root と別のディレクトリから呼ぶ
    assert p.returncode == 0, p.stdout + p.stderr
    assert (repo / "out" / "new.md").read_text() == "1.2.3"
    assert not (repo / "out" / "verify.md").exists()
    assert "段: 記録 → 0" in p.stdout
    assert "out/new.md" in p.stdout


def test_any_stage_runs_on_both(repo):
    declare(repo, {"version": 1, "steps": [step("import pathlib; pathlib.Path('out/a.md').write_text('a')", stage="any")]})
    assert run(repo, "run", "--stage", "verification", "--version", "1.2.3-dev.1").returncode == 0
    assert (repo / "out" / "a.md").exists()


def test_no_matching_stage_is_zero(repo):
    declare(repo, {"version": 1, "steps": [step("raise SystemExit(5)", stage="verification")]})
    assert run_prod(repo).returncode == 0


def test_guide_is_printed_after_success(repo):
    declare(repo, {"version": 1, "steps": [step("pass", guide="docs/guide.md")]})
    p = run_prod(repo)
    assert p.returncode == 0
    assert "guide: docs/guide.md" in p.stdout


def test_step_stdout_is_passed_through(repo):
    declare(repo, {"version": 1, "steps": [step("print('差の大きい版: 無し')")]})
    assert "差の大きい版: 無し" in run_prod(repo).stdout


def test_dry_run_lists_steps_without_running(repo):
    declare(repo, {"version": 1, "steps": [{**step(WRITE_OUT), "command": [PY, "-c", WRITE_OUT, "{version}"]}]})
    p = run_prod(repo, "--dry-run")
    assert p.returncode == 0
    assert "1.2.3" in p.stdout and "{version}" not in p.stdout
    assert not (repo / "out" / "new.md").exists()


# --- AC3: 書いてよい場所 --------------------------------------------------------

def test_step_failure_stops_with_one(repo):
    declare(repo, {"version": 1, "steps": [step("raise SystemExit(4)"),
                                           step("import pathlib; pathlib.Path('out/b.md').write_text('b')")]})
    p = run_prod(repo)
    assert p.returncode == 1
    assert not (repo / "out" / "b.md").exists()


def test_timeout_is_one(repo):
    declare(repo, {"version": 1, "steps": [step("import time; time.sleep(5)", timeout_seconds=1)]})
    assert run_prod(repo).returncode == 1


def test_write_outside_is_one(repo):
    declare(repo, {"version": 1, "steps": [step("import pathlib; pathlib.Path('stray.txt').write_text('x')")]})
    p = run_prod(repo)
    assert p.returncode == 1
    assert "stray.txt" in p.stdout + p.stderr


def test_empty_writes_rejects_any_change(repo):
    declare(repo, {"version": 1, "steps": [step("import pathlib; pathlib.Path('out/c.md').write_text('c')", writes=())]})
    assert run_prod(repo).returncode == 1


def test_prefix_does_not_match_sibling_name(repo):
    declare(repo, {"version": 1, "steps": [step("import pathlib; pathlib.Path('outside.md').write_text('c')", writes=("out",))]})
    assert run_prod(repo).returncode == 1


def test_modifying_prior_change_outside_is_one(repo):
    declare(repo, {"version": 1, "steps": [step("import pathlib; pathlib.Path('keep.txt').write_text('step')")]})
    (repo / "keep.txt").write_text("dirty\n", encoding="utf-8")
    assert run_prod(repo).returncode == 1


def test_reverting_prior_change_outside_is_one(repo):
    declare(repo, {"version": 1, "steps": [step("import pathlib; pathlib.Path('keep.txt').write_text('head\\n')")]})
    (repo / "keep.txt").write_text("dirty\n", encoding="utf-8")
    assert run_prod(repo).returncode == 1


def test_untouched_prior_change_outside_is_not_counted(repo):
    declare(repo, {"version": 1, "steps": [step("import pathlib; pathlib.Path('out/d.md').write_text('d')")]})
    (repo / "keep.txt").write_text("dirty\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("u\n", encoding="utf-8")
    p = run_prod(repo)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "keep.txt" not in p.stdout


def test_deleting_outside_is_one(repo):
    declare(repo, {"version": 1, "steps": [step("import os; os.remove('keep.txt')")]})
    assert run_prod(repo).returncode == 1


# --- AC4: 宣言が読めない --------------------------------------------------------

@pytest.mark.parametrize(("decl", "item"), [
    ("{not json", "release.json"),
    ({"version": 2, "steps": []}, "version"),
    ({"version": 1}, "steps"),
    ({"version": 1, "steps": [{"name": "a", "stage": "production", "writes": []}]}, "command"),
    ({"version": 1, "steps": [{"name": "a", "stage": "production", "command": ["x"], "writes": [],
                               "timeout_seconds": 0}]}, "timeout_seconds"),
    ({"version": 1, "steps": [{"name": "a", "stage": "prod", "command": ["x"], "writes": []}]}, "stage"),
    ({"version": 1, "steps": [{"name": "a", "stage": "production", "command": ["x"], "writes": ["../up"]}]}, "writes"),
])
def test_unreadable_declaration_is_three_with_item(repo, decl, item):
    declare(repo, decl)
    for args in (("run", "--stage", "production", "--version", "1.2.3"), ("check",)):
        p = run(repo, *args)
        assert p.returncode == 3, (args, p.stdout, p.stderr)
        assert item in p.stderr


# --- AC5: このリポジトリの宣言 --------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_this_repository_declares_one_production_step():
    assert run(REPO_ROOT, "check").returncode == 0
    p = run(REPO_ROOT, "run", "--stage", "production", "--version", "10.17.9", "--dry-run")
    assert p.returncode == 0, p.stderr
    assert p.stdout.count("段: ") == 1
    assert "--released 10.17.9" in p.stdout


def test_wait_and_merge_watches_checks_with_short_interval(monkeypatch):
    """配布の PR のチェックは短い間隔で読み直し、通った後のマージまでの遅れを小さくする。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("release_steps_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "release_steps_mod", mod)
    spec.loader.exec_module(mod)
    calls, sleeps = [], []
    seq = [[], [{"name": "t", "bucket": "pending"}], [{"name": "t", "bucket": "pass"}]]
    monkeypatch.setattr(mod, "pr_check_buckets", lambda root, n: seq.pop(0) if len(seq) > 1 else seq[0])
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(mod, "run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(mod, "merge_commit_of", lambda root, n: "c")
    assert mod.wait_and_merge(".", 5) == "c"
    watch = next(c for c in calls if "--watch" in c)
    assert float(watch[watch.index("-i") + 1]) <= 5
    assert sleeps and max(sleeps) <= 5
