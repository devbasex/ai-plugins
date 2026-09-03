"""定義ファイルの `description` の版数検査が、接尾辞の付いた版を読めることを固定する。

開発版の版数には接尾辞（`-dev.<連番>` / `-rc.<連番>`）が付く（`AGENTS.md` の
「版の付け方と開発版の配布」）。数字 3 つの直後に閉じ括弧を求める書式では
`(v9.7.0-dev.1)` に一致せず、開発版を出すたびにこの検査で止まっていた（#254）。

あわせて、agy の定義ファイル（`plugins/ndf/dev.agy/plugin.json`）の版数が
Claude 版と突き合わされることを固定する（#215 の受け入れ条件 A11）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

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
        "plugins/ndf/dev.agy/plugin.json",
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


# --- agy の定義ファイルの版数（#215 の受け入れ条件 A11） ---
#
# `plugins/ndf/dev.agy/plugin.json` は `version` と `description` の 2 箇所に版数を持つ。
# どちらも Claude 版の `plugin.json` と突き合わせる。**片方だけ直した状態で配れないこと**を、
# 古い値と記載の欠落の両方で確かめる。

AGY_MANIFEST = "plugins/ndf/dev.agy/plugin.json"


def rewrite_agy_manifest(root: Path, mutate: Callable[[dict[str, Any]], None]) -> None:
    """agy の定義ファイルだけを崩す。他のランタイムの定義には触らない。"""
    path = root / AGY_MANIFEST
    manifest = json.loads(path.read_text(encoding="utf-8"))
    mutate(manifest)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def test_agy_consistent_manifest_passes(tmp_path: Path) -> None:
    """崩していない木では、agy の定義ファイルも含めて通る。"""
    root = build_tree(tmp_path)
    assert (root / AGY_MANIFEST).is_file()
    result = run_check(root, tmp_path)
    assert result.returncode == 0, output_of(result)


def test_agy_stale_version_fails(tmp_path: Path) -> None:
    """`version` が前の版のままなら落ちる。"""
    root = build_tree(tmp_path)
    rewrite_agy_manifest(root, lambda m: m.update(version="9.2.1"))

    result = run_check(root, tmp_path)

    assert result.returncode == 1
    out = output_of(result)
    assert f"{AGY_MANIFEST} の version が claude 版と食い違う" in out, out
    assert "9.2.1" in out and helpers.VERSION in out, out


def test_agy_missing_version_fails(tmp_path: Path) -> None:
    """`version` の記載を消しても通らない。読み取れないこと自体を失敗として扱う。"""
    root = build_tree(tmp_path)
    rewrite_agy_manifest(root, lambda m: m.pop("version"))

    result = run_check(root, tmp_path)

    assert result.returncode == 1
    assert f"{AGY_MANIFEST} の version が claude 版と食い違う" in output_of(result)


def test_agy_stale_version_in_description_fails(tmp_path: Path) -> None:
    """`description` の版数だけが前の版でも落ちる。"""
    root = build_tree(tmp_path)
    rewrite_agy_manifest(
        root,
        lambda m: m.update(
            description=m["description"].replace(f"(v{helpers.VERSION})", "(v9.2.1)")
        ),
    )

    result = run_check(root, tmp_path)

    assert result.returncode == 1
    out = output_of(result)
    assert f"{AGY_MANIFEST} の description の版数が古い" in out, out


def test_agy_missing_version_in_description_fails(tmp_path: Path) -> None:
    """`description` から版数を消しても通らない。"""
    root = build_tree(tmp_path)
    rewrite_agy_manifest(
        root,
        lambda m: m.update(description=m["description"].replace(f"(v{helpers.VERSION})", "")),
    )

    result = run_check(root, tmp_path)

    assert result.returncode == 1
    assert (
        f"{AGY_MANIFEST} の description に `(vX.Y.Z)` 形式の版数がない" in output_of(result)
    )


def test_agy_skill_count_is_read_from_its_own_manifest(tmp_path: Path) -> None:
    """Skill 数の突き合わせ先は `agy-skills.txt` である。他のランタイムの数と取り違えない。"""
    root = build_tree(tmp_path)
    rewrite_agy_manifest(
        root,
        lambda m: m.update(
            description=m["description"].replace("2 focused skills", "3 focused skills")
        ),
    )

    result = run_check(root, tmp_path)

    assert result.returncode == 1
    assert (
        f"{AGY_MANIFEST} の description の Skill 数が食い違う"
        "（description: 3 / agy-skills.txt: 2）" in output_of(result)
    )
