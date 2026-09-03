"""agy の定義ファイルを検査の対象へ入れる（#215 の受け入れ条件 A9）。

版数を持つ箇所は 13 から 15 へ増える（`dev.agy/plugin.json` の `version` と `description`）。
agy には取得元の登録が無く、`agy plugin list` も版数を出さないため、利用者が版を判断できる
手がかりは clone した中身の版数だけである。古いまま残らないよう、Claude 版 `plugin.json` を
基準に突き合わせる。

**記載を消して検査を通せない形にする。** Skill 数を読み取れないこと自体も失敗として扱う。
"""
from __future__ import annotations

import json
from pathlib import Path

from validate_manifests_helpers import (
    MANIFESTS,
    VERSION,
    build_tree,
    description,
    output_of,
    run_check,
)

AGY_PLUGIN_JSON = "plugins/ndf/dev.agy/plugin.json"


def agy_manifest(root: Path) -> Path:
    return root / AGY_PLUGIN_JSON


def test_matching_agy_manifest_passes(tmp_path: Path) -> None:
    """版数と Skill 数が揃っていれば通る。"""
    result = run_check(build_tree(tmp_path), tmp_path)
    assert result.returncode == 0, output_of(result)


def test_old_version_fails(tmp_path: Path) -> None:
    """`version` が Claude 版と食い違うと落ちる。"""
    root = build_tree(tmp_path)
    path = agy_manifest(root)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["version"] = "9.2.0"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = run_check(root, tmp_path)
    assert result.returncode == 1
    assert f"{AGY_PLUGIN_JSON} の version が claude 版と食い違う" in output_of(result)


def test_old_version_in_description_fails(tmp_path: Path) -> None:
    """`description` の版数だけが古い場合も落ちる。"""
    root = build_tree(tmp_path)
    path = agy_manifest(root)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["description"] = description("9.2.0", len(MANIFESTS["agy"]))
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = run_check(root, tmp_path)
    assert result.returncode == 1
    assert f"{AGY_PLUGIN_JSON} の description の版数が古い" in output_of(result)


def test_wrong_skill_count_fails(tmp_path: Path) -> None:
    """`description` の Skill 数は `agy-skills.txt` の行数と突き合わせる。"""
    root = build_tree(tmp_path)
    path = agy_manifest(root)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["description"] = description(VERSION, len(MANIFESTS["agy"]) + 1)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = run_check(root, tmp_path)
    assert result.returncode == 1
    output = output_of(result)
    assert f"{AGY_PLUGIN_JSON} の description の Skill 数が食い違う" in output
    assert "agy-skills.txt" in output


def test_removed_skill_count_fails(tmp_path: Path) -> None:
    """Skill 数の記載を消しても検査は通らない。"""
    root = build_tree(tmp_path)
    path = agy_manifest(root)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["description"] = f"Fixture plugin (v{VERSION}): nothing countable here."
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = run_check(root, tmp_path)
    assert result.returncode == 1
    assert f"{AGY_PLUGIN_JSON} の description から Skill 数を読み取れない" in output_of(result)


def test_family_without_dev_agy_is_skipped(tmp_path: Path) -> None:
    """`dev.agy/` を持たない family は素通りする。agy へ配らないプラグインを巻き込まない。"""
    root = build_tree(tmp_path)
    agy_manifest(root).unlink()

    result = run_check(root, tmp_path)
    assert result.returncode == 0, output_of(result)
