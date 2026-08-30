"""案内を出さないパスの判定を検証する（受け入れ条件 6〜8）。

案内を出す例と出さない例を対にして確かめる。判定は主ディレクトリからの相対パスに
対して行い、末尾が `/` の項目は前方一致、それ以外は完全一致とその配下を許可する。
"""
from __future__ import annotations

import pytest

from conftest import run_lib

DEFAULTS = '"${WT_DEFAULT_ALLOW_PATHS[@]}"'


@pytest.mark.parametrize(
    "rel",
    [
        "issues/issue-146-worktree-first/01-spec-and-plan.md",
        "docs/ndf-plugin-reference.md",
        ".claude/settings.json",
        ".codex/config.toml",
        ".kiro/steering/ndf-policies.md",
        ".agents/skills/pr/SKILL.md",
        ".gemini/settings.json",
        ".serena/project.yml",
        ".gitignore",
    ],
)
def test_allowed_paths_are_silent(rel: str) -> None:
    got = run_lib(f'wt_is_allowed_path "{rel}" {DEFAULTS}; echo $?')
    assert got.stdout.strip() == "0", f"{rel}: {got.stderr}"


@pytest.mark.parametrize(
    "rel",
    [
        "plugins/ndf/skills/pr/SKILL.md",
        "plugins/ndf/scripts/worktree-guard.sh",
        "scripts/build-runtime-plugins.sh",
        "README.md",
        ".claude-plugin/marketplace.json",
    ],
)
def test_protected_paths_are_notified(rel: str) -> None:
    got = run_lib(f'wt_is_allowed_path "{rel}" {DEFAULTS}; echo $?')
    assert got.stdout.strip() == "1", f"{rel}: {got.stderr}"


def test_prefix_must_stop_at_a_separator() -> None:
    """`docs/` の許可が `docs-internal/` まで広がらない。"""
    got = run_lib('wt_is_allowed_path "docs-internal/x.md" "docs/"; echo $?')
    assert got.stdout.strip() == "1", got.stderr


def test_file_entry_matches_exactly() -> None:
    got = run_lib('wt_is_allowed_path ".gitignore" ".gitignore"; echo $?')
    assert got.stdout.strip() == "0", got.stderr
    got = run_lib('wt_is_allowed_path ".gitignore.bak" ".gitignore"; echo $?')
    assert got.stdout.strip() == "1", got.stderr


def test_empty_allow_list_notifies_everything() -> None:
    got = run_lib('wt_is_allowed_path "issues/a.md"; echo $?')
    assert got.stdout.strip() == "1", got.stderr


def test_directory_entry_matches_the_directory_itself() -> None:
    """`cp x docs/` の書き込み先は正規化で末尾のスラッシュが落ちて `docs` になる。"""
    got = run_lib('wt_is_allowed_path "docs" "docs/"; echo $?')
    assert got.stdout.strip() == "0", got.stderr


def test_directory_entry_still_stops_at_a_separator() -> None:
    got = run_lib('wt_is_allowed_path "docs-internal" "docs/"; echo $?')
    assert got.stdout.strip() == "1", got.stderr
