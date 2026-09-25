"""配布の PR で重いジョブを省くかの判定（`scripts/ci-heavy-skip.py`、#1055）。

一時ディレクトリに最小のリポジトリを作り、版上げの差分とコードの差分を比べる。
GitHub の API は偽物（呼ばれたパスから返す値を決める関数）で置き換える。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ci-heavy-skip.py"
RELEASE_STEPS = REPO / "plugins" / "ndf" / "scripts" / "release-steps.py"


def load(path: Path, name: str, monkeypatch):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, mod)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod(monkeypatch):
    return load(SCRIPT, "ci_heavy_skip_mod", monkeypatch)


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def commit(root: Path, msg: str = "c") -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)
    return git(root, "rev-parse", "HEAD")


def manifest(version: str, name: str = "ndf") -> str:
    return json.dumps({"name": name, "version": version,
                       "description": f"NDF (v{version})", "skills": "./skills/"}, indent=2) + "\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "develop")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    write(root, "plugins/ndf/.claude-plugin/plugin.json", manifest("1.0.0"))
    write(root, "plugins/ndf/.codex-plugin/plugin.json", manifest("1.0.0"))
    write(root, ".claude-plugin/marketplace.json", manifest("1.0.0", "ai-plugins"))
    write(root, "plugins/ndf/README.md", "## v1.0.0 へ更新するとき\n")
    write(root, "plugins/ndf/scripts/tool.py", "print(1)\n")
    write(root, "scripts/tests/test_x.py", "def test_x():\n    pass\n")
    write(root, "CHANGELOG.md", "# Changelog\n")
    write(root, "README.md", "NDFプラグイン v1.0.0\n")
    write(root, "docs/other.md", "other\n")
    commit(root, "base")
    return root


def bump(root: Path, version: str = "1.0.1") -> None:
    for rel in ("plugins/ndf/.claude-plugin/plugin.json", "plugins/ndf/.codex-plugin/plugin.json"):
        write(root, rel, manifest(version))
    write(root, ".claude-plugin/marketplace.json", manifest(version, "ai-plugins"))
    write(root, "plugins/ndf/README.md", f"## v{version} へ更新するとき\n\n- 本文を書き直した\n")
    write(root, "CHANGELOG.md", f"# Changelog\n\n## v{version}\n\n- #1 を足した\n")
    write(root, "README.md", f"NDFプラグイン v{version}\n")


def none_passed(path: str) -> dict:
    return {"workflow_runs": []}


def decide(mod, root: Path, base: str, head: str, api=none_passed, event="pull_request"):
    return mod.decide(str(root), event, base, head, "o/r", "pytest.yml", api=api)


# --- 1. 版数と説明だけの差分 -------------------------------------------------------

def test_version_and_description_only_diff_skips(mod, repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-q", "-c", "release/v1.0.1")
    bump(repo)
    head = commit(repo, "bump")
    skip, reason = decide(mod, repo, base, head)
    assert skip, reason


@pytest.mark.parametrize("rel, text", [
    ("plugins/ndf/scripts/tool.py", "print(2)\n"),
    ("scripts/tests/test_x.py", "def test_x():\n    assert True\n"),
    ("docs/other.md", "changed\n"),
    (".github/workflows/pytest.yml", "on: push\n"),
])
def test_one_line_outside_version_and_description_runs(mod, repo, rel, text):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-q", "-c", "release/v1.0.1")
    bump(repo)
    write(repo, rel, text)
    head = commit(repo, "bump and more")
    skip, reason = decide(mod, repo, base, head)
    assert not skip
    assert rel in reason


def test_manifest_change_other_than_version_runs(mod, repo):
    base = git(repo, "rev-parse", "HEAD")
    data = json.loads((repo / "plugins/ndf/.claude-plugin/plugin.json").read_text())
    data["skills"] = "./other/"
    write(repo, "plugins/ndf/.claude-plugin/plugin.json", json.dumps(data, indent=2) + "\n")
    head = commit(repo)
    skip, reason = decide(mod, repo, base, head)
    assert not skip and "plugin.json" in reason


def test_new_manifest_file_runs(mod, repo):
    base = git(repo, "rev-parse", "HEAD")
    write(repo, "plugins/new/.claude-plugin/plugin.json", manifest("0.1.0", "new"))
    head = commit(repo)
    assert not decide(mod, repo, base, head)[0]


def test_push_never_skips(mod, repo):
    base = git(repo, "rev-parse", "HEAD")
    bump(repo)
    head = commit(repo)
    assert not decide(mod, repo, base, head, event="push")[0]


# --- 2. develop で通ったコミットを進める main 宛の PR -------------------------------

def release_history(repo: Path) -> tuple[str, str, str]:
    """main ← develop の形を作る。(main の先端, develop で通った P, 版上げを入れた M) を返す。"""
    main = git(repo, "rev-parse", "HEAD")
    git(repo, "branch", "main", main)
    write(repo, "plugins/ndf/scripts/tool.py", "print('feature')\n")
    p = commit(repo, "feature")  # develop へ入った機能の変更（develop の push で CI を通る）
    git(repo, "switch", "-q", "-c", "release/v1.0.1")
    bump(repo)
    commit(repo, "bump")
    git(repo, "switch", "-q", "develop")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge release", "release/v1.0.1")
    return main, p, git(repo, "rev-parse", "HEAD")


def fake_api(passed: set[str], calls: list[str] | None = None):
    def api(path: str) -> dict:
        if calls is not None:
            calls.append(path)
        sha = path.split("head_sha=")[1].split("&")[0]
        assert "event=push" in path and "branch=develop" in path and "/workflows/pytest.yml/" in path
        runs = [{"head_sha": sha, "event": "push", "head_branch": "develop", "conclusion": "success"}]
        return {"workflow_runs": runs if sha in passed else []}
    return api


def test_main_pr_whose_head_passed_on_develop_skips(mod, repo):
    main, _, m = release_history(repo)
    skip, reason = decide(mod, repo, main, m, api=fake_api({m}))
    assert skip and m[:12] in reason


def test_main_pr_skips_when_first_parent_passed_and_rest_is_version_only(mod, repo):
    """版上げのマージ M の push の CI が走っている間でも、その前の P が通っていれば省く。"""
    main, p, m = release_history(repo)
    skip, reason = decide(mod, repo, main, m, api=fake_api({p}))
    assert skip and p[:12] in reason


def test_main_pr_runs_when_nothing_passed_on_develop(mod, repo):
    main, _, m = release_history(repo)
    calls: list[str] = []
    assert not decide(mod, repo, main, m, api=fake_api(set(), calls))[0]
    assert calls  # 問い合わせた上で省かない


def test_main_pr_runs_when_only_older_commit_passed(mod, repo):
    """通ったのが機能の変更より前のコミットなら、その変更は試されていないので省かない。"""
    main, _, m = release_history(repo)
    assert not decide(mod, repo, main, m, api=fake_api({main}))[0]


def test_main_pr_skips_when_main_has_only_its_own_merge_commits(mod, repo):
    """main が develop のマージのコミットだけを持ち、それが develop に戻っていない形（このリポジトリの形）。"""
    main, p, m = release_history(repo)
    git(repo, "switch", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge develop", main)  # 中身を足さないマージ
    git(repo, "commit", "-q", "--allow-empty", "-m", "noop")
    tip = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-q", "develop")
    skip, reason = decide(mod, repo, tip, m, api=fake_api({p}))
    assert skip, reason


def test_main_pr_runs_when_base_has_commits_not_in_head(mod, repo):
    main, p, m = release_history(repo)
    git(repo, "switch", "-q", "main")
    write(repo, "hotfix.py", "x = 1\n")
    hot = commit(repo, "hotfix")
    assert not decide(mod, repo, hot, m, api=fake_api({m, p}))[0]


def test_failed_or_other_branch_run_does_not_count(mod):
    def api(path):
        return {"workflow_runs": [
            {"head_sha": "abc", "event": "push", "head_branch": "develop", "conclusion": "failure"},
            {"head_sha": "abc", "event": "pull_request", "head_branch": "develop", "conclusion": "success"},
            {"head_sha": "abc", "event": "push", "head_branch": "feat/x", "conclusion": "success"},
        ]}
    assert not mod.passed_on_branch("o/r", "pytest.yml", "abc", "develop", api)


def test_api_error_does_not_skip(mod):
    def api(path):
        raise RuntimeError("HTTP 403")
    assert not mod.passed_on_branch("o/r", "pytest.yml", "abc", "develop", api)


# --- 出力 ---------------------------------------------------------------------------

def test_cli_writes_github_output(repo, tmp_path):
    base = git(repo, "rev-parse", "HEAD")
    bump(repo)
    head = commit(repo)
    out = tmp_path / "out.txt"
    p = subprocess.run([sys.executable, str(SCRIPT), "--root", str(repo), "--event", "pull_request",
                        "--base", base, "--head", head, "--repo", "o/r", "--workflow", "pytest.yml"],
                       capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "GITHUB_OUTPUT": str(out)})
    assert p.returncode == 0, p.stderr
    assert "skip=true" in out.read_text()


# --- release-steps.py の待ちとマージ -------------------------------------------------

def test_merge_when_green_accepts_skipped_heavy_jobs(monkeypatch):
    """省いたジョブ（SKIPPED）と、ステップを省いて通ったジョブ（SUCCESS）は通ったものとして数える。

    配布の PR の待ち（release-steps.py の wait_and_merge）は merged-steps.py merge-when-green に任せる。
    """
    ms = load(RELEASE_STEPS.parent / "merged-steps.py", "merged_steps_for_ci_skip", monkeypatch)
    rollup = [{"__typename": "CheckRun", "name": n, "status": "COMPLETED", "conclusion": c}
              for n, c in [("pytest", "SUCCESS"), ("pytest (0/2)", "SKIPPED"),
                           ("runtime-smoke (claude)", "SUCCESS"), ("ci-scope", "SUCCESS")]]
    pending, failed, passed = ms.check_states(rollup)
    assert pending == [] and failed == []
    assert sorted(passed) == ["ci-scope", "pytest", "pytest (0/2)", "runtime-smoke (claude)"]
