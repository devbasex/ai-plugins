"""`scripts/resolve.sh`（解決の入口）が 4 ランタイム × 開発中 / 配布済みで当たることを確かめる。

入口を探すコマンドは `development-workflow/references/scripts-lookup.md` の「入口を探すコマンド」の
bash のコードブロックにある。テストはそのブロックを読み出し、入口の実物を写した配置の上で
実行する。配置は一時の HOME とディレクトリに作り、実ユーザの導入物を拾わない。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2]
ENTRY = PLUGIN / "scripts" / "resolve.sh"
LOOKUP = PLUGIN / "skills" / "development-workflow" / "references" / "scripts-lookup.md"
HEADING = "## 入口を探すコマンド"
TOKEN = "'${CLAUDE_PLUGIN_ROOT}'"
RUNTIMES = ("claude", "kiro", "codex", "agy")


def finder() -> str:
    text = LOOKUP.read_text(encoding="utf-8")
    head = text.index(HEADING)
    block = re.search(r"^```bash\n(.*?)^```$", text[head:], re.S | re.M)
    assert block is not None
    return block.group(1)


def base_env(home: Path, claude: bool) -> dict[str, str]:
    env = os.environ.copy()
    for key in ("CLAUDECODE", "CLAUDE_PLUGIN_ROOT"):
        env.pop(key, None)
    env.update(LC_ALL="C", HOME=str(home), GIT_CONFIG_NOSYSTEM="1")
    if claude:
        env["CLAUDECODE"] = "1"
    return env


def run_finder(cwd: Path, home: Path, *, substitute: Path | None = None,
               claude: bool = False) -> str:
    """入口を探すコマンドを実行し、決まった `$SCRIPTS` を返す（見つからなければ空）。"""
    snippet = finder()
    if substitute is not None:
        snippet = snippet.replace(TOKEN, f"'{substitute}'", 1)
    got = subprocess.run(
        ["bash", "-c", f'set -uo pipefail\n{snippet}\nprintf "%s\\n" "$SCRIPTS"\n'],
        cwd=str(cwd), env=base_env(home, claude), capture_output=True, text=True,
    )
    assert got.returncode == 0, got.stderr
    return got.stdout.strip()


def run_entry(entry: Path, cwd: Path, home: Path, *args: str,
              claude: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(entry), *args], cwd=str(cwd), env=base_env(home, claude),
        capture_output=True, text=True,
    )


def make_plugin(root: Path, *, entry: bool = True, skills=("fix", "worktree")) -> Path:
    """配布物を作る。`entry=False` は入口を持たない古い版である。"""
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "projects-sync.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    if entry:
        shutil.copy(ENTRY, root / "scripts" / "resolve.sh")
    for name in skills:
        (root / "skills" / name / "scripts").mkdir(parents=True, exist_ok=True)
        (root / "skills" / name / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return root


def install_claude(home: Path, version: str = "10.0.0") -> Path:
    root = make_plugin(home / ".claude" / "plugins" / "cache" / "ai-plugins" / "ndf" / version)
    record = home / ".claude" / "plugins" / "installed_plugins.json"
    record.write_text(json.dumps({"version": 2, "plugins": {"ndf@ai-plugins": [
        {"scope": "user", "installPath": str(root), "version": version}]}}), encoding="utf-8")
    return root


def link_kiro(project: Path, plugin: Path) -> None:
    skills = project / ".kiro" / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    for src in sorted((plugin / "skills").iterdir()):
        (skills / src.name).symlink_to(src, target_is_directory=True)


def install(runtime: str, home: Path, project: Path, tmp: Path) -> tuple[Path, dict]:
    """ランタイムの配布物を置き、期待するルートと実行の条件を返す。"""
    if runtime == "claude":
        root = install_claude(home)
        return root, {"substitute": root, "claude": True}
    if runtime == "kiro":
        root = make_plugin(tmp / "kiro-src" / "plugins" / "ndf")
        link_kiro(project, root)
        return root, {}
    if runtime == "codex":
        root = make_plugin(home / ".codex" / ".tmp" / "marketplaces" / "ai-plugins" / "plugins" / "ndf")
        return root, {}
    root = make_plugin(home / ".gemini" / "config" / "plugins" / "ndf")
    return root, {}


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """NDF を持たない利用者のリポジトリ。"""
    p = tmp_path / "project"
    p.mkdir()
    subprocess.run(["git", "init", "-q", str(p)], check=True)
    return p


@pytest.mark.parametrize("runtime", RUNTIMES)
def test_distributed(runtime, tmp_path, home, project) -> None:
    """配布済み: 利用者のリポジトリからは、そのランタイムの配布物を採る。"""
    root, kw = install(runtime, home, project, tmp_path)
    assert run_finder(project, home, **kw) == str(root / "scripts")


@pytest.mark.parametrize("runtime", RUNTIMES)
def test_development(runtime, tmp_path, home) -> None:
    """開発中: 配布物があっても、現在地のリポジトリの plugins/ndf を先に採る。"""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    dev = make_plugin(repo / "plugins" / "ndf")
    _, kw = install(runtime, home, repo, tmp_path)
    sub = repo / "docs"
    sub.mkdir()
    assert run_finder(sub, home, **kw) == str(dev / "scripts")


def test_issue_590_old_codex_copy_is_not_taken(home, project) -> None:
    """#590: 参照ファイルで置き換わらなくても、Claude Code では Codex の古い控えを採らない。"""
    make_plugin(home / ".codex" / ".tmp" / "marketplaces" / "ai-plugins" / "plugins" / "ndf",
                entry=False)
    root = install_claude(home)
    assert run_finder(project, home, claude=True) == str(root / "scripts")


def test_issue_590_codex_entry_defers_to_claude_record(home, project) -> None:
    """Codex の控えが入口を持っていても、Claude Code の中では導入の記録へ戻る。"""
    codex = home / ".codex" / ".tmp" / "marketplaces" / "ai-plugins" / "plugins" / "ndf"
    make_plugin(codex)
    root = install_claude(home)
    got = run_entry(codex / "scripts" / "resolve.sh", project, home, "scripts", claude=True)
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == str(root / "scripts")


def test_old_cache_version_defers_to_installed_record(home, project) -> None:
    """cache に残った古い版から入口が当たっても、導入の記録が指す版を採る。"""
    make_plugin(home / ".claude" / "plugins" / "cache" / "ai-plugins" / "ndf" / "1.0.0")
    root = install_claude(home, "9.0.0")
    assert run_finder(project, home, claude=True) == str(root / "scripts")


def test_plugin_dir_wins_over_installed(tmp_path, home, project) -> None:
    """`claude --plugin-dir` で読み込んだ実体は、導入済みの版より先に採る。"""
    install_claude(home)
    loaded = make_plugin(tmp_path / "wt" / "plugins" / "ndf")
    assert run_finder(project, home, substitute=loaded, claude=True) == str(loaded / "scripts")


def test_codex_outside_claude_code(home, project) -> None:
    """Claude Code の外では、Claude Code の導入の記録を見ない。"""
    install_claude(home)
    codex = make_plugin(home / ".codex" / ".tmp" / "marketplaces" / "ai-plugins" / "plugins" / "ndf")
    assert run_finder(project, home) == str(codex / "scripts")


def test_skill_subcommands(tmp_path, home, project) -> None:
    root = make_plugin(home / ".gemini" / "config" / "plugins" / "ndf")
    entry = root / "scripts" / "resolve.sh"
    got = run_entry(entry, project, home, "skill", "fix")
    assert got.stdout.strip() == str(root / "skills" / "fix")
    got = run_entry(entry, project, home, "scripts", "fix")
    assert got.stdout.strip() == str(root / "skills" / "fix" / "scripts")
    got = run_entry(entry, project, home, "root")
    assert got.stdout.strip() == str(root)


def test_skill_not_distributed_exits_3(home, project) -> None:
    """agy は配布の基準に無い Skill を持たない。理由を標準エラーへ書いて 3 で終わる。"""
    root = make_plugin(home / ".gemini" / "config" / "plugins" / "ndf", skills=("fix",))
    got = run_entry(root / "scripts" / "resolve.sh", project, home, "skill", "cross-review")
    assert got.returncode == 3
    assert "cross-review" in got.stderr


def test_nothing_found_exits_3(tmp_path, home, project) -> None:
    lone = tmp_path / "lone" / "scripts"
    lone.mkdir(parents=True)
    shutil.copy(ENTRY, lone / "resolve.sh")
    got = run_entry(lone / "resolve.sh", project, home, "scripts")
    assert got.returncode == 3
    assert got.stderr.strip() != ""
    assert run_finder(project, home) == ""


def test_bad_arguments_exit_2(home, project) -> None:
    assert run_entry(ENTRY, project, home, "skill").returncode == 2
    assert run_entry(ENTRY, project, home, "nope").returncode == 2
