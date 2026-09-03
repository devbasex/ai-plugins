"""配布先ランタイム agy の配置を固定する（#215 の受け入れ条件 A8 / A12）。

agy はプラグインのディレクトリ直下の `plugin.json` で対象を判別する。**その位置へ置くと
Codex の配布 Skill が `.codex-plugin/plugin.json` の `skills` 配列ではなく `skills/` の実体に
変わる**（設計の決定 1 の実測）。増える 2 個は Claude Code だけで動くものであるため、
ルート直下へ置かないこと自体を固定する。

配る Skill の基準は `manifests/agy-skills.txt` だけが持ち、`dev.agy/skills/` の symlink は
`scripts/build-runtime-plugins.sh` が生成する。基準と生成物が食い違ったまま配られないよう、
`--check` が不足・余分・向き先の違いを検出することを確かめる。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from agy_build_helpers import (
    AGY_SKILLS,
    FAMILY,
    build_tree,
    links_dir,
    output_of,
    run_build,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / f"plugins/{FAMILY}"
MANIFEST_DIR = PLUGIN_DIR / "manifests"

# 配布数は課題ごとに変わりうるが、**agy を足したことで他の 3 つが変わらない**ことは
# #215 の受け入れ条件（A12）である。値を書いて固定する。
EXPECTED_COUNTS = {"claude": 33, "codex": 31, "kiro": 32, "agy": 31}


def manifest_names(runtime: str) -> list[str]:
    path = MANIFEST_DIR / f"{runtime}-skills.txt"
    assert path.is_file(), f"{path} が無い"
    return [
        line.split("#", 1)[0].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    ]


def test_root_plugin_json_is_absent() -> None:
    """ルート直下の `plugin.json` を置かない。

    置くと Codex がそちらを優先して読み、`skills/` の実体を全件配る。agy 向けの定義は
    `dev.agy/plugin.json` にある（Agent Plugins 1.0.0 §8.2 のクライアント拡張ディレクトリ）。
    """
    assert not (PLUGIN_DIR / "plugin.json").exists(), (
        f"plugins/{FAMILY}/plugin.json があると Codex の配布 Skill が manifest ではなく"
        " skills/ の実体になる（agy 向けの定義は dev.agy/plugin.json へ置く）"
    )


@pytest.mark.parametrize("runtime,expected", sorted(EXPECTED_COUNTS.items()))
def test_manifest_counts_do_not_change(runtime: str, expected: int) -> None:
    """4 ランタイムの配布数。agy を足しても既存の 3 つは動かない。"""
    assert len(manifest_names(runtime)) == expected


def test_agy_manifest_starts_from_the_codex_set() -> None:
    """agy へ配る集合は Codex と同じ。

    Claude Code だけで動く 2 個（`official-skills-autoloader` / `statusline`）を外した
    集合である（設計の決定 3）。
    """
    assert manifest_names("agy") == manifest_names("codex")


def test_dev_agy_links_follow_the_manifest() -> None:
    """`dev.agy/skills/` は基準のとおりに並び、実体の Skill を指す。"""
    links = PLUGIN_DIR / "dev.agy/skills"
    assert sorted(p.name for p in links.iterdir()) == sorted(manifest_names("agy"))
    for name in manifest_names("agy"):
        link = links / name
        assert link.is_symlink(), f"{link} が symlink でない"
        assert os.readlink(link) == f"../../skills/{name}"
        assert (link / "SKILL.md").is_file()


@pytest.mark.parametrize("name,target", [("agents", "../agents"), ("scripts", "../scripts")])
def test_dev_agy_shares_the_implementation(name: str, target: str) -> None:
    """エージェント定義と hook の実体は 4 ランタイムで共有する（設計の決定 6）。"""
    link = PLUGIN_DIR / "dev.agy" / name
    assert link.is_symlink(), f"{link} が symlink でない"
    assert os.readlink(link) == target


def test_build_creates_the_links(tmp_path: Path) -> None:
    """基準から生成できる。生成した直後は `--check` が通る。"""
    root = build_tree(tmp_path)
    result = run_build(root)
    assert result.returncode == 0, output_of(result)
    assert sorted(p.name for p in links_dir(root).iterdir()) == sorted(AGY_SKILLS)
    assert run_build(root, check=True).returncode == 0


def test_check_detects_a_missing_link(tmp_path: Path) -> None:
    """symlink を 1 本消すと `--check` が落ちる。"""
    root = build_tree(tmp_path)
    run_build(root)
    (links_dir(root) / AGY_SKILLS[0]).unlink()

    result = run_build(root, check=True)
    assert result.returncode != 0
    assert AGY_SKILLS[0] in output_of(result)


def test_check_detects_an_extra_link(tmp_path: Path) -> None:
    """基準に無い名前を足すと `--check` が落ちる。"""
    root = build_tree(tmp_path)
    run_build(root)
    (links_dir(root) / "delta").symlink_to("../../skills/delta")

    result = run_build(root, check=True)
    assert result.returncode != 0
    assert "delta" in output_of(result)


def test_check_detects_a_wrong_target(tmp_path: Path) -> None:
    """向き先が違う symlink も落ちる。実体を指していなければ配られない。"""
    root = build_tree(tmp_path)
    run_build(root)
    link = links_dir(root) / AGY_SKILLS[0]
    link.unlink()
    link.symlink_to(f"../../skills/{AGY_SKILLS[1]}")

    result = run_build(root, check=True)
    assert result.returncode != 0
    assert AGY_SKILLS[0] in output_of(result)


def test_build_removes_a_stale_link(tmp_path: Path) -> None:
    """基準から外した Skill の symlink は、生成し直すと消える。"""
    root = build_tree(tmp_path)
    run_build(root)
    (links_dir(root) / "delta").symlink_to("../../skills/delta")

    assert run_build(root).returncode == 0
    assert not (links_dir(root) / "delta").is_symlink()


def test_build_skips_a_family_without_the_manifest(tmp_path: Path) -> None:
    """`agy-skills.txt` を持たない family では何も作らない。

    agy へ配らないプラグイン（`playwright-kit`）を巻き込まないことを固定する。
    """
    root = build_tree(tmp_path)
    (root / f"plugins/{FAMILY}/manifests/agy-skills.txt").unlink()

    assert run_build(root).returncode == 0
    assert not links_dir(root).exists()


def test_generated_links_are_not_collected_twice() -> None:
    """`dev.agy/skills/` の symlink を、テストの収集がたどらない。

    symlink の先は `skills/` の実体であり、そこには Skill ごとのテストがある。両方を
    たどると同じテストが 2 度数えられる（agy を配布先へ加えた時点で 1569 件が 2974 件へ
    増えた）。件数は退行の判定に使うため、二重に数えられる状態を残さない。
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            f"plugins/{FAMILY}",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "dev.agy" not in result.stdout, [
        line for line in result.stdout.splitlines() if "dev.agy" in line
    ]
