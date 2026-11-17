"""リリースコマンドの実行（`release-steps.py`、#893）。

一時ディレクトリに最小のリポジトリ（`git init` と `.ndf/release.json`）を作り、`--root` で渡す。
リリースコマンドは `sys.executable -c ...` で書き、シェルを通さない。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "release-steps.py"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import inproc  # noqa: E402
from release_repo import push_origin  # noqa: E402
import strategy_fixture  # noqa: E402


def plugin_release(plugin: str = "ndf") -> dict:
    """配るプラグインを持つリリースの形（ブランチ戦略の宣言の `release`）。"""
    return {"form": "package-plugin", "plugin": plugin, "runtimes": ["claude"]}


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
    # ai-plugins と同じ宣言（ブランチと配るプラグインは宣言から読む。#1336）
    strategy_fixture.declare(root, "develop", "main", plugin_release())
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
    return subprocess.run(
        [PY, str(SCRIPT), *args[:1], "--root", str(root), *args[1:]], capture_output=True, text=True, cwd=str(cwd) if cwd else None
    )


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
    declare(
        repo,
        {
            "version": 1,
            "steps": [
                {**step(WRITE_OUT), "command": [PY, "-c", WRITE_OUT, "{version}"]},
                step("import pathlib; pathlib.Path('out/verify.md').write_text('v')", stage="verification"),
            ],
        },
    )
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
    declare(repo, {"version": 1, "steps": [step("raise SystemExit(4)"), step("import pathlib; pathlib.Path('out/b.md').write_text('b')")]})
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


@pytest.mark.parametrize(
    ("decl", "item"),
    [
        ("{not json", "release.json"),
        ({"version": 2, "steps": []}, "version"),
        ({"version": 1}, "steps"),
        ({"version": 1, "steps": [{"name": "a", "stage": "production", "writes": []}]}, "command"),
        (
            {"version": 1, "steps": [{"name": "a", "stage": "production", "command": ["x"], "writes": [], "timeout_seconds": 0}]},
            "timeout_seconds",
        ),
        ({"version": 1, "steps": [{"name": "a", "stage": "prod", "command": ["x"], "writes": []}]}, "stage"),
        ({"version": 1, "steps": [{"name": "a", "stage": "production", "command": ["x"], "writes": ["../up"]}]}, "writes"),
    ],
)
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
    """デプロイの PR の待ちは merge-when-green に任せる（上限と取り残されたチェックの再実行を持つ）。"""
    mod = _release_steps_module(monkeypatch)
    monkeypatch.setattr(mod.strategy_route, "admin_bypass", lambda root: False)
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


def test_wait_and_merge_merges_only_into_the_base_branch(monkeypatch):
    """#3703 の I8: wait_and_merge はベースブランチ宛ての PR だけを入れ、本番の承認の担い手や先端の指定を渡さない（本番は prod_land）。"""
    mod = _release_steps_module(monkeypatch)
    monkeypatch.setattr(mod.strategy_route, "admin_bypass", lambda root: False)
    calls = []
    out = json.dumps({"tool": "merged", "status": "ok", "summary": "#5 をマージした", "items": []})
    monkeypatch.setattr(mod, "run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, out, ""))
    monkeypatch.setattr(mod, "merge_commit_of", lambda root, n: "c")
    mod.wait_and_merge(".", 5)
    assert "--gate-approved" not in calls[0] and "--expect-head" not in calls[0]


def test_wait_and_merge_stops_with_the_summary_of_merge_when_green(monkeypatch):
    """merge-when-green が止まったら、その summary を持って止まる（上限なしで待ち続けない）。"""
    mod = _release_steps_module(monkeypatch)
    monkeypatch.setattr(mod.strategy_route, "admin_bypass", lambda root: False)
    out = json.dumps(
        {"tool": "merged", "status": "stopped", "summary": "#5 の取り残されたチェックが再実行でも動かない: build", "items": []}
    )
    monkeypatch.setattr(mod, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, out, ""))
    with pytest.raises(mod.StepError, match="取り残されたチェック"):
        mod.wait_and_merge(".", 5)


def test_wait_and_merge_passes_the_ci_wait_as_the_timeout(monkeypatch):
    """release の --ci-wait は merge-when-green の --timeout へ渡す（ステップの timeout より先に止まる。#1645 の決定 7）。"""
    mod = _release_steps_module(monkeypatch)
    monkeypatch.setattr(mod.strategy_route, "admin_bypass", lambda root: False)
    calls = []
    out = json.dumps({"tool": "merged", "status": "ok", "summary": "#5 をマージした", "items": []})
    monkeypatch.setattr(mod, "run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, out, ""))
    monkeypatch.setattr(mod, "merge_commit_of", lambda root, n: "c")
    mod.wait_and_merge(".", 5, ci_wait=1800.0)
    assert calls[0][calls[0].index("--timeout") + 1] == "1800"


@pytest.mark.parametrize("code,expected", [(75, 75), (1, 1), (4, 1)])
def test_wait_and_merge_returns_infra_wait_as_75(monkeypatch, code, expected):
    """AC11: merge-when-green の基盤待ち（75）は 75 で、中身の失敗（1）は今のとおり 1 で止まる。
    ベースブランチとの衝突（4。#1917-8）もリリースの手順では 1 として止まる。"""
    mod = _release_steps_module(monkeypatch)
    monkeypatch.setattr(mod.strategy_route, "admin_bypass", lambda root: False)
    summary = "#5 の CI の基盤待ち: Runner が付かずに取り消された（Lint / lint）。再実行 3 回で解けない"
    out = json.dumps({"tool": "merged", "status": "stopped", "summary": summary, "items": []})
    monkeypatch.setattr(mod, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, code, out, ""))
    with pytest.raises(mod.StepError) as e:
        mod.wait_and_merge(".", 5)
    assert e.value.code == expected and str(e.value) == f"PR #5 をマージできない: {summary}"


@pytest.mark.parametrize("in_develop", [True, False])
def test_release_remakes_the_pr_when_merged_pr_misses_new_commits(monkeypatch, in_develop):
    """release/v<版> の PR がマージ済みでも、後から積んだコミットが develop に無ければ PR を作り直してマージする。"""
    import argparse

    mod = _release_steps_module(monkeypatch)
    calls = []

    def git(root, *args, check=True):
        calls.append(args)
        rc = 0 if args[0] != "merge-base" or in_develop else 1
        out = "release/v1.2.3" if args[:2] == ("rev-parse", "--abbrev-ref") else ""
        return subprocess.CompletedProcess(args, rc, out, "")

    monkeypatch.setattr(mod, "git", git)
    # develop → main・開発版のチャネルの経路を、移行の前の ai-plugins の形の写しで読む
    monkeypatch.setattr(mod, "git_root", lambda r: strategy_fixture.like_root())
    monkeypatch.setattr(mod.bump, "require_bumped", lambda root, plugin, ver: None)
    monkeypatch.setattr(mod, "find_pr", lambda root, head, base, states: {"number": 7, "state": "MERGED"})
    monkeypatch.setattr(mod, "changelog_section", lambda root, ver, plugin: "")
    monkeypatch.setattr(mod, "run_checks", lambda root: [])
    monkeypatch.setattr(mod, "create_pr", lambda *a: 9)
    monkeypatch.setattr(mod, "wait_and_merge", lambda root, n, **kw: f"new-{n}")
    monkeypatch.setattr(mod, "merge_commit_of", lambda root, n: f"old-{n}")
    monkeypatch.setattr(mod, "emit", lambda obj, *a, **k: (_ for _ in ()).throw(SystemExit(obj)))
    monkeypatch.setattr(mod.reviewed, "dev_workflow_of", lambda root: None)  # 手元でマージする経路（宣言の無いリポジトリ）
    with pytest.raises(SystemExit) as e:
        mod.cmd_release(argparse.Namespace(root=".", version="1.2.3", plugins="ndf", channel="dev", ci_wait=3600.0))
    out = e.value.code
    assert ("merge-base", "--is-ancestor", "HEAD", "origin/develop") in calls
    assert (out["metrics"]["release_pr"], out["metrics"]["merge_commit"]) == ((7, "old-7") if in_develop else (9, "new-9"))


@pytest.mark.parametrize("version", ["1.2.2", "1.2.3"])
def test_release_stops_before_the_pr_when_plugin_json_is_not_bumped(monkeypatch, repo, version):
    """#1315: plugin.json の版が今回の版でなければ、push も PR も作らずに前提の不足（3）で止まる。"""
    import argparse

    mod = _release_steps_module(monkeypatch)
    pj = repo / "plugins" / "ndf" / ".claude-plugin" / "plugin.json"
    pj.parent.mkdir(parents=True)
    pj.write_text(json.dumps({"name": "ndf", "version": version}))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "v")
    git(repo, "checkout", "-q", "-b", "release/v1.2.3")
    pushed = []
    real_git = mod.git
    monkeypatch.setattr(
        mod, "git", lambda r, *args, check=True: pushed.append(args) if args[0] == "push" else real_git(r, *args, check=check)
    )
    monkeypatch.setattr(mod, "find_pr", lambda r, head, base, states: (_ for _ in ()).throw(SystemExit("pr")))
    # 手元でマージする経路（宣言の web-develop の開発版のチャネルは workflow を持つが、ここでは起動しない）
    monkeypatch.setattr(mod.reviewed, "dev_workflow_of", lambda root: None)
    with pytest.raises((mod.StepError, SystemExit)) as e:
        mod.cmd_release(argparse.Namespace(root=str(repo), version="1.2.3", plugins=None, channel="dev", ci_wait=3600.0))
    if version == "1.2.3":
        assert e.value.args == ("pr",) and pushed
    else:
        assert e.value.code == mod.EXIT_PRECONDITION and "1.2.2" in str(e.value) and not pushed


def fake_gh(tmp_path: Path, prs: dict) -> dict:
    """gh pr view N --json title,body に prs[N] を返す偽の gh を PATH の先頭へ置いた環境を返す。"""
    import os

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    data = tmp_path / "prs.json"
    data.write_text(json.dumps({str(k): v for k, v in prs.items()}, ensure_ascii=False), encoding="utf-8")
    gh = bin_dir / "gh"
    gh.write_text(f"#!{PY}\nimport json, sys\nprint(json.dumps(json.load(open({str(data)!r}))[sys.argv[3]]))\n", encoding="utf-8")
    gh.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def notes_repo(repo: Path) -> Path:
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [ndf 1.2.3] - 2026-01-01\n\n- 題名 A（#11）\n- 題名 B（#12）\n\n## [ndf 1.2.2] - 2025-12-01\n\n- 前の版\n",
        encoding="utf-8",
    )
    pdir = repo / "plugins" / "ndf"
    (pdir / ".claude-plugin").mkdir(parents=True)
    (pdir / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    (pdir / "README.md").write_text("# ndf\n\n## v1.2.3-dev.1 へ更新するとき\n\n- 題名 A（#11）\n\n## 使い方\n\n本文\n", encoding="utf-8")
    return pdir


PR_BODIES = {
    11: {
        "title": "題名 A",
        "body": "要約\n\n## 利用者向けの変化\n\n- 計画を課題番号だけで作れる\n- ステップの順が変わる\n  （続き）\n\n"
        "## 未検証・残る危険\n\n- 実機の CI とは未照合\n\n## テスト\n\n- ok\n",
    },
    12: {"title": "題名 B", "body": "節の無い本文\n"},
}


def test_notes_builds_the_changelog_and_leaves_the_readme(repo, tmp_path):
    """I1: notes は CHANGELOG.md の版の節だけを書き、版ごとの更新の節が残る README にも触れない。"""
    pdir = notes_repo(repo)
    before = (pdir / "README.md").read_bytes()
    env = fake_gh(tmp_path, PR_BODIES)
    p = subprocess.run(
        [PY, str(SCRIPT), "notes", "--root", str(repo), "--version", "1.2.3-dev.1", "--prs", "11", "12"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    res = json.loads(p.stdout.strip().splitlines()[-1])
    assert res["status"] == "ok" and res["metrics"]["fallback"] == 1
    want = "- 計画を課題番号だけで作れる（#11）\n- ステップの順が変わる （続き）（#11）\n- 題名 B（#12）\n"
    cl = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [ndf 1.2.3] - 2026-01-01\n\n{want}\n## [ndf 1.2.2]" in cl
    assert "題名 A（#11）" not in cl and "- 前の版" in cl
    assert (pdir / "README.md").read_bytes() == before
    assert [i["name"] for i in res["items"]] == ["CHANGELOG.md"]


def test_notes_fills_approval_cells(repo, tmp_path):
    notes_repo(repo)
    env = fake_gh(tmp_path, PR_BODIES)
    approval = repo / "issues" / "approval.md"
    approval.parent.mkdir()
    approval.write_text(
        "# t\n\n## 2. 承認の判断に使うもの\n\n| 項目 | 内容 |\n| --- | --- |\n| 版数 | 1.2.3 |\n"
        "| 含む PR | #11 題名 A<br>#12 題名 B |\n| 配る中身 | （未記入） |\n| 承認前の試用で確かめたこと | （未記入） |\n\n## 同意を求めること\n\n- [ ] x\n",
        encoding="utf-8",
    )
    before = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    p = subprocess.run(
        [
            PY,
            str(SCRIPT),
            "notes",
            "--root",
            str(repo),
            "--version",
            "1.2.3-dev.1",
            "--prs",
            "11",
            "12",
            "--approval",
            "issues/approval.md",
            "--verified",
            "claude,codex,kiro",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    text = approval.read_text(encoding="utf-8")
    assert "| 配る中身 | - 計画を課題番号だけで作れる（#11）<br>- ステップの順が変わる （続き）（#11）<br>- 題名 B（#12） |" in text
    assert "| 承認前の試用で確かめたこと | Claude Code・Codex・Kiro の 3 経路で develop から ndf 1.2.3-dev.1 を導入し" in text
    assert (
        "## 未検証・残る危険\n\n- 実機の CI とは未照合（#11）\n- 無い材料: レビュー・リファクタリング・全体テスト・設計文書の未確認・未実施のリリース後テスト\n\n## 移行の手順\n\n- 無し\n\n## 同意を求めること"
        in text
    )
    assert (repo / "CHANGELOG.md").read_text(encoding="utf-8") == before


def test_notes_without_changelog_section_is_precondition(repo, tmp_path):
    notes_repo(repo)
    (repo / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    env = fake_gh(tmp_path, PR_BODIES)
    p = subprocess.run(
        [PY, str(SCRIPT), "notes", "--root", str(repo), "--version", "1.2.3", "--prs", "12"], capture_output=True, text=True, env=env
    )
    assert p.returncode == 3


def test_notes_and_changelog_skip_unmerged_prs(repo, tmp_path):
    pdir = notes_repo(repo)
    env = fake_gh(tmp_path, {**PR_BODIES, 13: {"title": "未マージ", "body": "## 利用者向けの変化\n\n- 載らない\n", "state": "OPEN"}})
    p = subprocess.run(
        [PY, str(SCRIPT), "notes", "--root", str(repo), "--version", "1.2.3-dev.1", "--prs", "12", "13"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    res = json.loads(p.stdout.strip().splitlines()[-1])
    assert res["metrics"]["unmerged"] == [13] and res["metrics"]["prs"] == 1
    assert {"kind": "pr", "name": "#13", "result": "skipped", "reason": "マージされていない"} in res["items"]
    assert "#13" not in (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "#13" not in (pdir / "README.md").read_text(encoding="utf-8")
    p = subprocess.run(
        [PY, str(SCRIPT), "changelog", "--root", str(repo), "--version", "1.2.3-dev.1", "--prs", "12", "13"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert json.loads(p.stdout.strip().splitlines()[-1])["metrics"]["unmerged"] == [13]
    assert "#13" not in (repo / "CHANGELOG.md").read_text(encoding="utf-8")


def test_notes_and_changelog_stop_when_no_pr_is_merged(repo, tmp_path):
    pdir = notes_repo(repo)
    env = fake_gh(tmp_path, {13: {"title": "未マージ", "body": "## 利用者向けの変化\n\n- 載らない\n", "state": "OPEN"}})
    approval = repo / "issues" / "approval.md"
    approval.parent.mkdir()
    approval.write_text(
        "| 含む PR | #13 未マージ |\n| 配る中身 | 前の中身 |\n| 承認前の試用で確かめたこと | 前の確認 |\n", encoding="utf-8"
    )
    files = [repo / "CHANGELOG.md", pdir / "README.md", approval]
    before = [f.read_bytes() for f in files]
    base = ["--root", str(repo), "--version", "1.2.3-dev.1", "--prs", "13"]
    for args in (["notes", *base], ["notes", *base, "--approval", "issues/approval.md"], ["changelog", *base]):
        p = inproc.run(SCRIPT, *args, env=env)
        assert p.returncode == 3, args
        assert [f.read_bytes() for f in files] == before, args


# --- GraphQL の上限で REST へ退避する・bump の打ち直し ------------------------------

RATE = "GraphQL: API rate limit already exceeded for user ID 1."


def test_pr_reads_fall_back_to_rest_when_graphql_is_rate_limited(monkeypatch, tmp_path):
    mod = _release_steps_module(monkeypatch)
    calls = []

    def runner(args, stdin=None, cwd=None):
        if args[:2] in (["pr", "view"], ["api", "graphql"]):
            return mod.gh_parts.GhResult(1, "", RATE)
        calls.append((args, cwd))
        n = int(args[1].rsplit("/", 1)[1])
        return mod.gh_parts.GhResult(
            0,
            json.dumps(
                {
                    "number": n,
                    "title": f"題 {n}",
                    "body": "## 利用者向けの変化\n\n- 変わる\n",
                    "state": "closed",
                    "merged_at": "2026-09-26T00:00:00Z" if n == 5 else None,
                    "merge_commit_sha": "abc",
                    "html_url": f"https://github.com/o/r/pull/{n}",
                }
            ),
            "",
        )

    monkeypatch.setattr(mod.gh_parts.gh_call, "RUNNER", runner)
    skipped = []
    assert mod.pr_titles(tmp_path, [5, 6], skipped) == [(5, "- 題 5（#5）")]
    assert skipped == [6]
    assert mod.merge_commit_of(tmp_path, 5) == "abc"
    assert mod.merge_commit_of(tmp_path, 6) is None
    assert mod.pr_notes(tmp_path, [5])[0][1] == ["変わる（#5）"]
    assert calls[0] == (["api", "repos/{owner}/{repo}/pulls/5"], str(tmp_path))


def test_pr_read_failure_other_than_rate_limit_stops(monkeypatch, tmp_path):
    mod = _release_steps_module(monkeypatch)
    monkeypatch.setattr(mod.gh_parts.gh_call, "RUNNER", lambda args, stdin=None, cwd=None: mod.gh_parts.GhResult(1, "", "not found"))
    with pytest.raises(mod.StepError, match="gh pr view 5 が失敗: not found"):
        mod.pr_titles(tmp_path, [5])


@pytest.mark.parametrize("current, pushed", [("release/v1.0.1", 1), ("develop", 0)])
def test_create_pr_restores_head_deleted_after_merge(monkeypatch, tmp_path, current, pushed):
    """開発版の PR のマージの後に origin が遅れて head を消したら、作業ツリーのブランチに限って push し直して作り直す。"""
    mod = _release_steps_module(monkeypatch)
    tries, restored = [], []
    gone = "pull request create failed: GraphQL: Head sha can't be blank, Head ref must be a branch (createPullRequest)"

    def gh(args, cwd=None):
        tries.append(args)
        ok = len(restored) > 0
        return subprocess.CompletedProcess(args, 0 if ok else 1, "https://github.com/o/r/pull/7\n" if ok else "", "" if ok else gone)

    monkeypatch.setattr(mod.gh_parts, "gh", gh)
    monkeypatch.setattr(mod, "git", lambda r, *a, check=True: subprocess.CompletedProcess(a, 0, current + "\n", ""))
    monkeypatch.setattr(mod.repo_lib, "restore_branch", lambda r, b, run_git=None: restored.append(b) or True)
    if pushed:
        assert mod.create_pr(tmp_path, "main", "release/v1.0.1", "t", "b") == 7
    else:
        with pytest.raises(mod.StepError, match="Head ref must be a branch"):
            mod.create_pr(tmp_path, "main", "release/v1.0.1", "t", "b")
    assert (len(restored), len(tries)) == (pushed, 1 + pushed)


def bump_repo(root: Path, base_version: str, work_version: str) -> None:
    f = root / "plugins" / "ndf" / ".claude-plugin" / "plugin.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"version": base_version}), encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    push_origin(root)
    if work_version != base_version:
        f.write_text(json.dumps({"version": work_version}), encoding="utf-8")
        git(root, "commit", "-q", "-am", "bump")


def test_bump_rerun_after_the_version_was_raised_is_ok(repo):
    bump_repo(repo, "1.2.3", "1.2.4")
    p = run(repo, "bump", "--plugin", "ndf", "--to", "1.2.4")
    assert p.returncode == 0, p.stdout + p.stderr
    res = json.loads(p.stdout.strip().splitlines()[-1])
    assert res["status"] == "ok" and res["metrics"]["from"] == "1.2.3" and res["items"] == []


def fake_gh_list(tmp: Path, out: str, code: int = 0) -> dict:
    """`gh pr list` の答えを固定する偽の gh を PATH の先頭に置いた環境（自分のデプロイの照会。#2164）。"""
    d = tmp / "fakebin"
    d.mkdir(exist_ok=True)
    (d / "gh").write_text(f"#!/bin/sh\nprintf '%s' '{out}'\nexit {code}\n")
    (d / "gh").chmod(0o755)
    return {**os.environ, "PATH": f"{d}:{os.environ['PATH']}"}


def run_env(root: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(SCRIPT), *args[:1], "--root", str(root), *args[1:]], capture_output=True, text=True, env=env)


def last_json(p: subprocess.CompletedProcess) -> dict:
    return json.loads(p.stdout.strip().splitlines()[-1])


def manifest(root: Path) -> str:
    return (root / "plugins/ndf/.claude-plugin/plugin.json").read_text(encoding="utf-8")


def test_bump_below_or_at_the_base_stops_without_writing(repo):
    """ベースブランチが 10.27.5 のとき 10.27.5-dev.1 へは上げない（版を戻さない。受け入れ条件 5）。"""
    bump_repo(repo, "10.27.5", "10.27.5")
    before = manifest(repo)
    p = run(repo, "bump", "--plugin", "ndf", "--to", "10.27.5-dev.1")
    res = last_json(p)
    assert p.returncode == 3 and res["status"] == "stopped", p.stdout
    assert res["metrics"]["existing"] == "10.27.5" and res["metrics"]["next_free"] == "10.27.6-dev.1"
    assert manifest(repo) == before


def test_bump_to_a_release_with_a_tag_stops(repo):
    bump_repo(repo, "1.2.3", "1.2.3")
    git(repo, "tag", "ndf--v1.2.4")
    push_origin(repo)
    p = run(repo, "bump", "--plugin", "ndf", "--to", "1.2.4")
    res = last_json(p)
    assert p.returncode == 3 and res["metrics"]["existing"] == "ndf--v1.2.4" and res["metrics"]["next_free"] == "1.2.5"


def test_bump_to_a_release_below_the_base_without_a_tag_stops(repo):
    """現状固定: タグの無い正式版でもベースブランチの版以下なら止め、次に空いている版はベースブランチの次のパッチ。"""
    bump_repo(repo, "1.2.5", "1.2.5")
    p = run(repo, "bump", "--plugin", "ndf", "--to", "1.2.4")
    res = last_json(p)
    assert p.returncode == 3 and res["metrics"]["existing"] == "1.2.5" and res["metrics"]["next_free"] == "1.2.6", p.stdout


def test_bump_at_the_base_is_a_rerun_only_after_its_own_release(repo, tmp_path):
    """作業場所とベースブランチがともに新しい版なら、release/v<版> のマージ済みの PR があるときだけ打ち直し（決定 2）。"""
    bump_repo(repo, "1.2.4", "1.2.4")
    own = run_env(repo, fake_gh_list(tmp_path, '[{"number": 7}]'), "bump", "--plugin", "ndf", "--to", "1.2.4")
    assert own.returncode == 0 and last_json(own)["metrics"]["already"] is True, own.stdout
    other = run_env(repo, fake_gh_list(tmp_path, "[]"), "bump", "--plugin", "ndf", "--to", "1.2.4")
    assert other.returncode == 3 and last_json(other)["metrics"]["existing"] == "1.2.4"
    unknown = run_env(repo, fake_gh_list(tmp_path, "", 1), "bump", "--plugin", "ndf", "--to", "1.2.4")
    assert unknown.returncode == 2 and last_json(unknown)["metrics"]["unreadable"] == ["pr"]


def test_bump_without_origin_stops_as_unreadable(repo):
    bump_repo(repo, "1.2.3", "1.2.3")
    git(repo, "remote", "remove", "origin")
    before = manifest(repo)
    p = run(repo, "bump", "--plugin", "ndf", "--to", "1.2.4")
    assert p.returncode == 2 and "origin" in last_json(p)["metrics"]["unreadable"]
    assert manifest(repo) == before


def test_bump_leaves_a_line_repeated_elsewhere_to_the_hand(repo):
    """書き換えは bump-my-version がファイルの中の同じ字面をすべて直すため、集めた行と同じ字面がほかにもあれば
    書き換えずに manual へ回す（行ごとに直していた頃は最初の 1 行だけを直した）。"""
    bump_repo(repo, "1.2.3", "1.2.3")
    plugin_json(repo, "plugins/ndf", "1.2.3")
    (repo / "README.md").write_text("| **ndf** | 1.2.3 |\n\n| **ndf** | 1.2.3 |\n", encoding="utf-8")
    p = run(repo, "bump", "--plugin", "ndf", "--to", "1.2.4")
    res = json.loads(p.stdout.strip().splitlines()[-1])
    assert any(i["result"] == "manual" and "プラグイン一覧表" in i["name"] for i in res["items"]), res
    assert (repo / "README.md").read_text(encoding="utf-8") == "| **ndf** | 1.2.3 |\n\n| **ndf** | 1.2.3 |\n"
    got = json.loads((repo / "plugins/ndf/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
    assert got["version"] == "1.2.4"


# --- changed-plugins（#1142 の不足 c） ---------------------------------------------------


def plugin_json(root: Path, rel: str, version: str) -> None:
    f = root / rel / ".claude-plugin" / "plugin.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"version": version}, indent=2), encoding="utf-8")


def changed_repo(root: Path) -> Path:
    """ndf・mcp-serena・mcp-codex を持ち、ndf--v1.0.0 のタグの後に mcp-serena だけを変えたリポジトリ。"""
    plugin_json(root, "plugins/ndf", "1.0.0")
    plugin_json(root, "plugins/mcp/mcp-serena", "2.3.4")
    plugin_json(root, "plugins/mcp/mcp-codex", "0.1.0")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    git(root, "tag", "ndf--v1.0.0")
    git(root, "tag", "ndf--v1.0.1-dev.1")  # 接尾辞のあるタグは前の本番に数えない
    (root / "plugins" / "mcp" / "mcp-serena" / "README.md").write_text("changed\n", encoding="utf-8")
    (root / "plugins" / "mcp" / "README.md").write_text("index\n", encoding="utf-8")  # プラグインでない
    (root / "plugins" / "ndf" / "x.py").write_text("x\n", encoding="utf-8")  # ndf は items に入れない
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "change serena")
    return root


def changed(root: Path, *extra: str) -> tuple[int, dict]:
    p = run(root, "changed-plugins", *extra)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("extra", [("--since", "ndf--v1.0.0"), ()])
def test_changed_plugins_lists_only_other_plugins_changed_since_the_tag(repo, extra):
    code, res = changed(changed_repo(repo), *extra)
    assert code == 0 and res["status"] == "ok", res
    assert [(i["name"], i["from"], i["to"]) for i in res["items"]] == [("mcp-serena", "2.3.4", "2.3.5")]
    assert res["metrics"]["since"] == "ndf--v1.0.0"


def test_changed_plugins_skips_a_plugin_already_bumped_in_the_diff(repo):
    root = changed_repo(repo)
    plugin_json(root, "plugins/mcp/mcp-serena", "2.3.5")
    git(root, "commit", "-q", "-am", "bump serena")
    code, res = changed(root, "--since", "ndf--v1.0.0")
    assert code == 0 and res["items"] == [] and res["metrics"]["already"] == ["mcp-serena"]


def test_changed_plugins_without_the_tag_is_two(repo):
    code, res = changed(changed_repo(repo), "--since", "ndf--v9.9.9")
    assert code == 2 and res["status"] == "stopped"
    git(repo, "tag", "-d", "ndf--v1.0.0", "ndf--v1.0.1-dev.1")
    code, res = changed(repo)  # 本番のタグが 1 つも無い
    assert code == 2 and "ndf--v" in res["summary"]


@pytest.mark.parametrize("stale_tag", [False, True])
def test_changed_plugins_without_tags_starts_from_the_last_release_bump(repo, monkeypatch, stale_tag):
    """#3672 の round 11: タグを作らない宣言（CD の無い package-plugin は release.tag が null）では、本番ブランチの版を
    初めて持ったコミット（前のリリースの版上げ）を差分の起点にし、存在しないタグで止まらない。round 12: タグを作っていた
    頃の古いタグが残っていても、そのタグを起点にしない（版上げを「上げ済み」と数えて抜かさない）。"""
    mod = _release_steps_module(monkeypatch)  # lib/ を sys.path へ入れる
    from release_lib import publish

    if stale_tag:
        git(repo, "tag", "ndf--v0.9.0")  # タグ無しの宣言へ変える前に作ったタグ
    plugin_json(repo, "plugins/ndf", "1.0.0")
    plugin_json(repo, "plugins/mcp/mcp-serena", "2.3.4")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "release 1.0.0")
    shipped = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    git(repo, "update-ref", "refs/remotes/origin/main", shipped)  # 本番ブランチは前のリリース
    (repo / "plugins" / "mcp" / "mcp-serena" / "README.md").write_text("changed\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "change serena")
    plugin_json(repo, "plugins/ndf", "1.0.1")  # この版の bump の後に bump-others が走る
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "bump ndf")
    assert publish.untagged_since(repo, "ndf", "HEAD") == shipped
    assert mod.prev_release(repo, "ndf", "HEAD") == shipped  # approval-facts の比較元も同じ起点
    code, res = changed(repo)
    assert code == 0 and res["metrics"]["since"] == shipped, res
    assert [it["name"] for it in res["items"]] == ["mcp-serena"]


def test_untagged_record_and_ranges_read_versions_not_stale_tags(repo, monkeypatch):
    """#3672 の round 13: タグを作らない宣言では、残った古いタグでなく版から、版の節の PR の範囲の起点（前のリリースの版上げ）と
    リリース記録の直前の版（接尾辞の無い前の正式版）を決める。プラグインのマニフェストが無ければどちらも決めない。"""
    mod = _release_steps_module(monkeypatch)
    from release_lib import publish

    git(repo, "tag", "ndf--v0.9.0")  # タグ無しの宣言へ変える前に作ったタグ
    commits = []
    for ver in ("1.0.0", "1.0.1-dev.1", "1.0.1"):
        plugin_json(repo, "plugins/ndf", ver)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", ver)
        commits.append(subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip())
    git(repo, "update-ref", "refs/remotes/origin/main", commits[0])  # 本番ブランチは前のリリース
    assert mod.prev_release(repo, "ndf", "HEAD", "ndf--v1.0.1") == commits[0]
    git(repo, "update-ref", "refs/remotes/origin/main", commits[2])  # 1.0.1 を出した後
    assert publish.untagged_before(repo, "ndf", "1.0.1") == "1.0.0"
    # round 15: 出した後の導入の確かめは、確かめる版を除いた直前の正式版の版上げを起点にする（今回の版上げを起点にしない）
    assert publish.untagged_since(repo, "ndf", "HEAD") == commits[2]
    assert publish.untagged_since(repo, "ndf", "HEAD", exclude="1.0.1") == commits[0]
    import release_prev

    monkeypatch.setattr(release_prev, "_BATCH", 2)  # 版をまとめて読む単位をまたいでも同じ答え
    assert publish.untagged_since(repo, "ndf", "HEAD", exclude="1.0.1") == commits[0]
    assert publish.untagged_before(repo, "ndf", "1.0.1") == "1.0.0"
    assert publish.untagged_since(repo, "nothing", "HEAD") is None and publish.untagged_before(repo, "nothing", "1.0.1") is None


def test_untagged_changelog_and_guards_read_the_shipped_version(repo, monkeypatch):
    """#3701 の round 15: タグを作らない宣言では、版の節の消し方（前のリリースの起点が版上げのコミット）と、出した版へ書かない・
    出し直さない確かめ（タグの有無の代わりに origin の本番ブランチの版）も版から決める。手元の本番ブランチの版は数えない。"""
    _release_steps_module(monkeypatch)
    from release_lib import names, publish
    from step_result import StepError

    git(repo, "tag", "ndf--v0.9.0")  # タグ無しの宣言へ変える前に作ったタグ
    plugin_json(repo, "plugins/ndf", "1.0.0")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "release 1.0.0")
    shipped = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    git(repo, "update-ref", "refs/heads/main", shipped)  # 手元の本番ブランチだけ: 出済みの版に数えない
    assert names.shipped_note(repo, "ndf", "1.0.0") is None
    git(repo, "update-ref", "refs/remotes/origin/main", shipped)  # origin の本番ブランチへ 1.0.0 を出した
    assert publish.prev_release(repo, "ndf", "HEAD", "ndf--v1.0.3") == shipped
    lines = "# C\n\n## [ndf 1.0.2]\n\n- b\n\n## [ndf 1.0.1]\n\n- a\n\n## [ndf 1.0.0]\n\n- 前の版\n\n## [ndf 0.9.0]\n\n- 古い版\n".split(
        "\n"
    )
    gone = names.drop_untagged(repo, lines, "ndf", shipped, "1.0.3")
    assert [g["heading"] for g in gone] == ["## [ndf 1.0.2]", "## [ndf 1.0.1]"]
    assert "## [ndf 1.0.0]" in lines and "## [ndf 0.9.0]" in lines
    assert "1.0.0" in names.shipped_note(repo, "ndf", "1.0.0-dev.2") and "1.0.0" in names.shipped_note(repo, "ndf", "0.9.5")
    assert names.shipped_note(repo, "ndf", "1.0.1") is None
    with pytest.raises(StepError) as e:
        names.require_unreleased(repo, "ndf", "1.0.0")
    assert e.value.code == 3 and "本番ブランチの版 1.0.0" in str(e.value)
    names.require_unreleased(repo, "ndf", "1.0.1-dev.1")  # まだ出ていない版の節へは書く


@pytest.mark.parametrize(
    ("release", "made"),
    [({"tag": None}, ""), ({"tag": "v{version}", "github_release": False}, "タグ v1"), ({}, "タグ v1 と GitHub Release")],
)
def test_approval_names_only_the_artifacts_the_declaration_makes(monkeypatch, release, made):
    """#3672 の round 13: 承認資料の同意と戻し方は、宣言が作らせる公開物（publish.wants）だけを書く。"""
    _release_steps_module(monkeypatch)
    from release_lib import publish

    assert publish.made_by(SimpleNamespace(release=release), "v1") == made


def v_tag_repo(root: Path) -> Path:
    """宣言の release.tag が `v{version}` のリポジトリ（#3672 の round 9）。タグは v1.0.0・v1.1.0・接尾辞つきの v1.2.0-dev.1。"""
    release = {**plugin_release(), "tag": "v{version}", "github_release": True, "cd": {"workflow": "release.yml"}}
    strategy_fixture.declare(root, "develop", "main", release)
    plugin_json(root, "plugins/ndf", "1.0.0")
    plugin_json(root, "plugins/mcp/mcp-serena", "2.3.4")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    git(root, "tag", "v1.0.0")
    git(root, "tag", "ndf--v1.0.5")  # 宣言の形に合わないタグは前の版に数えない
    git(root, "commit", "-q", "--allow-empty", "-m", "next")
    git(root, "tag", "v1.1.0")
    git(root, "tag", "v1.2.0-dev.1")
    (root / "plugins" / "mcp" / "mcp-serena" / "README.md").write_text("changed\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "change serena")
    return root


def test_release_tags_follow_the_declared_shape(repo, monkeypatch):
    """#3672 の round 9: タグの名前と検索の形は宣言の release.tag から引き、前版の検索・公開・記録が同じ形を使う。"""
    root = v_tag_repo(repo)
    mod = _release_steps_module(monkeypatch)
    from release_lib import names, publish, record

    assert names.tag_name(root, "ndf", "1.2.0-dev.1") == "v1.2.0"
    assert mod.release_tag_before(root, "ndf") == "v1.1.0"
    assert mod.release_tag_before(root, "ndf", current="v1.1.0") == "v1.0.0"
    # 前のタグを渡さない changed-plugins も宣言の形で前版を引く
    code, res = changed(root)
    assert code == 0 and res["metrics"]["since"] == "v1.1.0", res
    assert [i["name"] for i in res["items"]] == ["mcp-serena"]
    # 公開もその形のタグを作る
    plugin_json(root, "plugins/ndf", "1.3.0")
    git(root, "commit", "-q", "-am", "bump")
    made = []
    monkeypatch.setattr(publish, "tag_and_release", lambda r, plugin, ver, tag, rev, release=True: made.append(tag) or [])
    sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    assert publish.publish(root, "ndf", sha)[1] == "v1.3.0" and made == ["v1.3.0"]
    # 記録は前のタグから宣言の形で版を引く
    body = record.body_of(root, "ndf", "1.3.0", "v1.3.0", "v1.1.0", [1], by_cd=True, template="v{version}")
    assert "1.1.0" in body and "v1.1.0" in body


def test_release_reads_names_from_the_start_snapshot(repo, monkeypatch):
    """#3672 の round 11: プランが環境変数で渡す開始時のスナップショットで名前を解き、開始後に変えた宣言を読み直さない
    （段が起動する子プロセスも環境変数を受け継ぐ。I13・決定 9）。"""
    root = v_tag_repo(repo)
    _release_steps_module(monkeypatch)  # lib/ を sys.path へ入れる
    import branch_strategy
    import repo as repo_lib
    from release_lib import names

    monkeypatch.delenv(repo_lib.SNAPSHOT_ENV, raising=False)
    snap = branch_strategy.snapshot_section(repo_lib.branch_strategy_of(root), "開始時")
    snap["section"]["release"]["tag"] = "rel-{version}"
    monkeypatch.setenv(repo_lib.SNAPSHOT_ENV, repo_lib.snapshot_env(root, snap))
    assert names.tag_name(root, "ndf", "1.3.0") == "rel-1.3.0"  # 今の宣言（v{version}）でなく開始時の形
    other = json.loads(repo_lib.snapshot_env(root, snap))
    monkeypatch.setenv(repo_lib.SNAPSHOT_ENV, json.dumps({**other, "top": "/elsewhere"}))
    assert names.tag_name(root, "ndf", "1.3.0") == "v1.3.0"  # 別のリポジトリのスナップショットは効かない
    monkeypatch.setenv(repo_lib.SNAPSHOT_ENV, "{")
    with pytest.raises(branch_strategy.StrategyError):
        repo_lib.branch_strategy_of(root)


def test_release_tag_shape_defaults_without_a_declared_tag(monkeypatch):
    """宣言に release.tag の形が無い（null を含む）ときは `<plugin>--v<版>`。"""
    _release_steps_module(monkeypatch)  # lib/ を sys.path へ入れる
    from release_lib import names

    assert names.tag_template(None) == names.tag_template({"tag": None}) == "{plugin}--v{version}"
    assert names.release_tag("ndf", "1.2.3", names.tag_template({"tag": "{plugin}@{version}"})) == "ndf@1.2.3"
    assert names.tag_version("ndf--v1.2.3", "ndf") == "1.2.3"
    assert names.tag_version("v1.2.3", "ndf") is None and names.tag_version("vnext", "ndf", "v{version}") is None


def test_bump_raises_the_other_plugin_from_changed_plugins(repo):
    root = changed_repo(repo)
    push_origin(root)
    _, res = changed(root, "--since", "ndf--v1.0.0")
    it = res["items"][0]
    p = run(root, "bump", "--plugin", it["name"], "--to", it["to"])
    assert p.returncode == 0, p.stdout + p.stderr
    got = json.loads((root / "plugins/mcp/mcp-serena/.claude-plugin/plugin.json").read_text())
    assert got["version"] == "2.3.5"


def _release_prod(
    monkeypatch, root: Path, branches: tuple[str, str], plugin: str, deliveries: list | None = None, channel: str = "prod", land=None
):
    """release --channel prod を git と gh を差し替えて流し、(PR の作成, 呼んだ git, 結果) を返す。`branches` は
    (ベースブランチ, 本番ブランチ)。結果の `_code` は終了コード、`land` は本番の取り込みの差し替え。"""
    import argparse

    subprocess.run(["git", "init", "-q", str(root)], check=True)  # 宣言はメインディレクトリ（git の木）から読む（#3672）
    strategy_fixture.declare(root, *branches, plugin_release(plugin), deliveries)
    mod = _release_steps_module(monkeypatch)
    calls, created, found = [], [], []

    def git(r, *args, check=True):
        calls.append(args)
        rc = 1 if args[:3] == ("rev-parse", "-q", "--verify") or args[0] == "ls-remote" else 0  # タグはまだ無い
        out = "release/v1.2.3" if args[:2] == ("rev-parse", "--abbrev-ref") else ""
        return subprocess.CompletedProcess(args, rc, out, "")

    monkeypatch.setattr(mod, "git", git)
    monkeypatch.setattr(mod.publish, "git", git)
    monkeypatch.setattr(mod, "git_root", lambda r: root)
    monkeypatch.setattr(mod.bump, "require_bumped", lambda r, plugin, ver: None)

    def find_pr(r, head, base, states):
        found.append((head, base))
        # 作った PR は開いたまま見つかる（trunk の版上げの PR は本番の PR を兼ねる）
        hit = [i for i, c in enumerate(created, 1) if (c[0], c[1]) == (base, head)]
        return {"number": hit[0], "state": "OPEN"} if hit and "OPEN" in states else None

    monkeypatch.setattr(mod, "find_pr", find_pr)
    monkeypatch.setattr(mod.port_mark, "ensure_no_port_line", lambda r, pr: pr)  # 使い回す PR の本文は書き直さない
    monkeypatch.setattr(mod, "changelog_section", lambda r, ver, plugin: "")
    monkeypatch.setattr(mod, "run_checks", lambda r: [])
    monkeypatch.setattr(mod, "create_pr", lambda r, base, head, title, body: created.append((base, head, title)) or len(created))
    monkeypatch.setattr(mod, "wait_and_merge", lambda r, n, **kw: calls.append(("base-merge", n)) or f"m-{n}")
    monkeypatch.setattr(mod, "land", land or (lambda r, a, n, head, trail_url="": calls.append(("merge", n, head)) or f"m-{n}"))
    monkeypatch.setattr(mod, "pr_view", lambda r, n, fields: {"headRefOid": "a" * 40})
    monkeypatch.setattr(mod.reviewed, "wait_ci_except", lambda *a, **k: None)
    monkeypatch.setattr(mod.approval, "release_pr_allowed", lambda r, n, view: None)
    monkeypatch.setattr(
        mod.approval, "check_approved", lambda r, approved, base, allowed: mod.ac.Verdict(True, approved, "b" * 40, "match")
    )
    monkeypatch.setattr(mod.gh_parts, "gh", lambda args, cwd=None: subprocess.CompletedProcess(args, 0, "", ""))
    monkeypatch.setattr(mod, "emit", lambda obj, code=None, *a, **k: (_ for _ in ()).throw(SystemExit({**obj, "_code": code})))
    # CD の無い場合（場合 3）の経路。CD の行の有無は公開の担い手だけに効かせる（#2773 の受け入れ条件 19）
    monkeypatch.setattr(mod.reviewed, "release_case_or_stop", lambda r: mod.reviewed.release_case.decide_case(False, None, ""))
    with pytest.raises(SystemExit) as e:
        mod.cmd_release(
            argparse.Namespace(root=str(root), version="1.2.3", plugins=None, channel=channel, approved_sha="a" * 40, ci_wait=3600.0)
        )
    return found, created, calls, e.value.code


def test_trunk_release_merges_the_release_pr_only_after_the_approval_check(monkeypatch, tmp_path):
    """#3672 の D17: trunk では版上げの PR が本番の PR を兼ね、開発版の PR の段（承認と照らさないマージ）を通らない。"""
    found, created, calls, out = _release_prod(monkeypatch, tmp_path, ("main", "main"), "foo")
    assert created == [("main", "release/v1.2.3", "Release: foo v1.2.3")]
    # 承認した head と同じ版上げの PR の head だけを本番の取り込みで 1 回入れ、ベースブランチ宛てのマージを打たない（#3703 の I2）
    assert [c for c in calls if c[0] in ("merge", "base-merge")] == [("merge", 1, "a" * 40)]
    assert out["metrics"]["main_pr"] == 1


def test_trunk_release_refuses_the_dev_channel(monkeypatch, tmp_path):
    """#3672: trunk には開発版のチャネルが無く、--channel dev は本番へ入れずに止まる。"""
    import importlib

    mod = importlib.import_module("step_result")
    with pytest.raises(mod.StepError, match="開発版のチャネルが無い"):
        _release_prod(monkeypatch, tmp_path, ("main", "main"), "foo", channel="dev")


def test_release_takes_branches_and_names_from_the_declaration(monkeypatch, tmp_path):
    """#1336 の AC10: ベースブランチと本番チャネルを変えた宣言では、その名前で PR を作りタグを打つ（develop・main の直書きで動かない）。"""
    # NDF がタグを打つ形: CD の行の無い届け先（deliveries: []）。release.cd が null だとタグを持てない（R10）
    found, created, calls, out = _release_prod(monkeypatch, tmp_path, ("trunk", "live"), "foo", [])
    assert found == [("release/v1.2.3", "trunk"), ("release/v1.2.3", "live"), ("release/v1.2.3", "live")]
    assert created == [("trunk", "release/v1.2.3", "Release: foo v1.2.3"), ("live", "release/v1.2.3", "Release: foo v1.2.3 を live へ")]
    # 開発版の PR はベースブランチへ wait_and_merge、本番の PR は比べた先端で本番の取り込みへ（#3703 の I1・I8・I9）
    assert [c for c in calls if c[0] in ("merge", "base-merge")] == [("base-merge", 1), ("merge", 2, "b" * 40)]
    assert ("tag", "-a", "foo--v1.2.3", "m-2", "-m", "foo v1.2.3") in calls
    assert out["metrics"]["tag"] == "foo--v1.2.3" and [i["base"] for i in out["items"] if i["kind"] == "pr"] == ["trunk", "live"]


def test_release_with_the_ai_plugins_declaration_keeps_its_values(monkeypatch, tmp_path):
    """#1336 の I9: ai-plugins の宣言では、ブランチ develop / main・タグ ndf--v<版>・題 Release: ndf v<版> が今と同じに出る。"""
    found, created, calls, out = _release_prod(monkeypatch, tmp_path, ("develop", "main"), "ndf", [])
    assert found == [("release/v1.2.3", "develop"), ("release/v1.2.3", "main"), ("release/v1.2.3", "main")]
    assert created == [("develop", "release/v1.2.3", "Release: ndf v1.2.3"), ("main", "release/v1.2.3", "Release: ndf v1.2.3 を main へ")]
    assert out["metrics"]["tag"] == "ndf--v1.2.3"


# --- 他のプラグインの上げ幅と移行の手順（#1752） ---------------------------------------------


def fake_gh_views(tmp_path: Path, views: dict) -> dict:
    """gh <pr|issue> view N --json ... に views["<pr|issue>:N"] を返す偽の gh（無ければ 1 で終える）を置いた環境。"""
    import os

    bin_dir = tmp_path / "bin-views"
    bin_dir.mkdir()
    data = tmp_path / "views.json"
    data.write_text(json.dumps(views, ensure_ascii=False), encoding="utf-8")
    gh = bin_dir / "gh"
    gh.write_text(
        f"#!{PY}\nimport json, sys\nd = json.load(open({str(data)!r})).get(sys.argv[1] + ':' + sys.argv[3])\n"
        "if d is None:\n    print('not found', file=sys.stderr); sys.exit(1)\nprint(json.dumps(d))\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def merged_change(root: Path, rel: str) -> str:
    """rel を変えたブランチを --no-ff でマージし、マージのコミットを返す。"""
    git(root, "checkout", "-q", "-b", f"feat-{rel.replace('/', '-')}")
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("changed\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "change")
    git(root, "checkout", "-q", "-")
    git(root, "merge", "--no-ff", "-q", f"feat-{rel.replace('/', '-')}", "-m", "merge")
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


ISSUE_744 = (
    "## 影響\n\n| 対象 | 影響 |\n| --- | --- |\n"
    "| 公開インタフェース | 変わる。playwright-kit の `--pwk-drive-folder` を外す。互換の経路は持たない |\n| データ | 無し |\n"
)
APPROVAL = "# t\n\n## 2. 承認の判断に使うもの\n\n| 項目 | 内容 |\n| --- | --- |\n| 版数 | 10.17.61 |\n\n## 同意を求めること\n\n- [ ] x\n\n## 戻し方\n\ny\n"


def m744_repo(root: Path) -> tuple[Path, str]:
    """m744 を再現する: ndf--v10.17.60 の後に playwright-kit 2.0.6 を変えたスプリント PR #1745 がマージされた。"""
    plugin_json(root, "plugins/ndf", "10.17.60")
    plugin_json(root, "plugins/playwright-kit", "2.0.6")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    git(root, "tag", "ndf--v10.17.60")
    oid = merged_change(root, "plugins/playwright-kit/skills/x.md")
    (root / "issues").mkdir()
    (root / "issues" / "approval.md").write_text(APPROVAL, encoding="utf-8")
    return root, oid


def others_run(root: Path, env: dict, *args: str) -> tuple[int, dict]:
    p = inproc.run(SCRIPT, "changed-plugins", "--root", str(root), *args, env=env)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def m744_views(oid: str, issue: str = ISSUE_744, pr_body: str = "要約\n\n## 閉じる課題\n\nCloses #744\n") -> dict:
    return {"pr:1745": {"body": pr_body, "state": "MERGED", "mergeCommit": {"oid": oid}}, "issue:744": {"body": issue}}


def test_changed_plugins_m744_candidate_is_major_and_lands_in_the_approval(repo, tmp_path):
    """受け入れ条件 1・2・3 と I9: m744 の入力で playwright-kit の候補は MAJOR・3.0.0 になり、承認資料の表に載る。"""
    root, oid = m744_repo(repo)
    env = fake_gh_views(tmp_path, m744_views(oid))
    code, res = others_run(root, env, "--prs", "1745", "--approval", "issues/approval.md")
    assert code == 0, res
    (it,) = res["items"]
    assert (it["name"], it["from"], it["level"], it["to"]) == ("playwright-kit", "2.0.6", "major", "3.0.0")
    assert it["basis"] == ["#1745 が閉じる #744 の公開インタフェース: 互換の経路は持たない"]
    text = (root / "issues" / "approval.md").read_text(encoding="utf-8")
    sec = text[text.index("## 版を上げる他のプラグイン") : text.index("## 同意を求めること")]
    row = next(l for l in sec.splitlines() if l.startswith("| playwright-kit"))
    assert [c.strip() for c in row.strip("|").split("|")] == [
        "playwright-kit",
        "2.0.6",
        "MAJOR",
        "3.0.0",
        "#1745 が閉じる #744 の公開インタフェース: 互換の経路は持たない",
    ]
    assert "| ndf" not in sec  # 主のプラグインは載らない（I9）
    assert text.count("- [ ] 「版を上げる他のプラグイン」") == 1
    others_run(root, env, "--prs", "1745", "--approval", "issues/approval.md")  # 走らせ直しても重ならない
    again = (root / "issues" / "approval.md").read_text(encoding="utf-8")
    assert again.count("## 版を上げる他のプラグイン") == 1 and again.count("- [ ] 「版を上げる他のプラグイン」") == 1


@pytest.mark.parametrize(
    "issue, pr_body, level, basis",
    [
        # 否定の文は印でない（受け入れ条件 4）
        (
            "## 影響\n\n| 対象 | 影響 |\n| --- | --- |\n| 公開インタフェース | 変わる。互換の無い削除はしない |\n",
            None,
            "patch",
            ["材料に互換の無い変更の記述が無い"],
        ),
        # 移行の手順がプラグインの名前に触れれば MAJOR（I3）
        (
            "本文\n",
            "## 移行の手順\n\n- playwright-kit の `--pwk-drive-folder` を外す\n",
            "major",
            ["#1745 の移行の手順が playwright-kit に触れる"],
        ),
        # 名前の一部だけの一致は触れたことにしない
        ("本文\n", "## 移行の手順\n\n- playwright-kit-extra を入れ直す\n", "patch", ["材料に互換の無い変更の記述が無い"]),
    ],
)
def test_changed_plugins_candidate_follows_the_words_of_the_materials(repo, tmp_path, issue, pr_body, level, basis):
    root, oid = m744_repo(repo)
    views = m744_views(oid, issue, (pr_body or "") + "\nCloses #744\n")
    env = fake_gh_views(tmp_path, views)
    _, plain = others_run(root, env)
    code, res = others_run(root, env, "--prs", "1745")
    assert code == 0 and res["items"][0]["level"] == level and res["items"][0]["basis"] == basis
    if level == "patch":  # 材料に印が無いなら今の changed-plugins と同じ版（受け入れ条件 4）
        assert res["items"][0]["to"] == plain["items"][0]["to"] == "2.0.7"
    assert plain["items"][0]["level"] == "patch" and plain["items"][0]["basis"] == ["材料を渡していない"]


def test_changed_plugins_unreadable_material_is_patch_with_the_reason(repo, tmp_path):
    """I4: マージのコミットを読めない PR は候補を PATCH にし、根拠に読めなかったことを書く。"""
    root, _ = m744_repo(repo)
    views = {"pr:1745": {"body": "Closes #744\n", "state": "MERGED", "mergeCommit": None}, "issue:744": {"body": ISSUE_744}}
    code, res = others_run(root, fake_gh_views(tmp_path, views), "--prs", "1745", "1746")
    assert code == 0, res
    it = res["items"][0]
    assert it["level"] == "patch" and it["to"] == "2.0.7"
    assert it["basis"][0] == "#1745 を読めない（マージのコミットが無い）" and it["basis"][1].startswith("#1746 を読めない")


def test_changed_plugins_without_other_plugins_writes_none(repo, tmp_path):
    """受け入れ条件 1: 他のプラグインが 0 件なら節は「- 無し」で、同意の行を足さない。"""
    plugin_json(repo, "plugins/ndf", "1.0.0")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "tag", "ndf--v1.0.0")
    oid = merged_change(repo, "plugins/ndf/x.py")
    (repo / "issues").mkdir()
    (repo / "issues" / "approval.md").write_text(APPROVAL, encoding="utf-8")
    env = fake_gh_views(tmp_path, {"pr:5": {"body": "x", "state": "MERGED", "mergeCommit": {"oid": oid}}})
    code, res = others_run(repo, env, "--prs", "5", "--approval", "issues/approval.md")
    assert code == 0 and res["items"] == []
    text = (repo / "issues" / "approval.md").read_text(encoding="utf-8")
    assert "## 版を上げる他のプラグイン\n\n- 無し\n\n## 同意を求めること\n\n- [ ] x\n\n## 戻し方" in text
    # 本番: 0 件なら承認資料を読まずに通る（決定 3）
    code, res = others_run(repo, env, "--decided", "issues/missing.md")
    assert code == 0 and res["items"] == []


def test_bump_others_takes_the_decided_level(repo, tmp_path):
    """受け入れ条件 5・F2: 承認資料の表が MAJOR なら --decided は 3.0.0 を返し、--set で変えた上げ幅も通る。"""
    root, oid = m744_repo(repo)
    env = fake_gh_views(tmp_path, m744_views(oid))
    others_run(root, env, "--prs", "1745", "--approval", "issues/approval.md")
    code, res = others_run(root, env, "--decided", "issues/approval.md")
    assert code == 0, res
    assert [(i["name"], i["to"]) for i in res["items"]] == [("playwright-kit", "3.0.0")]
    assert res["metrics"]["decided"].endswith("issues/approval.md")
    code, res = others_run(root, env, "--approval", "issues/approval.md", "--set", "playwright-kit=minor")
    assert code == 0 and res["items"][0]["to"] == "2.1.0"
    assert res["items"][0]["basis"][0] == "リリース承認ゲートで MINOR に決めた"
    code, res = others_run(root, env, "--decided", "issues/approval.md")
    assert code == 0 and res["items"][0]["to"] == "2.1.0"


@pytest.mark.parametrize(
    "edit",
    [
        lambda t: None,  # 承認資料が無い
        lambda t: t.replace("| MAJOR |", "| BIG |"),  # 上げ幅を読めない
        lambda t: t.replace("| 3.0.0 |", "| 2.0.7 |"),  # 上げた後の版が合わない（I1）
        lambda t: t.replace("| playwright-kit |", "| mcp-other |"),  # 差分と表が合わない（I2）
        lambda t: t.replace("## 版を上げる他のプラグイン", "## 別の節"),  # 節が無い
    ],
)
def test_bump_others_stops_when_the_decided_level_is_unreadable(repo, tmp_path, edit):
    """受け入れ条件 6: 決まった上げ幅を読めなければ PATCH へ倒さずに 1 で落ちる。"""
    root, oid = m744_repo(repo)
    env = fake_gh_views(tmp_path, m744_views(oid))
    others_run(root, env, "--prs", "1745", "--approval", "issues/approval.md")
    path = root / "issues" / "approval.md"
    new = edit(path.read_text(encoding="utf-8"))
    path.unlink() if new is None else path.write_text(new, encoding="utf-8")
    code, res = others_run(root, env, "--decided", "issues/approval.md")
    assert code == 1 and res["status"] == "stopped" and res["items"] == []


def test_changed_plugins_keeps_already_bumped_plugins(repo, tmp_path):
    """受け入れ条件 7: 前のタグから版が変わったプラグインは上げ直さず、表では「上げ済み」の行で --decided も上げない。"""
    root, oid = m744_repo(repo)
    plugin_json(root, "plugins/playwright-kit", "3.0.0")
    git(root, "commit", "-q", "-am", "bump pwk")
    env = fake_gh_views(tmp_path, m744_views(oid))
    code, res = others_run(root, env, "--prs", "1745", "--approval", "issues/approval.md")
    assert code == 0 and res["items"] == [] and res["metrics"]["already"] == ["playwright-kit"]
    text = (root / "issues" / "approval.md").read_text(encoding="utf-8")
    assert "上げ済み" in text and "- [ ] 「版を上げる他のプラグイン」" not in text
    code, res = others_run(root, env, "--decided", "issues/approval.md")
    assert code == 0 and res["items"] == [] and res["metrics"]["already"] == ["playwright-kit"]


@pytest.mark.parametrize(
    "args",
    [
        ("--decided", "issues/approval.md", "--prs", "1745"),
        ("--set", "playwright-kit=MAJOR"),
        ("--approval", "issues/approval.md", "--set", "nothing=MAJOR"),
        ("--approval", "issues/approval.md", "--set", "playwright-kit=HUGE"),
    ],
)
def test_changed_plugins_rejects_wrong_arguments(repo, tmp_path, args):
    root, oid = m744_repo(repo)
    env = fake_gh_views(tmp_path, m744_views(oid))
    others_run(root, env, "--prs", "1745", "--approval", "issues/approval.md")
    code, res = others_run(root, env, *args)
    assert code == 2 and res["status"] == "stopped"


MIGRATION_BODIES = {
    **PR_BODIES,
    13: {
        "title": "題名 C",
        "body": "## 利用者向けの変化\n\n- Drive の保管を外した\n\n## 移行の手順\n\n- playwright-kit の `--pwk-drive-folder` を外す\n",
    },
}


def test_notes_copy_the_migration_steps_under_their_heading(repo, tmp_path):
    """受け入れ条件 3: 移行の手順は版の節の `### 移行の手順` の下と、承認資料の節へ PR 番号つきで写る。"""
    pdir = notes_repo(repo)
    before = (pdir / "README.md").read_bytes()
    env = fake_gh(tmp_path, MIGRATION_BODIES)
    p = subprocess.run(
        [PY, str(SCRIPT), "notes", "--root", str(repo), "--version", "1.2.3-dev.1", "--prs", "12", "13"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    want = "- 題名 B（#12）\n- Drive の保管を外した（#13）\n\n### 移行の手順\n\n- playwright-kit の `--pwk-drive-folder` を外す（#13）\n"
    assert f"## [ndf 1.2.3] - 2026-01-01\n\n{want}\n## [ndf 1.2.2]" in (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert (pdir / "README.md").read_bytes() == before
    approval = repo / "issues" / "approval.md"
    approval.parent.mkdir()
    approval.write_text(
        "# t\n\n| 項目 | 内容 |\n| --- | --- |\n| 含む PR | #12 題名 B<br>#13 移行 |\n| 配る中身 | x |\n| 承認前の試用で確かめたこと | x |\n\n## 同意を求めること\n\n- [ ] x\n",
        encoding="utf-8",
    )
    args = [PY, str(SCRIPT), "notes", "--root", str(repo), "--version", "1.2.3-dev.1", "--prs", "12", "13", "--approval", str(approval)]
    assert subprocess.run(args, capture_output=True, text=True, env=env).returncode == 0
    text = approval.read_text(encoding="utf-8")
    assert "## 移行の手順\n\n- playwright-kit の `--pwk-drive-folder` を外す（#13）\n\n## 同意を求めること" in text


def test_notes_without_migration_steps_add_no_heading(repo, tmp_path):
    """I6・受け入れ条件 12: 移行の手順を持つ PR が無ければ版の節に見出しを作らない。"""
    notes_repo(repo)
    env = fake_gh(tmp_path, PR_BODIES)
    p = subprocess.run(
        [PY, str(SCRIPT), "notes", "--root", str(repo), "--version", "1.2.3", "--prs", "11", "12"], capture_output=True, text=True, env=env
    )
    assert p.returncode == 0 and json.loads(p.stdout.strip().splitlines()[-1])["metrics"]["migration"] == 0
    assert "移行の手順" not in (repo / "CHANGELOG.md").read_text(encoding="utf-8")


def test_decided_versions_resumes_after_a_partial_bump():
    """版上げが途中で止まり上げた分がコミットされた後の再実行: HEAD の版が表の「上げた後の版」と同じ行は
    上げ終えたものとして外し、残りだけを上げる。HEAD の版が表と違えば合わないとして止める。"""
    scripts = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(scripts), str(scripts / "lib")]
    from release_lib import others

    rows = [others.OtherPlugin("a", "1.0.0", "PATCH", "1.0.1", []), others.OtherPlugin("b", "2.0.0", "MAJOR", "3.0.0", [])]
    assert others.decided_versions(rows, {"b": "2.0.0"}, {"a": "1.0.1"}) == {"b": "3.0.0"}
    with pytest.raises(ValueError, match="差分に無い: a"):
        others.decided_versions(rows, {"b": "2.0.0"}, {"a": "1.1.0"})
    with pytest.raises(ValueError, match="差分に無い: a"):
        others.decided_versions(rows, {"b": "2.0.0"})


# --- #2623: 公開の担い手と CD の結果 -------------------------------------------------

CD_ROW = {"target": "ndf", "kind": "auto", "trigger": "release-cd.yml", "channel": "production", "versioned": True}


@pytest.mark.parametrize(
    ("row", "by_cd"),
    [
        ({**CD_ROW, "publishes_release": True}, True),
        (CD_ROW, False),  # 別の成果物を届ける行が公開を省かせない（決定 12）
        ({"target": "ndf", "kind": "manual", "trigger": "手順", "channel": "production", "versioned": True}, False),
    ],
)
def test_release_publishes_only_when_no_row_declares_the_cd_does(monkeypatch, tmp_path, row, by_cd):
    """受け入れ条件 20・I11: 公開の担い手は宣言の publishes_release だけで決まり、両方で打たない。"""
    _, _, calls, out = _release_prod(monkeypatch, tmp_path, ("develop", "main"), "ndf", [row])
    tagged = ("tag", "-a", "ndf--v1.2.3", "m-2", "-m", "ndf v1.2.3") in calls
    assert tagged is (not by_cd)
    assert out["metrics"]["tag"] == (None if by_cd else "ndf--v1.2.3")
    assert out["metrics"]["publisher"] == ("cd" if by_cd else "ndf")


def test_the_separated_production_merge_lands_the_compared_tip(monkeypatch, tmp_path):
    """#3703 の受け入れ条件 7・I1・I9: 分かれた経路の CD の無い本番の PR は、比べた先端で本番の取り込み（land）を 1 回通り、
    wait_and_merge を本番ブランチ宛てに呼ばない。"""
    import argparse

    mod = _release_steps_module(monkeypatch)
    sha = "a" * 40
    seen, merged = [], []
    monkeypatch.setattr(mod, "find_pr", lambda *a, **k: None)
    monkeypatch.setattr(mod, "create_pr", lambda *a: seen.append(a[-1]) or 9)
    monkeypatch.setattr(mod, "wait_and_merge", lambda *a, **kw: merged.append(a))
    monkeypatch.setattr(mod, "land", lambda r, a, n, head, trail_url="": seen.append((n, head)) or "m")
    a = argparse.Namespace(version="1.2.3", ci_wait=60.0, approval=None)
    verdict = mod.ac.Verdict(True, sha, "b" * 40, "match")
    ctx = mod.ReleaseCtx(tmp_path, a, "ndf", ["ndf"], "release/v1.2.3", "develop", "main")
    main_pr, merge, _ = mod._merge_prod_pr(ctx, 7, sha, None, verdict, [], {})
    # 本番の PR も旧いプラグインの変更を運ぶので、本文に ndf-next の port 検査の印を持つ
    assert mod.port_mark.NO_PORT_LINE in seen[0] and seen[1] == (9, "b" * 40) and (main_pr, merge) == (9, "m") and merged == []


def _record(monkeypatch, root, **args):
    """record を gh と git を差し替えて流し、(結果, 投稿した本文, 終了コード) を返す。"""
    import argparse

    if not (root / ".ndf" / "project.json").is_file():  # 届け先の行を持つ宣言は呼び手が先に置く
        strategy_fixture.declare(root, "develop", "main", plugin_release())
    subprocess.run(["git", "init", "-q", str(root)], check=True)  # 宣言はメインディレクトリ（git の木）から読む（#3672）
    mod = _release_steps_module(monkeypatch)
    posted, codes = [], []
    monkeypatch.setattr(mod, "git_root", lambda r: root)
    monkeypatch.setattr(mod, "git", lambda r, *a, check=True: subprocess.CompletedProcess(a, 1, "", ""))
    monkeypatch.setattr(mod.record, "git", lambda r, *a, check=True: subprocess.CompletedProcess(a, 0, "abc1234\n", ""))
    monkeypatch.setattr(mod, "find_pr", lambda r, head, base, states: {"number": 7, "state": "MERGED"})
    monkeypatch.setattr(mod, "merge_commit_of", lambda r, n: "5d6e7f8" + "0" * 33)
    monkeypatch.setattr(mod, "release_tag_before", lambda r, plugin, current: None)
    monkeypatch.setattr(mod.gh_parts, "gh", lambda args, cwd=None: subprocess.CompletedProcess(args, 1, "", ""))
    monkeypatch.setattr(mod.record, "release_pr_text", lambda r, n: ("", "https://x/pull/7"))
    monkeypatch.setattr(mod.record, "post_comment", lambda r, n, body: posted.append((n, body)))

    def emit(obj, code=None, *a, **k):
        codes.append(code)
        raise SystemExit(obj)

    monkeypatch.setattr(mod, "emit", emit)
    ns = {
        "root": str(root),
        "promote": False,
        "merged": False,
        "version": None,
        "head": None,
        "base": None,
        "prs": [3, 4],
        "cd_result": None,
    }
    with pytest.raises(SystemExit) as e:
        mod.cmd_record(argparse.Namespace(**{**ns, **args}))
    return e.value.code, posted, codes[-1] if codes else None


def _cd_file(tmp_path, value, reason=""):
    f = tmp_path / "cd.json"
    f.write_text(
        json.dumps({"value": value, "revision": "5d6e7f8" + "0" * 33, "reason": reason, "evidence": [{"url": "https://x/runs/1"}]})
    )
    return str(f)


def test_record_merged_writes_to_the_last_pr_with_the_revision(monkeypatch, tmp_path):
    """受け入れ条件 3: 版の無い CD の形はリビジョンを版の値にして記録を書く。"""
    out, posted, code = _record(monkeypatch, tmp_path, merged=True, base="main", cd_result=_cd_file(tmp_path, "success"))
    assert code is None and out["status"] == "ok" and out["metrics"]["cd_result"] == "success"
    n, body = posted[0]
    assert n == 4 and "main 5d6e7f8" in body and "CD の結果: 成功" in body and body.count("段階: 本番") == 1


def test_record_stops_with_5_after_writing_a_failure(monkeypatch, tmp_path):
    """受け入れ条件 8・I14: 失敗は書いた後に 5 で止まる。"""
    out, posted, code = _record(monkeypatch, tmp_path, merged=True, base="main", cd_result=_cd_file(tmp_path, "failure", "落ちた"))
    assert code == 5 and out["status"] == "stopped" and "段階: 反映失敗（落ちた）" in posted[0][1]


def test_record_by_cd_replaces_the_tag_check_with_the_result(monkeypatch, tmp_path):
    """公開物を CD が作る形はタグの照会を CD の結果へ置き換える。結果のファイルが無ければ今と同じく前提で止まる。"""
    strategy_fixture.declare(tmp_path, "develop", "main", plugin_release(), [{**CD_ROW, "publishes_release": True}])
    out, posted, _ = _record(monkeypatch, tmp_path, version="1.2.3", cd_result=_cd_file(tmp_path, "undetermined", "待ちの上限"))
    assert out["status"] == "ok" and "CD の結果: 未確定" in posted[0][1] and "公開は CD が担う" in posted[0][1]
    import step_result

    with pytest.raises(step_result.StepError):
        _record(monkeypatch, tmp_path, version="1.2.3")


def test_record_without_cd_does_not_require_the_publications_the_declaration_skips(monkeypatch, tmp_path):
    """#3672 の round 5: CD の無い宣言（R10 で release.tag が null・github_release が偽）は、_published が作らないタグと
    GitHub Release を record でも求めず、タグを事実として書かずに記録を書く。"""
    out, posted, code = _record(monkeypatch, tmp_path, version="1.2.3")
    assert code is None and out["status"] == "ok"
    assert "宣言がタグを作らない" in posted[0][1] and "ndf--v1.2.3" not in posted[0][1]


def test_cd_result_reads_the_merge_commit_of_the_last_pr_not_the_branch_tip(monkeypatch):
    """直接本番へマージする経路は record --merged と同じリビジョン（最後の PR のマージのコミット）を読む。読めなければ先端で代えない。"""
    import argparse

    mod = _release_steps_module(monkeypatch)
    monkeypatch.setattr(mod, "git", lambda *a, **kw: subprocess.CompletedProcess(a, 0, "tip\n", ""))
    monkeypatch.setattr(mod, "merge_commit_of", lambda root, n: f"merge-{n}")
    a = argparse.Namespace(base="main", head=None, prs=[3, 7])
    assert mod._cd_revision(".", a) == "merge-7"
    monkeypatch.setattr(mod, "merge_commit_of", lambda root, n: None)
    assert mod._cd_revision(".", a) == ""
    assert mod._cd_revision(".", argparse.Namespace(base="main", head=None, prs=None)) == "tip"


# --- #2773: CD のある場合の本番・開発版・証跡のチェック ---------------------------------------------------

HEAD = "c" * 40


def _case(name):
    import release_case as rc

    return {
        "no-cd": rc.decide_case(False, None, ""),
        "cd-no-review": rc.decide_case(True, 0, ""),
        "cd-review": rc.decide_case(True, 1, ""),
        "cd-admin": rc.decide_case(True, 1, "", admin=True),
    }[name]


def _exits(monkeypatch, target):
    seen = []

    def emit(obj, code=None, *a, **k):
        seen.append(code)
        raise SystemExit(obj)

    monkeypatch.setattr(target, "emit", emit)
    return seen


# 本番の PR（find_pr の答え）。開いたものは承認の照合へ、マージ済みは再開へ進む
OPEN_PROD_PR = {"number": 9, "state": "OPEN"}
MERGED_PROD_PR = {"number": 9, "state": "MERGED"}


def _reviewed_prod(monkeypatch, tmp_path, case, state, found=None, land=None, record=None, backs=None):
    """reviewed.release_prod を git と gh を差し替えて流し、(結果, 終了コード, 本番の取り込み（land）の引数, 公開した PR) を返す。
    `record` は迂回の記録の書き込みの差し替え（呼ばれた引数を足す並びか、投げる例外）。`backs` は公開の後の (items, back) を足す。"""
    import argparse

    from release_lib import prod_landed, reviewed as rv
    from release_lib.context import ReleaseCtx

    merged, published, ci = [], [], []
    monkeypatch.setattr(
        rv, "git", lambda r, *a, check=True: subprocess.CompletedProcess(a, 1 if a[:2] == ("rev-parse", "-q") else 0, "", "")
    )
    monkeypatch.setattr(rv.repo_lib, "restore_branch", lambda *a, **k: None)
    monkeypatch.setattr(rv, "ensure_no_port_line", lambda root, pr: pr)  # 印の足し方は test_ensure_no_port_line_* が確かめる
    monkeypatch.setattr(rv, "repo_or_stop", lambda r: "o/r")
    monkeypatch.setattr(prod_landed, "repo_or_stop", lambda r: "o/r")
    monkeypatch.setattr(rv.approval_trail, "read_head", lambda repo, n: (HEAD, "main", "open"))
    monkeypatch.setattr(
        rv.approval, "approved_on_pr", lambda repo, n, head, c: rv.approval_trail.Judgment(state, "理由", url="https://t/1")
    )

    def write_admin_record(repo, n, sha, url):
        if isinstance(record, Exception):
            raise record
        (record if record is not None else []).append((n, sha, url))
        return True, "https://r/1"

    monkeypatch.setattr(rv.approval_trail, "write_admin_record", write_admin_record)

    def published_(r, a, plugin, prod, main_pr, merge, items, metrics, back=None):
        published.append(main_pr)
        if backs is not None:
            backs.append((items, back))
        raise SystemExit({"items": items})

    monkeypatch.setattr(rv, "wait_ci_except", lambda *a: ci.append(a[2]))
    monkeypatch.setattr(rv, "wait_trail_status", lambda *a: None)
    ops = rv.ProdOps(
        find_pr=lambda r, head, base, states: found,
        create_pr=lambda *a: 9,
        merge_commit_of=lambda r, n: "m",
        merge_release_pr=lambda *a: (7, 7, "c"),
        published=published_,
        land=land or (lambda r, a, n, head, trail_url="": merged.append({"pr": n, "head": head, "trail_url": trail_url}) or "m"),
        pr_check_buckets=lambda r, n: [],
    )
    codes = _exits(monkeypatch, rv)
    a = argparse.Namespace(version="1.2.3", ci_wait=60.0)
    with pytest.raises(SystemExit) as e:
        ctx = ReleaseCtx(tmp_path, a, "ndf", ["ndf"], "release/v1.2.3", "develop", "main")
        rv.release_prod(ctx, _case(case), ops)
    return e.value.code, (codes[-1] if codes else None), merged, published, ci


@pytest.mark.parametrize("case", ["cd-no-review", "cd-review"])
def test_reviewed_prod_stops_at_the_gate_until_the_approval_matches(monkeypatch, tmp_path, case):
    """受け入れ条件 5・6・13・18: 承認が先端と一致しなければ、CI を待ってから本番の PR を開いたまま 10 で止まりマージしない。"""
    out, code, merged, published, ci = _reviewed_prod(monkeypatch, tmp_path, case, "failure")
    assert code == 10 and out["status"] == "gate" and merged == [] and published == []
    assert out["metrics"]["main_pr"] == 9 and out["metrics"]["approved_sha"] == HEAD and ci == [("ndf/release-approval",)]
    assert ("approve --pr 9" in out["next"]) is (case == "cd-no-review")


@pytest.mark.parametrize("case", ["cd-no-review", "cd-review"])
def test_reviewed_prod_merges_the_approved_head_without_the_bypass(monkeypatch, tmp_path, case):
    """受け入れ条件 2・12・I7・#3703 の受け入れ条件 7: 承認が先端と一致すれば、その先端と証跡の URL で本番の取り込み（land）を
    1 回通り、公開の後へ進む（迂回するかは land_approved が宣言から決める）。"""
    _, _, merged, published, _ = _reviewed_prod(monkeypatch, tmp_path, case, "success", found=OPEN_PROD_PR)
    assert merged == [{"pr": 9, "head": HEAD, "trail_url": "https://t/1"}] and published == [9]


def test_reviewed_prod_merges_cd_admin_with_the_bypass_and_records_it(monkeypatch, tmp_path):
    """#3506 の受け入れ条件 1・6・I3・I8・#3703 の決定 10: cd-admin で証跡が先端と一致すれば、先端と証跡の URL で本番の取り込みを
    通り、結果の行の admin が true になる。迂回の記録は land_approved が書き、ここでは書かない（2 回書かない）。"""
    record, backs = [], []
    out, _, merged, published, _ = _reviewed_prod(
        monkeypatch, tmp_path, "cd-admin", "success", found=OPEN_PROD_PR, record=record, backs=backs
    )
    assert merged == [{"pr": 9, "head": HEAD, "trail_url": "https://t/1"}] and published == [9]
    assert {"kind": "pr", "name": "#9", "result": "merged", "base": "main", "admin": True} in out["items"]
    assert record == [] and not any(i.get("record") == "admin_merge" for i in out["items"])
    items, back = backs[0]
    assert back(items, {}, "done") == ("ok", None, "done")


@pytest.mark.parametrize("case", ["cd-no-review", "cd-review"])
def test_reviewed_prod_without_the_declaration_writes_admin_false_and_no_record(monkeypatch, tmp_path, case):
    """#3506 の受け入れ条件 7: 宣言の無い場合は迂回なしで、結果の行の admin が false、迂回の記録を書かない。"""
    record = []
    out, *_ = _reviewed_prod(monkeypatch, tmp_path, case, "success", found=OPEN_PROD_PR, record=record)
    assert {"kind": "pr", "name": "#9", "result": "merged", "base": "main", "admin": False} in out["items"] and record == []


def test_reviewed_prod_cd_admin_stops_at_the_gate_without_the_approval(monkeypatch, tmp_path):
    """#3506 の受け入れ条件 2・I4: cd-admin で承認が無ければマージせず 10 で止まり、証跡を書く手順を案内する。"""
    out, code, merged, published, _ = _reviewed_prod(monkeypatch, tmp_path, "cd-admin", "failure")
    assert code == 10 and out["status"] == "gate" and merged == [] and published == []
    assert "approve --pr 9" in out["next"] and "--by user" in out["next"]


def test_reviewed_prod_cd_admin_stops_at_the_gate_when_the_head_moves(monkeypatch, tmp_path):
    """#3506 の受け入れ条件 4・I6a・I6b: 迂回の前に先端が動けば（merge-when-green の head_moved）マージせず 10 で止まる。"""
    from release_lib.approval import HeadMoved

    def moved(r, a, n, head, trail_url=""):
        raise HeadMoved("先端が動いた")

    record = []
    out, code, _, published, _ = _reviewed_prod(monkeypatch, tmp_path, "cd-admin", "success", found=OPEN_PROD_PR, land=moved, record=record)
    assert code == 10 and out["status"] == "gate" and published == [] and record == []


@pytest.mark.parametrize("code", [1, 75])
def test_reviewed_prod_cd_admin_does_not_merge_when_the_checks_fail(monkeypatch, tmp_path, code):
    """#3506 の受け入れ条件 3・I5: チェックが落ちたら 1、上限なら 75 で止まり、迂回の記録を書かない。"""
    from step_result import StepError

    def failed(r, a, n, head, trail_url=""):
        raise StepError("PR #9 をマージできない: チェックが落ちた", code)

    record = []
    with pytest.raises(StepError) as e:
        _reviewed_prod(monkeypatch, tmp_path, "cd-admin", "success", found=OPEN_PROD_PR, land=failed, record=record)
    assert e.value.code == code and record == []


@pytest.mark.parametrize("review", ["APPROVED", "REVIEW_REQUIRED"])
def test_reviewed_prod_resume_writes_the_missing_admin_record(monkeypatch, tmp_path, review):
    """#3506 の I7: マージ済みの本番の PR に、利用者の証跡が先端と一致し記録が無ければ、打ち直しで書く。
    PR のレビューの判定（承認がそろっていても）は条件に使わない。"""
    from release_lib import reviewed as rv

    monkeypatch.setattr(rv, "gh_json", lambda root, args, what: {"headRefOid": HEAD, "reviewDecision": review})
    bys = []

    def judge(repo, n, head, by=None):
        bys.append(by)
        return rv.approval_trail.Judgment("success", "承認", url="https://t/2")

    monkeypatch.setattr(rv.approval_trail, "judge_trails_on", judge)
    record = []
    out, _, merged, published, _ = _reviewed_prod(monkeypatch, tmp_path, "cd-admin", "success", found=MERGED_PROD_PR, record=record)
    assert merged == [] and published == [9] and record == [(9, HEAD, "https://t/2")] and bys == ["user"]


def test_admin_record_on_resume_skips_without_a_matching_user_trail(monkeypatch):
    from release_lib import reviewed as rv

    monkeypatch.setattr(rv, "gh_json", lambda root, args, what: {"headRefOid": HEAD})
    monkeypatch.setattr(rv.approval_trail, "judge_trails_on", lambda *a: rv.approval_trail.Judgment("failure", "無い"))
    assert rv.admin_record_on_resume(".", "o/r", _case("cd-admin"), 9) is None
    assert rv.admin_record_on_resume(".", "o/r", _case("cd-review"), 9) is None


def test_admin_back_keeps_the_merge_back_code():
    from release_lib import reviewed as rv

    items = [{"kind": "comment", "name": "#9", "result": "unwritten", "record": "admin_merge"}]
    status, code, done = rv.admin_back(lambda i, m, d: ("stopped", 4, d + "。戻すマージで止まった"))(items, {}, "done")
    assert (status, code) == ("stopped", 4) and "戻すマージ" in done and "迂回の記録" in done
    assert rv.admin_back()([], {}, "done") == ("ok", None, "done")


# 現状固定（I-023）: admin_back の分岐（戻すマージの有無・状態・終了コード × 書けなかった迂回の記録の有無）。期待値は今の実装の出力である
_UNWRITTEN = {"record": "admin_merge", "result": "unwritten"}
_STOPPED_TAIL = "。迂回の記録を書けなかった（本番は出ている）"


@pytest.mark.parametrize(
    "back, items, expected",
    [
        (None, [], ("ok", None, "done")),
        (None, [_UNWRITTEN], ("stopped", 1, "done" + _STOPPED_TAIL)),
        (None, [{"record": "admin_merge", "result": "written"}], ("ok", None, "done")),
        (None, ["x", {"record": "admin_merge"}], ("ok", None, "done")),
        (lambda i, m, d: ("ok", 0, d), [], ("ok", 0, "done")),
        (lambda i, m, d: ("ok", 0, d), [_UNWRITTEN], ("stopped", 1, "done" + _STOPPED_TAIL)),
        (lambda i, m, d: ("ok", None, d), [_UNWRITTEN], ("stopped", 1, "done" + _STOPPED_TAIL)),
        (lambda i, m, d: ("stopped", 4, d + "。X"), [], ("stopped", 4, "done。X")),
        (lambda i, m, d: ("stopped", 4, d + "。X"), [_UNWRITTEN], ("stopped", 4, "done。X" + _STOPPED_TAIL)),
        (lambda i, m, d: ("stopped", 0, d + "。X"), [_UNWRITTEN], ("stopped", 1, "done。X" + _STOPPED_TAIL)),
        (lambda i, m, d: ("stopped", None, d), [_UNWRITTEN], ("stopped", 1, "done" + _STOPPED_TAIL)),
    ],
)
def test_admin_back_branches(back, items, expected):
    from release_lib import reviewed as rv

    assert rv.admin_back(back)(list(items), {}, "done") == expected


def test_admin_back_sees_the_rows_the_merge_back_appended():
    from release_lib import reviewed as rv

    items: list = []
    got = rv.admin_back(lambda i, m, d: (i.append(dict(_UNWRITTEN)), ("ok", 0, d))[1])(items, {}, "done")
    assert got == ("stopped", 1, "done" + _STOPPED_TAIL) and items == [_UNWRITTEN]


def test_release_selected_writes_admin_on_the_production_row(monkeypatch, tmp_path):
    """#3506 の I8: 選んで集める取り込み方でも、本番の PR の行に迂回の有無を書く。"""
    import argparse

    from release_lib import strategy_route as promotion
    from release_lib.context import ReleaseCtx

    monkeypatch.setattr(
        promotion, "git", lambda r, *a, check=True: subprocess.CompletedProcess(a, 1 if a[:2] == ("rev-parse", "-q") else 0, "m\n", "")
    )
    monkeypatch.setattr(promotion, "_merge_exact", lambda *a: "m")
    ops = argparse.Namespace(find_pr=lambda r, head, base, states: None, create_pr=lambda *a: 9)
    a = argparse.Namespace(version="1.2.3", channel="prod", promotion="select", ci_wait=60.0)
    ctx = ReleaseCtx(tmp_path, a, "ndf", ["ndf"], "release/v1.2.3", "develop", "main")
    for case, admin in (("cd-admin", True), ("cd-no-review", False)):
        _, _, items, _, back = promotion.release_selected(ctx, None, ops, _case(case))
        assert {"kind": "pr", "name": "#9", "result": "merged", "base": "main", "admin": admin} in items and callable(back)


def test_reviewed_prod_resumes_after_the_merge(monkeypatch, tmp_path):
    _, _, merged, published, _ = _reviewed_prod(monkeypatch, tmp_path, "cd-no-review", "success", found=MERGED_PROD_PR)
    assert merged == [] and published == [9]


@pytest.mark.parametrize("case", ["cd-no-review", "cd-review"])
def test_reviewed_prod_resume_stops_when_the_merged_head_lacks_the_approval(monkeypatch, tmp_path, case):
    """#3703 の I2: 人が承認の後のコミットごとマージした本番の PR（head が PR の上の承認と違う）からは、再開でも公開しない。"""
    from step_result import StepError

    with pytest.raises(StepError) as e:
        _reviewed_prod(monkeypatch, tmp_path, case, "failure", found=MERGED_PROD_PR)
    assert e.value.code == 1 and "PR の上の承認と一致しない" in str(e.value)


def _closed_landing_ops(landed):
    """早送りで閉じた本番の PR だけがある `ProdOps` の代わり（`landed_prod_pr` が読む find_pr・merge_commit_of・landed_commit）。"""
    calls = []

    def find_pr(root, head, base, states):
        calls.append(tuple(states))
        return {"number": 9, "state": "CLOSED"} if "CLOSED" in states else None

    ops = SimpleNamespace(find_pr=find_pr, merge_commit_of=lambda r, n: None, landed_commit=lambda r, n, head: landed)
    return ops, calls


@pytest.mark.parametrize(
    ("landed", "state", "want"), [("f" * 40, "success", (9, "f" * 40)), (None, "success", None), ("f" * 40, "failure", 1)]
)
def test_landed_prod_pr_resumes_a_pr_closed_by_the_fast_forward(monkeypatch, landed, state, want):
    """#3703: ruleset の無い strict の早送りで閉じた本番の PR は、取り込みの記録が PR の head と合い、その head が PR の上の承認と
    一致するときだけ再開する。記録が無ければ None（新しく作る側へ）、承認と違えば 1 で止める。"""
    from release_lib import prod_landed as pld
    from step_result import StepError

    monkeypatch.setattr(pld, "repo_or_stop", lambda r: "o/r")
    monkeypatch.setattr(pld.approval_trail, "read_head", lambda repo, n: (HEAD, "main", "closed"))
    monkeypatch.setattr(pld.approval, "approved_on_pr", lambda repo, n, head, c: pld.approval_trail.Judgment(state, "理由"))
    ops, calls = _closed_landing_ops(landed)
    if want == 1:
        with pytest.raises(StepError) as e:
            pld.landed_prod_pr(".", "release/v1.2.3", "main", None, ops, _case("cd-no-review"))
        assert e.value.code == 1
    else:
        assert pld.landed_prod_pr(".", "release/v1.2.3", "main", None, ops, _case("cd-no-review")) == want
    assert calls == [("CLOSED",)]


def test_landed_prod_pr_skips_the_closed_search_when_a_pr_is_open():
    """開いた本番の PR があれば、閉じた PR を探さない（開いた PR で承認を待つ）。"""
    from release_lib import prod_landed as pld

    ops, calls = _closed_landing_ops("f" * 40)
    assert pld.landed_prod_pr(".", "release/v1.2.3", "main", OPEN_PROD_PR, ops, _case("cd-no-review")) is None and calls == []


@pytest.mark.parametrize(("reason", "want"), [(None, None), ("動く workflow がある", 75)])
def test_wait_ci_except_passes_a_pr_without_ci(reason, want):
    """CI を設定していないリポジトリ: 検査が 0 件で、PR の head で動く CI が無ければ待たずに戻る。動く CI があれば上限まで待って 75。"""
    from release_lib import reviewed as rv
    from step_result import StepError

    probes = []
    ops = SimpleNamespace(pr_check_buckets=lambda r, n: [], no_checks_reason=lambda r, n: probes.append(n) or reason)
    if want is None:
        assert rv.wait_ci_except(".", 9, ("ndf/release-approval",), 0, ops) is None
    else:
        with pytest.raises(StepError) as e:
            rv.wait_ci_except(".", 9, ("ndf/release-approval",), 0, ops)
        assert e.value.code == want
    assert probes == [9]


def test_prod_ops_wires_the_entry_functions_at_call_time(monkeypatch):
    """入口が release_prod へ渡す操作は、呼ぶ時点の入口の関数である（マージは _merge_release_pr、公開の後は _published）。"""
    mod = _release_steps_module(monkeypatch)
    wired = {
        "find_pr": "find_pr",
        "create_pr": "create_pr",
        "merge_commit_of": "merge_commit_of",
        "merge_release_pr": "_merge_release_pr",
        "published": "_published",
        "land": "land",
        "pr_check_buckets": "pr_check_buckets",
        "no_checks_reason": "pr_no_checks_reason",
        "landed_commit": "landed_commit",
    }
    for name in wired.values():
        monkeypatch.setattr(mod, name, object())
    ops = mod._prod_ops()
    assert {field: getattr(ops, field) for field in wired} == {field: getattr(mod, name) for field, name in wired.items()}


@pytest.mark.parametrize(
    "merge, bypass",
    [
        ({"method": "direct", "admin": True}, True),
        ({"method": "direct", "admin": False}, False),
        ({"method": "strict", "admin": False}, False),
    ],
)
def test_wait_and_merge_takes_the_bypass_from_the_declaration(monkeypatch, tmp_path, merge, bypass):
    """#3672 の I6: 管理者の迂回は引数でなく宣言の merge から決め、method が direct で admin が真のときだけ使う。"""
    mod = _release_steps_module(monkeypatch)
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q")
    sec = strategy_fixture.section("web-develop")
    strategy_fixture.write_strategy(root, "web-develop", merge={**sec["merge"], **merge})
    calls = []
    out = json.dumps({"tool": "merged", "status": "ok", "summary": "#5 をマージした", "items": []})
    monkeypatch.setattr(mod, "run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, out, ""))
    monkeypatch.setattr(mod, "merge_commit_of", lambda root, n: "c")
    mod.wait_and_merge(root, 5)
    assert ("--no-admin" not in calls[0]) is bypass


@pytest.mark.parametrize("admin", [True, False])
def test_wait_and_merge_takes_the_bypass_from_the_case_over_the_declaration(monkeypatch, tmp_path, admin):
    """#3672 の round 5: CD のある場合は呼び手が渡す場合（case.admin_merge）で迂回を決め、宣言の merge.admin を見ない
    （cd-no-review に倒れた場合に宣言だけで必須のレビューを越えない）。"""
    mod = _release_steps_module(monkeypatch)
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q")
    sec = strategy_fixture.section("web-develop")
    strategy_fixture.write_strategy(root, "web-develop", merge={**sec["merge"], "method": "direct", "admin": not admin})
    calls = []
    out = json.dumps({"tool": "merged", "status": "ok", "summary": "#5 をマージした", "items": []})
    monkeypatch.setattr(mod, "run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, out, ""))
    monkeypatch.setattr(mod, "merge_commit_of", lambda root, n: "c")
    mod.wait_and_merge(root, 5, admin=admin)
    assert ("--no-admin" not in calls[0]) is admin


def test_wait_and_merge_stops_without_the_declaration(monkeypatch, tmp_path):
    """#3672 の異常系: 宣言を読めなければ迂回の有無を既定へ倒さず、merge-when-green を呼ばずに止まる。"""
    mod = _release_steps_module(monkeypatch)
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q")
    calls = []
    monkeypatch.setattr(mod, "run", lambda cmd, **kw: calls.append(cmd))
    with pytest.raises(mod.StepError):
        mod.wait_and_merge(root, 5)
    assert calls == []


@pytest.mark.parametrize("arg", ["approved_sha", "approval"])
def test_cd_cases_refuse_the_approved_commit_argument(monkeypatch, arg):
    """決定 10: CD のある場合の本番は承認したコミットの引数を受けず 3 で止まる。CD の無い場合は今のまま受ける。"""
    import argparse

    mod = _release_steps_module(monkeypatch)
    a = argparse.Namespace(channel="prod", version="1.2.3", approved_sha=None, approval=None)
    setattr(a, arg, "a" * 40)
    with pytest.raises(mod.StepError) as e:
        mod.approval.approved_of(".", a, "ndf", _case("cd-no-review"))
    assert e.value.code == mod.EXIT_PRECONDITION
    a.approval, a.approved_sha = None, "a" * 40
    assert mod.approval.approved_of(".", a, "ndf", _case("no-cd")) == "a" * 40


def _check(
    monkeypatch,
    tmp_path,
    base="main",
    state="open",
    rounds=([5], [5]),
    role="admin",
    case="cd-no-review",
    required=(),
    decl_base="develop",
    head_ref="release/v1.2.3",
):
    """approval-check を流し、(結果, 付けた状態の並び) を返す。rounds は open_prs_at が順に返す番号。required は本番チャネルの
    ruleset の必須チェックの名前。decl_base は宣言のベースブランチ（本番と同じなら trunk）、head_ref は PR の head のブランチ。"""
    import argparse

    mod = _release_steps_module(monkeypatch)
    rv, at = mod.reviewed, mod.reviewed.approval_trail
    posted, left = [], list(rounds)
    monkeypatch.setattr(rv, "release_case_or_stop", lambda r: _case(case))
    monkeypatch.setattr(rv, "git_root", lambda r: tmp_path)
    monkeypatch.setattr(rv.delivery, "load_delivery", lambda r: rv.delivery.DeliveryDecl(base=decl_base, production="main"))
    monkeypatch.setattr(at, "read_head_ref", lambda repo, n: head_ref)
    monkeypatch.setattr(rv, "repo_or_stop", lambda r: "o/r")
    monkeypatch.setattr(at, "read_head", lambda repo, n: (HEAD, base, state))
    monkeypatch.setattr(at, "open_prs_at", lambda repo, sha, b: left.pop(0) if len(left) > 1 else left[0])
    trail = {"id": 1, "body": at.render_trail(HEAD, "user"), "user": {"login": "boss"}, "created_at": "t", "updated_at": "t"}
    monkeypatch.setattr(at, "read_comments", lambda repo, n: [trail])
    monkeypatch.setattr(at, "read_roles", lambda repo, logins: {"boss": role})
    monkeypatch.setattr(at, "post_status", lambda repo, sha, j: posted.append(j.state))
    rules = [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": c} for c in required]}}]
    monkeypatch.setattr(rv, "gh_json", lambda root, args, what: rules if "/rules/branches/main" in args[1] else None)
    _exits(monkeypatch, rv)
    with pytest.raises(SystemExit) as e:
        rv.approval_check(argparse.Namespace(root=str(tmp_path), pr=5))
    return e.value.code, posted


def test_approval_check_success_and_reread(monkeypatch, tmp_path):
    """I10: success を付けた後の読み直しで同じ先端の PR が増えていれば failure に付け直す。"""
    out, posted = _check(monkeypatch, tmp_path)
    assert posted == ["success"] and out["metrics"]["state"] == "success" and out["metrics"]["approver"] == "boss"
    out, posted = _check(monkeypatch, tmp_path, rounds=([5], [5, 6]))
    assert posted == ["success", "failure"] and "#6" in out["metrics"]["description"]


def test_approval_check_fails_for_write_role_and_skips_other_bases(monkeypatch, tmp_path):
    out, posted = _check(monkeypatch, tmp_path, role="write")
    assert posted == ["failure"]
    out, posted = _check(monkeypatch, tmp_path, base="develop")
    assert posted == [] and out["status"] == "ok"


def test_approval_check_on_trunk_judges_only_the_version_bump_pr(monkeypatch, tmp_path):
    """trunk（ベースと本番が同じ）では、スプリントの PR も本番宛てになる。証跡を見るのは版上げの PR（release/v<版>）だけで、
    ほかの PR には何も付けない（付けると証跡が無い failure でスプリントのマージが止まる）。"""
    out, posted = _check(monkeypatch, tmp_path, decl_base="main", head_ref="spec/aifac-1")
    assert posted == [] and out["status"] == "ok" and out["metrics"]["head_ref"] == "spec/aifac-1"
    out, posted = _check(monkeypatch, tmp_path, decl_base="main", head_ref="release/v10.28.0")
    assert posted == ["success"]


def test_approval_check_posts_nothing_for_cd_review(monkeypatch, tmp_path):
    """I8: レビューで承認する場合は証跡のチェックを付けない（付けると証跡が無い failure でマージが止まる）。"""
    out, posted = _check(monkeypatch, tmp_path, case="cd-review")
    assert posted == [] and out["status"] == "ok" and out["metrics"]["case"] == "cd-review"


def test_approval_check_judges_cd_review_when_the_status_is_required(monkeypatch, tmp_path):
    """決定 12 の手順 3: 承認数 1 のまま ruleset の必須チェックに証跡のチェックがあれば、レビュー必須でも証跡を判定して付ける。"""
    out, posted = _check(monkeypatch, tmp_path, case="cd-review", required=["ndf/release-approval"])
    assert posted == ["success"] and out["metrics"]["state"] == "success"
    out, posted = _check(monkeypatch, tmp_path, case="cd-review", required=["ci"])
    assert posted == [] and out["metrics"]["case"] == "cd-review"


def _trail_wait(monkeypatch, required, statuses):
    """wait_trail_status を流し、読んだ status の回数を返す。statuses は先端の status の読みが順に返す状態（None は付いていない）。"""
    mod = _release_steps_module(monkeypatch)
    rv = mod.reviewed
    left, reads = list(statuses), []
    rules = [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": c} for c in required]}}]

    def gh_json(root, args, what):
        if "/rules/branches/" in args[1]:
            return rules
        reads.append(1)
        st = left.pop(0) if len(left) > 1 else left[0]
        return {"statuses": [{"context": rv.approval_trail.STATUS_CONTEXT, "state": st}] if st else []}

    monkeypatch.setattr(rv, "gh_json", gh_json)
    monkeypatch.setattr(rv.waits, "wait_until", lambda read, done, policy: _Waited(read, done))
    rv.wait_trail_status(".", "o/r", "main", HEAD, 5)
    return len(reads)


class _Waited:
    def __init__(self, read, done):
        for _ in range(5):
            self.value = read()
            if done(self.value):
                break
        self.done = done(self.value)


def test_wait_trail_status_waits_for_a_present_status_even_when_not_required(monkeypatch):
    """必須でなくても先端に付いた failure は merge-when-green を止めるため、success まで待つ。付いていなければ待たない。"""
    assert _trail_wait(monkeypatch, [], [None]) == 1
    assert _trail_wait(monkeypatch, [], ["failure", "failure", "success"]) == 3
    assert _trail_wait(monkeypatch, ["ndf/release-approval"], ["pending", "success"]) == 2


def test_approval_check_rejudges_the_other_pr_when_one_closes(monkeypatch, tmp_path):
    out, posted = _check(monkeypatch, tmp_path, state="closed", rounds=([8], [8], [8]))
    assert posted == ["success"] and out["metrics"]["pr"] == 8
    out, posted = _check(monkeypatch, tmp_path, state="closed", rounds=([],))
    assert posted == []


def test_approval_check_posts_error_when_unreadable(monkeypatch, tmp_path):
    import argparse

    mod = _release_steps_module(monkeypatch)
    rv, at = mod.reviewed, mod.reviewed.approval_trail
    posted = []
    monkeypatch.setattr(rv, "git_root", lambda r: tmp_path)
    monkeypatch.setattr(rv.delivery, "load_delivery", lambda r: rv.delivery.DeliveryDecl(base="develop", production="main"))
    monkeypatch.setattr(rv, "repo_or_stop", lambda r: "o/r")
    monkeypatch.setattr(at, "read_head", lambda repo, n: (HEAD, "main", "open"))
    monkeypatch.setattr(at, "open_prs_at", lambda *a: (_ for _ in ()).throw(at.TrailError("unreadable", "読めない")))
    monkeypatch.setattr(at, "post_status", lambda repo, sha, j: posted.append(j.state))
    with pytest.raises(mod.StepError):
        rv.approval_check(argparse.Namespace(root=str(tmp_path), pr=5))
    assert posted == ["error"]


def test_approval_post_writes_once_per_head(monkeypatch, tmp_path):
    import argparse

    mod = _release_steps_module(monkeypatch)
    rv, at = mod.reviewed, mod.reviewed.approval_trail
    material = tmp_path / "approval.md"
    material.write_text(f"# 承認資料\n\n| 項目 | 値 |\n| --- | --- |\n| 承認したコミット | {HEAD} |\n", encoding="utf-8")
    comments, written = [], []

    class Ok:
        ok, error, value = True, "", {"url": "https://x/c/2"}

    monkeypatch.setattr(rv, "git_root", lambda r: tmp_path)
    monkeypatch.setattr(rv, "repo_or_stop", lambda r: "o/r")
    monkeypatch.setattr(at, "read_head", lambda repo, n: (HEAD, "main", "open"))
    monkeypatch.setattr(at, "read_comments", lambda repo, n: comments)
    monkeypatch.setattr(rv.gh_rest, "comment", lambda repo, n, body: written.append(body) or comments.append({"body": body}) or Ok())
    _exits(monkeypatch, rv)
    for _ in range(2):
        with pytest.raises(SystemExit):
            rv.approval_post(argparse.Namespace(root=str(tmp_path), pr=9, approval=str(material)))
    assert len(written) == 1 and f"`{HEAD}`" in written[0] and at.parse_trail(written[0]) is None
    with pytest.raises(mod.StepError) as e:
        rv.approval_post(argparse.Namespace(root=str(tmp_path), pr=9, approval=str(tmp_path / "missing.md")))
    assert e.value.code == mod.EXIT_PRECONDITION
    # 同じ先端で資料を作り直したら、古いコメントを使い回さずに置き直す
    material.write_text(material.read_text(encoding="utf-8") + "\n追記\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        rv.approval_post(argparse.Namespace(root=str(tmp_path), pr=9, approval=str(material)))
    assert len(written) == 2 and "追記" in written[1]


def test_approval_post_refuses_a_material_for_another_head(monkeypatch, tmp_path):
    """資料の「承認したコミット」が PR の先端と違えば、承認資料のコメントを置かずに 3 で止まる。"""
    import argparse

    mod = _release_steps_module(monkeypatch)
    rv, at = mod.reviewed, mod.reviewed.approval_trail
    material = tmp_path / "approval.md"
    material.write_text(f"| 項目 | 値 |\n| --- | --- |\n| 承認したコミット | {'d' * 40} |\n", encoding="utf-8")
    written = []
    monkeypatch.setattr(rv, "git_root", lambda r: tmp_path)
    monkeypatch.setattr(rv, "repo_or_stop", lambda r: "o/r")
    monkeypatch.setattr(at, "read_head", lambda repo, n: (HEAD, "main", "open"))
    monkeypatch.setattr(at, "read_comments", lambda repo, n: [])
    monkeypatch.setattr(rv.gh_rest, "comment", lambda repo, n, body: written.append(body))
    with pytest.raises(mod.StepError) as e:
        rv.approval_post(argparse.Namespace(root=str(tmp_path), pr=9, approval=str(material)))
    assert e.value.code == mod.EXIT_PRECONDITION and "先端" in str(e.value) and written == []


def test_approve_on_pr_writes_the_trail_and_gates_on_a_moved_head(monkeypatch, tmp_path):
    """受け入れ条件 3: approve --pr は証跡を書く。先端が承認したコミットと違えば書かずに 10。"""
    import argparse

    mod = _release_steps_module(monkeypatch)
    ap = mod.approval
    monkeypatch.setattr(ap.repo_lib, "owner_repo", lambda r: "o/r")
    monkeypatch.setattr(ap.trail, "write_trail", lambda repo, pr, sha, by: (True, "https://x/c/3"))
    a = argparse.Namespace(pr=9, approved_sha=HEAD, by="user", approval=None, facts_from=None)
    out = ap.approve(tmp_path, a)
    assert out["status"] == "ok" and out["metrics"]["comment_url"] == "https://x/c/3"

    def moved(*_):
        raise ap.trail.TrailError("head_moved", "動いた")

    monkeypatch.setattr(ap.trail, "write_trail", moved)
    with pytest.raises(ap.Gate):
        ap.approve(tmp_path, a)
    with pytest.raises(mod.StepError):
        ap.approve(tmp_path, argparse.Namespace(pr=9, approved_sha=None, by="user", approval=None, facts_from=None))


def test_release_dev_runs_the_declared_workflow_only_for_an_open_pr(monkeypatch, tmp_path):
    """受け入れ条件 1・15: 宣言があれば開発版の PR のマージを workflow に任せ、マージ済みなら起動しない。"""
    import argparse

    from release_lib.context import ReleaseCtx

    mod = _release_steps_module(monkeypatch)
    rv = mod.reviewed
    runs = []
    monkeypatch.setattr(mod, "merge_commit_of", lambda r, n: "c")
    monkeypatch.setattr(mod, "_merged_sync_pr", lambda r, b, base: None)
    monkeypatch.setattr(mod, "pr_view", lambda r, n, f: {"mergeStateStatus": "CLEAN"})
    monkeypatch.setattr(rv, "release_dev_by_workflow", lambda r, a, wf, n: runs.append((wf, n)) or "https://x/runs/1")
    refreshed = []
    monkeypatch.setattr(mod, "changelog_section", lambda r, v, p: "- 変化")
    monkeypatch.setattr(mod, "run_checks", lambda r: [])
    monkeypatch.setattr(
        rv.gh_parts, "gh", lambda args, stdin=None, cwd=None: refreshed.append((args[2], stdin)) or SimpleNamespace(returncode=0, stderr="")
    )
    _exits(monkeypatch, rv)
    a = argparse.Namespace(version="1.2.3-dev.1", channel="dev")
    for state in ("OPEN", "MERGED"):
        monkeypatch.setattr(mod, "_reusable_release_pr", lambda r, b, base, s=state: {"number": 4, "state": s})
        with pytest.raises(SystemExit) as e:
            rv.release_dev(ReleaseCtx(tmp_path, a, "ndf", ["ndf"], "release/v1.2.3-dev.1", "develop", "main"), "release-dev.yml", mod)
        assert e.value.code["metrics"]["dev_channel_workflow"] == "release-dev.yml"
    assert runs == [("release-dev.yml", 4)]
    # 使い回す開いた PR だけ、本文を今の版の本文（ndf-next の port 検査の印を持つ）へ書き直す（#3343）
    assert [n for n, _ in refreshed] == ["4"] and mod.port_mark.NO_PORT_LINE in refreshed[0][1]


@pytest.mark.parametrize("has_line", [False, True])
def test_ensure_no_port_line_appends_the_mark_to_a_reused_pr(monkeypatch, tmp_path, has_line):
    """前の版が作った -sync の PR を使い回すとき、本文に port 検査の印が無ければ、ほかの行を残して末尾へ足す（#3343）。"""
    from release_lib import port_mark as pm

    body = "解いたパス: README.md" + (f"\n\n{pm.NO_PORT_LINE}" if has_line else "")
    monkeypatch.setattr(pm, "gh_json", lambda root, args, what: {"body": body})
    edited = []

    def gh(args, stdin=None, cwd=None):
        if args[1] == "checks":
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")
        edited.append((args[2], stdin))
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(pm.gh_parts, "gh", gh)
    assert pm.ensure_no_port_line(tmp_path, {"number": 7}) == {"number": 7} and pm.ensure_no_port_line(tmp_path, None) is None
    if has_line:
        assert edited == []
    else:
        assert [n for n, _ in edited] == ["7"] and edited[0][1].startswith("解いたパス: README.md") and pm.NO_PORT_LINE in edited[0][1]


def test_ensure_no_port_line_waits_until_the_failed_check_is_rerun(monkeypatch, tmp_path):
    """編集の前に落ちていたチェックは、本文の編集で打ち直されて落ちた状態から外れるまで待ってから戻る（#3573）。
    待たずに戻ると、続く CI の待ちが打ち直す前の失敗を読んで止まる。"""
    import json as _json

    from release_lib import port_mark as pm

    monkeypatch.setattr(pm, "gh_json", lambda root, args, what: {"body": "解いたパス: README.md"})
    reads = iter(
        [
            [{"name": "ndf-next-checks", "bucket": "fail"}, {"name": "ndf/release-approval", "bucket": "fail"}],  # 編集の前
            [{"name": "ndf-next-checks", "bucket": "fail"}, {"name": "ndf/release-approval", "bucket": "fail"}],  # 未登録
            [{"name": "ndf-next-checks", "bucket": "pending"}, {"name": "ndf/release-approval", "bucket": "fail"}],
        ]
    )
    calls = []

    def gh(args, stdin=None, cwd=None):
        calls.append(args[1])
        stdout = _json.dumps(next(reads)) if args[1] == "checks" else ""
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(pm.gh_parts, "gh", gh)
    monkeypatch.setattr(pm.waits, "wait_until", lambda read, done, policy: _Waited(read, done))
    assert pm.ensure_no_port_line(tmp_path, {"number": 7}) == {"number": 7}
    assert calls == ["checks", "edit", "checks", "checks"]


def test_ensure_no_port_line_does_not_wait_for_checks_the_mark_does_not_change(monkeypatch, tmp_path):
    """port 検査のほかに落ちているチェック（証跡が無い間の ndf/release-approval など）は、本文の編集で変わらないため待たない（#3573）。"""
    import json as _json

    from release_lib import port_mark as pm

    monkeypatch.setattr(pm, "gh_json", lambda root, args, what: {"body": "解いたパス: README.md"})
    calls = []

    def gh(args, stdin=None, cwd=None):
        calls.append(args[1])
        rows = [{"name": "ndf-next-checks", "bucket": "pass"}, {"name": "ndf/release-approval", "bucket": "fail"}]
        return SimpleNamespace(returncode=0, stdout=_json.dumps(rows) if args[1] == "checks" else "", stderr="")

    monkeypatch.setattr(pm.gh_parts, "gh", gh)
    monkeypatch.setattr(pm.waits, "wait_until", lambda read, done, policy: pytest.fail("待たない"))
    assert pm.ensure_no_port_line(tmp_path, {"number": 7}) == {"number": 7}
    assert calls == ["checks", "edit"]


@pytest.mark.parametrize("synced", [None, 6])
def test_release_dev_takes_a_conflicting_pr_through_the_sync_pr(monkeypatch, tmp_path, synced):
    """開発版の PR がベースブランチと衝突していれば -sync の PR で取り込み、そのマージも workflow に任せる。
    再開で -sync が取り込み済みなら workflow を起動しない。"""
    import argparse

    from release_lib.context import ReleaseCtx

    mod = _release_steps_module(monkeypatch)
    rv = mod.reviewed
    runs, syncs = [], []
    monkeypatch.setattr(mod, "merge_commit_of", lambda r, n: f"c{n}")
    monkeypatch.setattr(mod, "_reusable_release_pr", lambda r, b, base: {"number": 4, "state": "OPEN"})
    monkeypatch.setattr(mod, "_merged_sync_pr", lambda r, b, base: synced)
    monkeypatch.setattr(mod, "pr_view", lambda r, n, f: {"mergeStateStatus": "DIRTY"})
    monkeypatch.setattr(rv, "release_dev_by_workflow", lambda r, a, wf, n: runs.append(n) or f"https://x/runs/{n}")
    monkeypatch.setattr(mod, "_release_pr_body", lambda r, a, p, ps: "本文")
    monkeypatch.setattr(rv.gh_parts, "gh", lambda args, stdin=None, cwd=None: SimpleNamespace(returncode=0, stderr=""))

    def merge_sync(root, a, plugin, branch, base, release_pr, merge=None):
        syncs.append(release_pr)
        return 5, merge(5)

    monkeypatch.setattr(mod, "_merge_sync_pr", merge_sync)
    _exits(monkeypatch, rv)
    with pytest.raises(SystemExit) as e:
        ctx = ReleaseCtx(tmp_path, argparse.Namespace(version="1.2.3-dev.1"), "ndf", ["ndf"], "release/v1.2.3-dev.1", "develop", "main")
        rv.release_dev(ctx, "release-dev.yml", mod)
    out = e.value.code
    want = synced or 5
    assert (out["metrics"]["sync_pr"], out["metrics"]["merge_commit"]) == (want, f"c{want}")
    assert out["items"][0]["result"] == "compared" and out["items"][1]["name"] == f"#{want}"
    assert (runs, syncs) == (([], []) if synced else ([5], [4]))


@pytest.mark.parametrize("code, stderr, want", [(0, "", True), (1, "gh: Not Found (HTTP 404)", False)])
def test_dev_workflow_ready_reads_the_default_branch(monkeypatch, tmp_path, code, stderr, want):
    """宣言の workflow が既定ブランチに無ければ（404）手元でマージする側へ戻す。"""
    import subprocess

    sys.path.insert(0, str(SCRIPT.parent))
    from release_lib import reviewed

    seen = []
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(
        reviewed.gh_parts, "gh", lambda args, cwd=None: seen.append(args) or subprocess.CompletedProcess(args, code, "", stderr)
    )
    assert reviewed.dev_workflow_ready(tmp_path, "release-dev.yml") is want
    assert seen[0][1] == "repos/o/r/contents/.github/workflows/release-dev.yml"


def test_dev_run_title_comes_from_the_workflow_file(tmp_path):
    sys.path.insert(0, str(SCRIPT.parent))
    from release_lib import reviewed

    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "release-dev.yml").write_text('name: release-dev\nrun-name: "release-dev #${{ inputs.pr }}"\n', encoding="utf-8")
    assert reviewed.dev_run_title(tmp_path, "release-dev.yml", 12) == "release-dev #12"


# --- #3703: 本番の取り込み（prod_land）と CD の無い trunk の本番（trunk_release） --------------------------------------


class _LandPort:
    """`land.Port` の偽物。取り込みの呼び出しを `calls` に残す。"""

    def __init__(self, head=HEAD, method="merge-queue", commit="merge"):
        self.head, self.calls = head, []
        self.facts = SimpleNamespace(
            present=True,
            queue=method == "merge-queue",
            queue_merge_method=commit,
            up_to_date=True,
            bypass="never",
            approvals=0,
            required=(),
        )

    def pull(self, n):
        row = {"number": n, "state": "open", "merged": False, "draft": False, "head": self.head, "base": "main", "title": "Release"}
        return {**row, "bump": True, "cut_version": "1.0.0", "contains_tip": True, "tip": "t" * 40}

    def production(self, branch):
        return {"tip": "t" * 40, "version": "1.0.0"}

    def queue(self, branch):
        return []

    def rule_facts(self, branch):
        return self.facts

    def merge(self, n, sha, commit, admin):
        self.calls.append(("merge", sha, commit, admin))
        return True, ""

    def enqueue(self, n, head, jump):
        self.calls.append(("enqueue", head))
        return True, ""

    def checks_failing(self, sha, pull=None, required=()):
        return None

    def has_human_record(self, n):
        return False

    def comment(self, n, body):
        return True


def _landing_io(merge=None, state="open", head=HEAD, ci=None, after_wait="merged"):
    """prod_land.land の外（宣言・GitHub・待ち）を偽の操作で組み、(LandingIO, 偽の Port) を返す。"""
    import branch_strategy

    from release_lib import prod_land as pl

    sec = strategy_fixture.section("plugin")
    sec["merge"] = {**sec["merge"], **(merge or {})}
    port = _LandPort(head, sec["merge"]["method"], sec["merge"]["commit"])
    io = pl.LandingIO(
        strategy=lambda root: branch_strategy.Strategy(sec),
        repo=lambda root: "o/r",
        queue_state=lambda root, pr: state,
        wait_queued=lambda root, pr, timeout: after_wait,
        port=lambda root, repo, strategy: port,
        merge_commit=lambda root, pr: "m" * 40,
        fetch=lambda root: None,
        wait_ci=ci or (lambda *a: None),
        wait_trail=lambda *a, **k: None,
    )
    return io, port


def _prod_land(merge=None, state="open", head=HEAD, ci=None, after_wait="merged"):
    """prod_land.land を偽の LandingIO で呼ぶ関数と、偽の Port を返す。"""
    import functools

    from release_lib import prod_land as pl

    io, port = _landing_io(merge, state, head, ci, after_wait)
    return functools.partial(pl.land, io=io), port


_LAND_ARGS = SimpleNamespace(ci_wait=60.0)


def test_prod_land_puts_the_pr_in_the_merge_queue_without_merging(monkeypatch):
    """#3703 の受け入れ条件 1・I4: merge-queue の宣言では PR を merge queue へ入れ、直接マージせず、公開の欠け（LandPending）で返す。"""
    from release_lib.prod_land import LandPending

    land, port = _prod_land()
    with pytest.raises(LandPending) as e:
        land(Path("."), _LAND_ARGS, 9, HEAD)
    assert e.value.result == "queued" and port.calls == [("enqueue", HEAD)]


def test_prod_land_leaves_the_merge_to_a_human(monkeypatch):
    """#3703 の受け入れ条件 3: merge.actor が human ならマージも merge queue への投入もせず、人のマージを待つ。"""
    from release_lib.prod_land import LandPending

    land, port = _prod_land({"actor": "human"})
    with pytest.raises(LandPending) as e:
        land(Path("."), _LAND_ARGS, 9, HEAD)
    assert e.value.result == "human" and port.calls == []


@pytest.mark.parametrize("method", ["strict", "direct"])
@pytest.mark.parametrize("commit", ["merge", "squash", "rebase"])
def test_prod_land_merges_with_the_declared_history(monkeypatch, method, commit):
    """#3703 の受け入れ条件 4: strict・direct は宣言の merge.commit の方式で承認した head を入れる。"""
    land, port = _prod_land({"method": method, "commit": commit})
    assert land(Path("."), _LAND_ARGS, 9, HEAD) == "m" * 40
    assert port.calls == [("merge", HEAD, commit, False)]


@pytest.mark.parametrize(("recorded", "want"), [("f" * 40, "f" * 40), (None, None)])
def test_prod_land_returns_the_fast_forward_commit_without_a_merge_commit(monkeypatch, recorded, want):
    """#3703: ruleset の無い strict の早送りは PR を閉じるだけで mergeCommit が無い。取り込みの記録のコミットを返し、
    呼び手が本番ブランチの先端（取り込みの後に入った承認の外の変更を含みうる）へタグを打たないようにする。"""
    import dataclasses

    from release_lib import prod_land as pl

    io, port = _landing_io({"method": "strict"})
    io = dataclasses.replace(io, merge_commit=lambda root, pr: None)
    row = {"head": HEAD, "tree_ok": True, "first_parent_is_tip": True, "commit": recorded}
    port.landed_record = lambda branch, n, since: row if recorded else None
    monkeypatch.setattr(pl.land_lib, "land_approved", lambda *a, **k: {"result": "merged", "head": HEAD, "reason": ""})
    assert pl.land(Path("."), _LAND_ARGS, 9, HEAD, io=io) == want


@pytest.mark.parametrize("code", [1, 75])
def test_prod_land_waits_for_the_checks_before_landing(monkeypatch, code):
    """#3703 の受け入れ条件 5・I3: 検査の中身の失敗は 1、上限は 75 で止まり、本番ブランチも merge queue も変えない。"""
    from step_result import StepError

    def ci(*a):
        raise StepError("落ちた", code)

    land, port = _prod_land(ci=ci)
    with pytest.raises(StepError) as e:
        land(Path("."), _LAND_ARGS, 9, HEAD)
    assert e.value.code == code and port.calls == []


def test_prod_land_does_not_land_a_moved_head(monkeypatch):
    """#3703 の受け入れ条件 6・I2: 承認した head と PR の今の head が違えば、入れずに HeadMoved を投げる（merge queue へも入れない）。"""
    from release_lib.approval import HeadMoved

    land, port = _prod_land(head="d" * 40)
    with pytest.raises(HeadMoved):
        land(Path("."), _LAND_ARGS, 9, HEAD)
    assert port.calls == []


@pytest.mark.parametrize(
    ("state", "after", "want"),
    [("merged", None, "merged"), ("queued", "merged", "merged"), ("queued", "queued", 75), ("closed", None, 1), ("queued", "closed", 1)],
)
def test_prod_land_rerun_reads_the_merge_queue(monkeypatch, state, after, want):
    """#3703 の決定 3: 打ち直しで merge queue にあれば上限まで待ち、マージされればマージコミット、上限なら 75、閉じていれば 1。
    どれも入れ直さない。"""
    from step_result import StepError

    land, port = _prod_land(state=state, after_wait=after)
    if want == "merged":
        assert land(Path("."), _LAND_ARGS, 9, HEAD) == "m" * 40
    else:
        with pytest.raises(StepError) as e:
            land(Path("."), _LAND_ARGS, 9, HEAD)
        assert e.value.code == want
    assert port.calls == []


@pytest.mark.parametrize("state", ["merged", "queued"])
def test_prod_land_stops_when_the_merged_head_is_not_the_approved_one(monkeypatch, state):
    """#3703 の I2: 照会の直前か merge queue を待つ間に、承認した head と違う head が入っていれば、マージコミットを返さず 1 で止める。"""
    from step_result import StepError

    land, port = _prod_land(state=state, head="d" * 40, after_wait="merged")
    with pytest.raises(StepError) as e:
        land(Path("."), _LAND_ARGS, 9, HEAD)
    assert e.value.code == 1 and "承認した" in str(e.value) and port.calls == []


def test_prod_land_reenqueues_a_pr_that_left_the_merge_queue(monkeypatch):
    """#3703 の決定 3: merge queue から外れて開いていれば、検査の待ちと head の照合からやり直して入れ直す。"""
    from release_lib.prod_land import LandPending

    land, port = _prod_land(state="queued", after_wait="open")
    with pytest.raises(LandPending):
        land(Path("."), _LAND_ARGS, 9, HEAD)
    assert port.calls == [("enqueue", HEAD)]


def test_prod_land_uses_the_default_io_when_omitted(monkeypatch):
    """io を省いた land（CLI の通常の呼び出し）は、呼ぶ時点の default_io() の操作を使う。"""
    from release_lib import prod_land as pl

    io, port = _landing_io()
    monkeypatch.setattr(pl, "default_io", lambda: io)
    with pytest.raises(pl.LandPending):
        pl.land(Path("."), _LAND_ARGS, 9, HEAD)
    assert port.calls == [("enqueue", HEAD)]


@pytest.fixture
def prod_land_decl(monkeypatch):
    """prod_land の宣言とリポジトリの読み（メインディレクトリの宣言・origin の owner/repo）を差し替え、モジュールを返す。

    差し替え先: `prod_land._repo`（"o/r"）・`prod_land._strategy`（宣言の見本 plugin）。prod_land の中で宣言や
    リポジトリの読み方を分け直したときは、ここの 1 か所を直す（`land` は差し替えずに `LandingIO` で渡す）。"""
    import branch_strategy

    from release_lib import prod_land as pl

    monkeypatch.setattr(pl, "_repo", lambda root: "o/r")
    monkeypatch.setattr(pl, "_strategy", lambda root: branch_strategy.Strategy(strategy_fixture.section("plugin")))
    return pl


@pytest.mark.parametrize(
    ("node", "want"),
    [
        ({"merged": True, "state": "MERGED", "isInMergeQueue": False}, "merged"),
        ({"merged": False, "state": "OPEN", "isInMergeQueue": True}, "queued"),
        ({"merged": False, "state": "OPEN", "isInMergeQueue": False}, "open"),
        ({"merged": False, "state": "CLOSED", "isInMergeQueue": False}, "closed"),
    ],
)
def test_queue_state_maps_the_pr_to_four_states(monkeypatch, prod_land_decl, node, want):
    pl = prod_land_decl
    got = SimpleNamespace(ok=True, value={"repository": {"pullRequest": node}}, error="")
    monkeypatch.setattr(pl.gh_graphql, "graphql", lambda q, v: got)
    assert pl.queue_state(Path("."), 9) == want


def test_queue_state_does_not_guess_when_unreadable(monkeypatch, prod_land_decl):
    """読めなければ merge queue に無いとみなさず止まる（merge queue に残る PR を二重に入れない）。"""
    from step_result import StepError

    pl = prod_land_decl
    monkeypatch.setattr(pl.gh_graphql, "graphql", lambda q, v: SimpleNamespace(ok=False, value=None, error="boom"))
    with pytest.raises(StepError):
        pl.queue_state(Path("."), 9)


@pytest.mark.parametrize("result", ["queued", "human"])
def test_pending_result_reports_the_publish_gap(result):
    """#3703 の受け入れ条件 1・3・決定 9: merge queue へ入れた・人のマージを待つは stopped と gaps の公開の行で返す（終了コード 6）。"""
    from release_lib import prod_land as pl
    from step_result import code_for

    out = pl.pending_result("foo", "1.2.3", "main", "foo--v1.2.3", pl.LandPending(result, "", 9), "打ち直す")
    assert out["status"] == "stopped" and code_for(out) == 6 and out["next"] == "打ち直す"
    assert out["gaps"] == [{"kind": "publish", "name": "foo--v1.2.3", "reason": pl.PENDING_REASONS[result]}]
    assert out["items"] == [{"kind": "pr", "name": "#9", "result": result, "base": "main"}]


def test_cmd_release_returns_6_and_publishes_nothing_while_the_merge_queue_holds_the_pr(monkeypatch, tmp_path):
    """#3703 の受け入れ条件 1・I4: 本番の取り込みが merge queue へ入れたら、タグも GitHub Release も作らずに 6 で返す。"""
    from release_lib import prod_land as pl

    def land(r, a, n, head, trail_url=""):
        raise pl.LandPending("queued", "", n)

    found, created, calls, out = _release_prod(monkeypatch, tmp_path, ("main", "main"), "foo", land=land)
    assert out["_code"] == 6 and out["status"] == "stopped" and out["gaps"][0]["kind"] == "publish"
    assert not any(c[0] == "tag" for c in calls)


class _TrunkOps:
    """trunk_release が読む `release-steps.py` の操作の偽物。"""

    def __init__(self, head=HEAD, merged=None, open_pr=None, land=None):
        self.head, self.merged_pr, self.open_pr = head, merged, open_pr
        self.created, self.landed = [], []
        self._land = land

    def git(self, root, *args, check=True):
        rc = 1 if args[:3] == ("rev-parse", "-q", "--verify") else 0
        return subprocess.CompletedProcess(args, rc, "p" * 40 if args[0] == "rev-parse" else "", "")

    def find_pr(self, root, head, base, states):
        if "MERGED" in states and self.merged_pr:
            return {"number": self.merged_pr, "state": "MERGED"}
        if "OPEN" in states and self.open_pr:
            return {"number": self.open_pr, "state": "OPEN"}
        return None

    def create_pr(self, root, base, head, title, body):
        self.created.append((base, head))
        return 5

    def _release_pr_body(self, root, a, plugin, plugins):
        return "body"

    def pr_view(self, root, n, fields):
        return {"headRefOid": self.head}

    def merge_commit_of(self, root, n):
        return "m" * 40

    def pr_check_buckets(self, root, n):
        return []

    def land(self, root, a, n, head, trail_url=""):
        if self._land:
            return self._land(root, a, n, head)
        self.landed.append((n, head))
        return "m" * 40


def _trunk(monkeypatch, tmp_path, ops, approved=HEAD):
    """trunk_release.release_trunk を偽の操作で流す。止まれば (結果, 終了コード)、戻れば戻り値を返す。"""
    import argparse

    from release_lib import trunk_release as tr
    from release_lib.context import ReleaseCtx

    codes = _exits(monkeypatch, tr)
    monkeypatch.setattr(tr.names, "tag_name", lambda root, plugin, ver: f"{plugin}--v{ver}")
    monkeypatch.setattr(tr.names, "shipped_note", lambda root, plugin, ver: None)
    monkeypatch.setattr(tr.reviewed, "wait_ci_except", lambda *a: None)
    a = argparse.Namespace(version="1.2.3", ci_wait=60.0, approved_sha=approved, approval=None)
    ctx = ReleaseCtx(tmp_path, a, "foo", ["foo"], "release/v1.2.3", "main", "main", sync=False)
    try:
        return tr.release_trunk(ctx, ops)
    except SystemExit as e:
        return e.code, codes[-1]


def test_release_trunk_opens_the_release_pr_and_stops_at_the_gate_without_the_approval(monkeypatch, tmp_path):
    """#3703 の決定 4・受け入れ条件 12: 承認の前の 1 回目は版上げの PR を開いて 10 で止まり、本番の取り込みを打たない。"""
    ops = _TrunkOps()
    out, code = _trunk(monkeypatch, tmp_path, ops, approved=None)
    assert code == 10 and out["status"] == "gate" and ops.created == [("main", "release/v1.2.3")] and ops.landed == []
    assert out["items"][-1]["result"] == "awaiting_approval" and "approve" in out["next"]


def test_release_trunk_lands_the_approved_head(monkeypatch, tmp_path):
    """#3703 の受け入れ条件 7・I1: 承認と版上げの PR の head が同じなら、開いた PR を使い回して本番の取り込みを 1 回通る。"""
    ops = _TrunkOps(open_pr=4)
    pr, merge, items, metrics = _trunk(monkeypatch, tmp_path, ops)
    assert (pr, merge) == (4, "m" * 40) and ops.created == [] and ops.landed == [(4, HEAD)]
    assert items[-1]["result"] == "merged" and metrics["approved_sha"] == HEAD


def test_release_trunk_does_not_land_commits_after_the_approval(monkeypatch, tmp_path):
    """#3703 の受け入れ条件 6・I2: 承認の後に版上げのブランチへ積まれた head は入れず、承認ゲート（10）で取り直しを求める。"""
    ops = _TrunkOps(head="d" * 40, open_pr=4)
    out, code = _trunk(monkeypatch, tmp_path, ops)
    assert code == 10 and ops.landed == [] and "approve" in out["next"]


def test_release_trunk_stops_at_the_gate_when_the_head_moves_while_landing(monkeypatch, tmp_path):
    from release_lib.approval import HeadMoved

    def moved(*a):
        raise HeadMoved("動いた")

    out, code = _trunk(monkeypatch, tmp_path, _TrunkOps(open_pr=4, land=moved))
    assert code == 10 and "動いた" in out["summary"]


@pytest.mark.parametrize(("head", "ok"), [(HEAD, True), ("d" * 40, False)])
def test_release_trunk_resumes_a_merged_release_pr_by_its_head(monkeypatch, tmp_path, head, ok):
    """#3703 の受け入れ条件 2・I2: マージ済みの版上げの PR は、その head が承認の SHA と同じときだけ公開へ進み、入れ直さない。"""
    from step_result import StepError

    ops = _TrunkOps(head=head, merged=4)
    if ok:
        pr, merge, items, _ = _trunk(monkeypatch, tmp_path, ops)
        assert (pr, merge, items[-1]["result"]) == (4, "m" * 40, "already_merged") and ops.landed == []
        return
    with pytest.raises(StepError) as e:
        _trunk(monkeypatch, tmp_path, ops)
    assert e.value.code == 1 and ops.landed == []


def test_release_trunk_resumes_a_fast_forwarded_pr_from_the_landed_record(monkeypatch, tmp_path):
    """#3703 の I2: ruleset の無い strict の squash で閉じた PR は、取り込みの記録があればマージ済みと同じに公開へ進む。"""
    from release_lib import trunk_release as tr

    ops = _TrunkOps()
    ops.find_pr = lambda root, head, base, states: {"number": 4, "state": "CLOSED"} if states == ("CLOSED",) else None
    monkeypatch.setattr(tr.prod_land, "landed_commit", lambda root, pr, head: "f" * 40)
    pr, merge, items, _ = _trunk(monkeypatch, tmp_path, ops)
    assert (pr, merge, items[-1]["result"]) == (4, "f" * 40, "already_merged") and ops.landed == []
