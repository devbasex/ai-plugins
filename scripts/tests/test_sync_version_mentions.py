"""`scripts/sync-version-mentions.py`: 版上げの後の版数の記載をそろえる。

11.0.1 の版上げの PR は、`plugin.json` の `version` だけが上がり、description と説明文書の版数が古いまま残って
`runtime-plugin-validate` と `pytest` が落ちた。実物の木を一時ディレクトリへ写し、版上げと同じ形（Claude 版・Codex 版の
`plugin.json` の `version` だけを上げる）にしてから打つ。実物は書き換えない。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "sync-version-mentions.py"


def _load():
    spec = importlib.util.spec_from_file_location("sync_version_mentions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _export(tmp_path: Path) -> Path:
    tree = tmp_path / "repo"
    tree.mkdir()
    archive = subprocess.run(["git", "-C", str(REPO_ROOT), "archive", "HEAD"], check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(tree)], input=archive, check=True)
    return tree


def _bump(tree: Path, plugin: str) -> str:
    """版上げの PR の組み立てと同じく、`version` の値だけを上げる（ほかの行は触らない）。"""
    claude = tree / "plugins" / plugin / ".claude-plugin" / "plugin.json"
    old = json.loads(claude.read_text(encoding="utf-8"))["version"]
    major, minor, patch = (int(p) for p in old.split("-")[0].split("."))
    new = f"{major}.{minor}.{patch + 1}"
    for path in (claude, claude.parent.parent / ".codex-plugin" / "plugin.json"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace(f'"version": "{old}"', f'"version": "{new}"', 1), encoding="utf-8")
    return new


def _run(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(tree)], capture_output=True, text=True, check=False)


def test_bumped_tree_becomes_consistent(tmp_path: Path) -> None:
    tree = _export(tmp_path)
    new = _bump(tree, "ndf")
    before = subprocess.run(
        [sys.executable, str(tree / "scripts" / "check-doc-staleness.py"), "--root", str(tree)], capture_output=True, text=True
    )
    assert before.returncode != 0  # 版上げの直後は検査が落ちる（直す対象がある）
    got = _run(tree)
    assert got.returncode == 0, got.stderr
    description = json.loads((tree / "plugins/ndf/.claude-plugin/plugin.json").read_text(encoding="utf-8"))["description"]
    assert f"(v{new})" in description
    assert "そろえた: README.md" in got.stdout


def test_root_plugin_manifest_follows_the_claude_version(tmp_path: Path) -> None:
    # 2026-11-22 の 11.0.14 の組み立て: 依存を更新した playwright-kit の Claude 版だけが 4.0.5 へ上がり、
    # ルートの plugin.json（Agent Plugins package）が 4.0.4 のまま残って validate_manifests が落ちた
    tree = _export(tmp_path)
    new = _bump(tree, "playwright-kit")
    got = _run(tree)
    assert got.returncode == 0, got.stderr
    root = json.loads((tree / "plugins/playwright-kit/plugin.json").read_text(encoding="utf-8"))
    assert root["version"] == new
    assert f"(v{new})" in root["description"]
    assert "そろえた: plugins/playwright-kit/plugin.json" in got.stdout


def test_consistent_tree_is_left_alone(tmp_path: Path) -> None:
    tree = _export(tmp_path)
    got = _run(tree)
    assert got.returncode == 0, got.stderr
    assert got.stdout == ""


@pytest.mark.parametrize(
    ("line", "old", "new", "want"),
    [
        ("| **ndf** | 11.0.0 | 説明 |\n", "11.0.0", "11.0.1", "| **ndf** | 11.0.1 | 説明 |\n"),
        ("`11.0.0` の次を開発するなら `11.1.0-dev.1`\n", "11.0.0", "11.0.1", "`11.0.1` の次を開発するなら `11.1.0-dev.1`\n"),
        ("v111.0.0 と 11.0.0\n", "11.0.0", "11.0.1", "v111.0.0 と 11.0.1\n"),  # 版数の一部には当てない
    ],
)
def test_replace_on_line_changes_only_the_reported_version(tmp_path: Path, line: str, old: str, new: str, want: str) -> None:
    path = tmp_path / "doc.md"
    path.write_text("先頭\n" + line, encoding="utf-8")
    assert _load().replace_on_line(path, 2, old, new)
    assert path.read_text(encoding="utf-8") == "先頭\n" + want


def test_stale_lines_reads_the_checker_output() -> None:
    output = (
        "ERROR: README.md: 概要の版数が食い違う（記載: 11.0.0（L72） / plugins/ndf/.claude-plugin/plugin.json: 11.0.1）\n"
        "ERROR: README.md: Skill の数が食い違う（記載: 44 / 実体: 45）\n"
    )
    assert _load().stale_lines(output) == [("README.md", 72, "11.0.0", "11.0.1")]
