"""配布のコマンドの実行（`release-steps.py`、#893）。

一時ディレクトリに最小のリポジトリ（`git init` と `.ndf/release.json`）を作り、`--root` で渡す。
配布のコマンドは `sys.executable -c ...` で書き、シェルを通さない。
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
    assert "コマンド: 記録 → 0" in p.stdout
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
    assert p.stdout.count("コマンド: ") == 1
    assert "--released 10.17.9" in p.stdout


def _release_steps_module(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("release_steps_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "release_steps_mod", mod)
    spec.loader.exec_module(mod)
    return mod


def test_wait_and_merge_delegates_to_merge_when_green(monkeypatch):
    """配布の PR の待ちは merge-when-green に任せる（上限と取り残されたチェックの再実行を持つ）。"""
    mod = _release_steps_module(monkeypatch)
    calls = []
    out = json.dumps({"tool": "merged", "status": "ok", "summary": "#5 をマージした", "items": []})
    monkeypatch.setattr(mod, "run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, out, ""))
    monkeypatch.setattr(mod, "merge_commit_of", lambda root, n: "c")
    assert mod.wait_and_merge(".", 5) == "c"
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == sys.executable and cmd[1].endswith("merged-steps.py")
    assert cmd[2:5] == ["merge-when-green", "5", "--no-cleanup"]
    assert "--watch" not in cmd


def test_wait_and_merge_stops_with_the_summary_of_merge_when_green(monkeypatch):
    """merge-when-green が止まったら、その summary を持って止まる（上限なしで待ち続けない）。"""
    mod = _release_steps_module(monkeypatch)
    out = json.dumps({"tool": "merged", "status": "stopped",
                      "summary": "#5 の取り残されたチェックが再実行でも動かない: build", "items": []})
    monkeypatch.setattr(mod, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, out, ""))
    with pytest.raises(mod.StepError, match="取り残されたチェック"):
        mod.wait_and_merge(".", 5)


def fake_gh(tmp_path: Path, prs: dict) -> dict:
    """gh pr view N --json title,body に prs[N] を返す偽の gh を PATH の先頭へ置いた環境を返す。"""
    import os
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    data = tmp_path / "prs.json"
    data.write_text(json.dumps({str(k): v for k, v in prs.items()}, ensure_ascii=False), encoding="utf-8")
    gh = bin_dir / "gh"
    gh.write_text(f"#!{PY}\nimport json, sys\nprint(json.dumps(json.load(open({str(data)!r}))[sys.argv[3]]))\n",
                  encoding="utf-8")
    gh.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def notes_repo(repo: Path) -> Path:
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [ndf 1.2.3] - 2026-01-01\n\n- 題名 A（#11）\n- 題名 B（#12）\n\n"
                                       "## [ndf 1.2.2] - 2025-12-01\n\n- 前の版\n", encoding="utf-8")
    pdir = repo / "plugins" / "ndf"
    (pdir / ".claude-plugin").mkdir(parents=True)
    (pdir / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    (pdir / "README.md").write_text("# ndf\n\n## v1.2.3-dev.1 へ更新するとき\n\n- 題名 A（#11）\n\n## 使い方\n\n本文\n",
                                    encoding="utf-8")
    return pdir


PR_BODIES = {
    11: {"title": "題名 A", "body": "要約\n\n## 利用者向けの変化\n\n- 計画を課題番号だけで作れる\n- ステップの順が変わる\n  （続き）\n\n"
                                  "## 未検証・残る危険\n\n- 実機の CI とは未照合\n\n## テスト\n\n- ok\n"},
    12: {"title": "題名 B", "body": "節の無い本文\n"},
}


def test_notes_builds_changelog_and_readme_from_user_changes(repo, tmp_path):
    pdir = notes_repo(repo)
    env = fake_gh(tmp_path, PR_BODIES)
    p = subprocess.run([PY, str(SCRIPT), "notes", "--root", str(repo), "--version", "1.2.3-dev.1", "--prs", "11", "12"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    res = json.loads(p.stdout.strip().splitlines()[-1])
    assert res["status"] == "ok" and res["metrics"]["fallback"] == 1
    want = "- 計画を課題番号だけで作れる（#11）\n- ステップの順が変わる （続き）（#11）\n- 題名 B（#12）\n"
    cl = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [ndf 1.2.3] - 2026-01-01\n\n{want}\n## [ndf 1.2.2]" in cl
    assert "題名 A（#11）" not in cl and "- 前の版" in cl
    readme = (pdir / "README.md").read_text(encoding="utf-8")
    assert f"## v1.2.3-dev.1 へ更新するとき\n\n{want}\n## 使い方" in readme


def test_notes_fills_approval_cells(repo, tmp_path):
    notes_repo(repo)
    env = fake_gh(tmp_path, PR_BODIES)
    approval = repo / "issues" / "approval.md"
    approval.parent.mkdir()
    approval.write_text("# t\n\n## 2. 承認の判断に使うもの\n\n| 項目 | 内容 |\n| --- | --- |\n| 版数 | 1.2.3 |\n"
                        "| 配る中身 | （未記入） |\n| 検証への配布で確かめたこと | （未記入） |\n\n## 同意を求めること\n\n- [ ] x\n",
                        encoding="utf-8")
    before = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    p = subprocess.run([PY, str(SCRIPT), "notes", "--root", str(repo), "--version", "1.2.3-dev.1", "--prs", "11", "12",
                        "--approval", "issues/approval.md", "--verified", "claude,codex,kiro"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    text = approval.read_text(encoding="utf-8")
    assert "| 配る中身 | - 計画を課題番号だけで作れる（#11）<br>- ステップの順が変わる （続き）（#11）<br>- 題名 B（#12） |" in text
    assert "| 検証への配布で確かめたこと | Claude Code・Codex・Kiro の 3 経路で develop から ndf 1.2.3-dev.1 を導入し" in text
    assert "## 未検証・残る危険\n\n- 実機の CI とは未照合（#11）\n\n## 同意を求めること" in text
    assert (repo / "CHANGELOG.md").read_text(encoding="utf-8") == before


def test_notes_without_changelog_section_is_precondition(repo, tmp_path):
    notes_repo(repo)
    (repo / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    env = fake_gh(tmp_path, PR_BODIES)
    p = subprocess.run([PY, str(SCRIPT), "notes", "--root", str(repo), "--version", "1.2.3", "--prs", "12"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 3


def test_notes_and_changelog_skip_unmerged_prs(repo, tmp_path):
    pdir = notes_repo(repo)
    env = fake_gh(tmp_path, {**PR_BODIES, 13: {"title": "未マージ", "body": "## 利用者向けの変化\n\n- 載らない\n",
                                               "state": "OPEN"}})
    p = subprocess.run([PY, str(SCRIPT), "notes", "--root", str(repo), "--version", "1.2.3-dev.1", "--prs", "12", "13"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    res = json.loads(p.stdout.strip().splitlines()[-1])
    assert res["metrics"]["unmerged"] == [13] and res["metrics"]["prs"] == 1
    assert {"kind": "pr", "name": "#13", "result": "skipped", "reason": "マージされていない"} in res["items"]
    assert "#13" not in (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "#13" not in (pdir / "README.md").read_text(encoding="utf-8")
    p = subprocess.run([PY, str(SCRIPT), "changelog", "--root", str(repo), "--version", "1.2.3-dev.1", "--prs", "12", "13"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert json.loads(p.stdout.strip().splitlines()[-1])["metrics"]["unmerged"] == [13]
    assert "#13" not in (repo / "CHANGELOG.md").read_text(encoding="utf-8")


def test_notes_and_changelog_stop_when_no_pr_is_merged(repo, tmp_path):
    pdir = notes_repo(repo)
    env = fake_gh(tmp_path, {13: {"title": "未マージ", "body": "## 利用者向けの変化\n\n- 載らない\n", "state": "OPEN"}})
    approval = repo / "issues" / "approval.md"
    approval.parent.mkdir()
    approval.write_text("| 配る中身 | 前の中身 |\n| 検証への配布で確かめたこと | 前の確認 |\n", encoding="utf-8")
    files = [repo / "CHANGELOG.md", pdir / "README.md", approval]
    before = [f.read_bytes() for f in files]
    base = ["--root", str(repo), "--version", "1.2.3-dev.1", "--prs", "13"]
    for args in (["notes", *base], ["notes", *base, "--approval", "issues/approval.md"], ["changelog", *base]):
        p = subprocess.run([PY, str(SCRIPT), *args], capture_output=True, text=True, env=env)
        assert p.returncode == 3, args
        assert [f.read_bytes() for f in files] == before, args
