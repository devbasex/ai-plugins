"""`$SCRIPTS` の解決が 3 ランタイムの配置で当たることを検証する。

解決の手順は `references/projects-tracking.md` の bash のコードブロックにしかない。
テストはそのブロックを読み出して実行する。手順を写し取ると、写しだけが正しくて配布された
手順が外れている状態を作れてしまう。

外部への通信は行わない。配置は `tmp_path` の上に作る。
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REFERENCE = (
    Path(__file__).resolve().parents[1] / "references" / "projects-tracking.md"
)
HEADING = "### `$SCRIPTS` を決める"
# Claude Code が SKILL.md の中で置き換える語。テストでも同じ置き換えを行う。
PLUGIN_ROOT_TOKEN = "'${CLAUDE_PLUGIN_ROOT}'"


def lookup_snippet() -> str:
    """`$SCRIPTS` を決める節の bash のコードブロックを取り出す。"""
    text = REFERENCE.read_text(encoding="utf-8")
    head = text.index(HEADING)
    block = re.search(r"^```bash\n(.*?)^```$", text[head:], re.S | re.M)
    assert block is not None, f"{REFERENCE} の「{HEADING}」に bash のブロックが無い"
    return block.group(1)


def resolve(cwd: Path, home: Path, plugin_root: Path | None = None) -> str:
    """手順を実行し、決まった `$SCRIPTS` を返す。空文字は「見つからなかった」である。"""
    snippet = lookup_snippet()
    if plugin_root is not None:
        # Claude Code は置き換えた上で渡す。置き換え後もシングルクォートを保つ。
        snippet = snippet.replace(PLUGIN_ROOT_TOKEN, f"'{plugin_root}'", 1)
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["HOME"] = str(home)
    got = subprocess.run(
        ["bash", "-c", f'set -uo pipefail\n{snippet}\nprintf "%s\\n" "$SCRIPTS"\n'],
        cwd=str(cwd), env=env, capture_output=True, text=True,
    )
    assert got.returncode == 0, got.stderr
    return got.stdout.strip()


def make_plugin(root: Path) -> Path:
    """プラグインの配布物を作る。`scripts/` と `skills/<Skill名>/` を持つ。"""
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "projects-sync.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    for name in ("development-workflow", "worktree"):
        skill = root / "skills" / name
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return root


def link_kiro(project: Path, plugin: Path) -> Path:
    """Kiro CLI のインストーラと同じ配置を作る。

    `.kiro/skills` は実体のディレクトリで、その下の Skill ごとにプラグインの
    `skills/<Skill名>` への symlink が張られる。
    """
    skills = project / ".kiro" / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    for src in sorted((plugin / "skills").iterdir()):
        (skills / src.name).symlink_to(src, target_is_directory=True)
    return project


def make_codex(home: Path, marketplace: str = "ai-plugins") -> Path:
    """Codex がマーケットプレイスのスナップショットへ展開した配置を作る。"""
    root = home / ".codex" / ".tmp" / "marketplaces" / marketplace / "plugins" / "ndf"
    return make_plugin(root)


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    """手順が実ユーザの導入物を拾わないよう、HOME を空のディレクトリにする。"""
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture()
def elsewhere(tmp_path: Path) -> Path:
    """どの配置とも関係のない作業ディレクトリ。"""
    d = tmp_path / "elsewhere"
    d.mkdir()
    return d


def test_nothing_is_found_without_any_layout(elsewhere, home) -> None:
    """導入物が無ければ空で返る。見つからないことは失敗ではない。"""
    assert resolve(elsewhere, home) == ""


def test_claude_code_plugin_root(tmp_path, elsewhere, home) -> None:
    """Claude Code は `${CLAUDE_PLUGIN_ROOT}` が絶対パスへ置き換わる。"""
    plugin = make_plugin(tmp_path / "claude" / "cache" / "ai-plugins" / "ndf" / "9.4.0")
    assert resolve(elsewhere, home, plugin_root=plugin) == str(plugin / "scripts")


def test_kiro_workspace_symlink(tmp_path, home) -> None:
    """Kiro CLI: `.kiro/skills/<Skill名>` の symlink からプラグインへ戻る。"""
    plugin = make_plugin(tmp_path / "kiro-plugin" / "plugins" / "ndf")
    project = link_kiro(tmp_path / "project", plugin)
    assert resolve(project, home) == str(plugin / "scripts")


def test_kiro_global_symlink(tmp_path, elsewhere, home) -> None:
    """`--scope global` では `~/.kiro/skills/` に張られる。作業ディレクトリに依らない。"""
    plugin = make_plugin(tmp_path / "kiro-plugin" / "plugins" / "ndf")
    link_kiro(home, plugin)
    assert resolve(elsewhere, home) == str(plugin / "scripts")


def test_kiro_skills_parent_is_not_the_plugin(tmp_path, home) -> None:
    """`.kiro/skills/../scripts` はプラグインの外を指す。そこには配布物が無い。"""
    plugin = make_plugin(tmp_path / "kiro-plugin" / "plugins" / "ndf")
    project = link_kiro(tmp_path / "project", plugin)
    assert not (project / ".kiro" / "scripts").exists()
    assert resolve(project, home) == str(plugin / "scripts")


def test_codex_marketplace_snapshot(elsewhere, home) -> None:
    """Codex: マーケットプレイスのスナップショットの下から見つける。"""
    plugin = make_codex(home)
    assert resolve(elsewhere, home) == str(plugin / "scripts")


def test_codex_marketplace_name_is_not_fixed(elsewhere, home) -> None:
    """マーケットプレイス名は導入元で変わる。名前を決め打ちしない。"""
    plugin = make_codex(home, marketplace="another-marketplace")
    assert resolve(elsewhere, home) == str(plugin / "scripts")


def test_repository_checkout_is_the_last_resort(tmp_path, home) -> None:
    """リポジトリを直接 clone した場合は作業ディレクトリからの相対で当たる。"""
    checkout = tmp_path / "checkout"
    plugin = make_plugin(checkout / "plugins" / "ndf")
    assert resolve(checkout, home) == str(plugin / "scripts")


def test_kiro_wins_over_codex(tmp_path, home) -> None:
    """両方が導入されている機械では、いま開いているプロジェクトの側を採る。"""
    make_codex(home)
    plugin = make_plugin(tmp_path / "kiro-plugin" / "plugins" / "ndf")
    project = link_kiro(tmp_path / "project", plugin)
    assert resolve(project, home) == str(plugin / "scripts")
