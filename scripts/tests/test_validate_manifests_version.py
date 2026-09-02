"""定義ファイルの `description` の版数検査が、接尾辞の付いた版を読めることを固定する。

開発版の版数には接尾辞（`-dev.<連番>` / `-rc.<連番>`）が付く（`AGENTS.md` の
「版の付け方と開発版の配布」）。数字 3 つの直後に閉じ括弧を求める書式では
`(v9.7.0-dev.1)` に一致せず、開発版を出すたびにこの検査で止まっていた（#254）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import validate_manifests_helpers as helpers
from validate_manifests_helpers import build_tree, output_of, run_check

PRERELEASE = ["9.7.0-dev.1", "9.7.0-rc.2"]


def test_release_version_passes(tmp_path: Path) -> None:
    """接尾辞の無い版はこれまでどおり通る。"""
    result = run_check(build_tree(tmp_path, "9.6.0"), tmp_path)
    assert result.returncode == 0, output_of(result)


@pytest.mark.parametrize("version", PRERELEASE)
def test_prerelease_version_passes(tmp_path: Path, version: str) -> None:
    """接尾辞の付いた版は、`description` にも同じ形で書けば通る。"""
    result = run_check(build_tree(tmp_path, version), tmp_path)
    assert result.returncode == 0, output_of(result)


def test_prerelease_version_dropped_in_description_fails(tmp_path: Path) -> None:
    """接尾辞を落とした `description` は、`version` と食い違うため落ちる。

    書式を緩めるだけでは、`(v9.7.0)` に一致した時点で通ってしまう書き方もあり得る。
    拾えるようになったうえで、拾った値が `plugin.json` と一致することまで見ていることを
    確かめる。
    """
    root = build_tree(tmp_path, "9.7.0-dev.1", described="9.7.0")
    result = run_check(root, tmp_path)

    assert result.returncode == 1
    output = output_of(result)
    for label in (
        "plugins/ndf/.claude-plugin/plugin.json",
        "plugins/ndf/.codex-plugin/plugin.json",
        ".claude-plugin/marketplace.json の ndf",
    ):
        assert f"{label} の description の版数が古い" in output, output
    assert "形式の版数がない" not in output, output


def test_missing_version_in_description_still_fails(tmp_path: Path) -> None:
    """版数を書かない `description` は、これまでどおり落ちる。

    書式を緩めた結果、版数の記載そのものを省けるようになっていないことを確かめる。
    """
    root = build_tree(tmp_path)
    path = root / "plugins/ndf/.claude-plugin/plugin.json"
    path.write_text(
        path.read_text(encoding="utf-8").replace(f"(v{helpers.VERSION})", ""),
        encoding="utf-8",
    )

    result = run_check(root, tmp_path)

    assert result.returncode == 1
    assert (
        "plugins/ndf/.claude-plugin/plugin.json の description に `(vX.Y.Z)` 形式の版数がない"
        in output_of(result)
    )


@pytest.mark.parametrize("version", ["9.6.0", *PRERELEASE])
def test_skill_count_is_read_beside_the_version(tmp_path: Path, version: str) -> None:
    """版数の隣にある Skill 数を、版数の数字と取り違えない。

    接尾辞は `9.7.0-dev.1` のように数字で終わる。Skill 数を拾う書式がその数字を
    掴むと、版数を変えただけで Skill 数の突き合わせが崩れる。
    """
    root = build_tree(tmp_path, version)
    path = root / "plugins/ndf/.claude-plugin/plugin.json"
    path.write_text(
        path.read_text(encoding="utf-8").replace("5 focused skills", "4 focused skills"),
        encoding="utf-8",
    )

    result = run_check(root, tmp_path)

    assert result.returncode == 1
    assert (
        "plugins/ndf/.claude-plugin/plugin.json の description の Skill 数が食い違う"
        "（description: 4 / claude-skills.txt: 5）" in output_of(result)
    )
