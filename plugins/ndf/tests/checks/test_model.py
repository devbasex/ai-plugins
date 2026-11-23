"""検査の見る範囲（`Scope`）がリポジトリの設定とプラグインの外のファイルを読む経路と、設定のキー `toolchain`・`budget` の形（#3687）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from ndf import contracts, identity
from ndf.checks.model import CheckError, Scope, load_settings

SCHEMA = "schemas/ndf-settings/project.schema.json"

# 設計書の `.ndf/project.json` の例の 2 つのキー
TOOLCHAIN = {
    "workflows": ".github/workflows/*.yml",
    "python_ci": ["3.11", "3.13"],
    "python_compat": {"versions": ["3.10"], "workflows": [".github/workflows/script-structure.yml"]},
    "plugin_pyprojects": ["plugins/ndf/pyproject.toml"],
    "launchers": ["plugins/ndf/bin/ndf"],
}
BUDGET = {
    "tests": 4400,
    "ci_jobs": 40,
    "unused_deps": 3,
    "replaceable": 3,
    "dep_modules": {"markdown-it-py": "markdown_it", "python-dotenv": "dotenv", "ruamel-yaml": "ruamel"},
    "dep_exempt": {"ruff": "子プロセスとして起動する（ndf check の lint）"},
}


def _errors(settings: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(contracts.load_json(SCHEMA))
    return [e.message for e in validator.iter_errors({"version": 1, **settings})]


def _settings_file(repo: Path) -> Path:
    return repo / identity.FIXED_WORDS["ndf_settings_dir"] / "project.json"


def test_the_example_toolchain_and_budget_pass_the_schema() -> None:
    assert _errors({"toolchain": TOOLCHAIN, "budget": BUDGET}) == []


def test_every_key_of_toolchain_and_budget_is_optional() -> None:
    assert _errors({"toolchain": {}, "budget": {}}) == []


@pytest.mark.parametrize(
    "settings",
    [
        {"toolchain": {"unknown": 1}},
        {"budget": {"unknown": 1}},
        {"toolchain": {"python_compat": {"versions": ["3.10"], "unknown": []}}},
        {"budget": {"tests": -1}},
        {"budget": {"ci_jobs": 1.5}},
        {"toolchain": {"python_ci": [3.11]}},
        {"toolchain": {"python_ci": ["3"]}},
        {"toolchain": {"python_compat": {"versions": ["py310"]}}},
        {"toolchain": {"launchers": "plugins/ndf/bin/ndf"}},
        {"budget": {"dep_modules": {"ruamel-yaml": 1}}},
        {"budget": {"dep_exempt": ["ruff"]}},
        {"toolchain": {"workflows": "/etc/*.yml"}},
        {"toolchain": {"workflows": "../other/.github/workflows/*.yml"}},
        {"toolchain": {"launchers": ["plugins/../../bin/ndf"]}},
    ],
    ids=[
        "toolchain の未知のキー",
        "budget の未知のキー",
        "python_compat の未知のキー",
        "上限が負",
        "上限が整数でない",
        "Python のバージョンが数値",
        "Python のバージョンに小数部が無い",
        "互換のバージョンの形が違う",
        "パスの一覧が文字列",
        "import の名前が文字列でない",
        "除外が辞書でない",
        "ワークフローのグロブが絶対パス",
        "ワークフローのグロブがリポジトリの外へ出る",
        "パスの一覧に .. の区切り",
    ],
)
def test_malformed_toolchain_or_budget_is_rejected(settings: dict[str, Any]) -> None:
    assert _errors(settings) != []


def test_repo_files_finds_files_outside_the_plugin_in_sorted_order(tmp_path: Path) -> None:
    plugin = tmp_path / "plugins" / "p"
    plugin.mkdir(parents=True)
    for name in ("b.yml", "a.yml"):
        (tmp_path / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".github" / "workflows" / name).write_text("on: push\n", encoding="utf-8")
    (tmp_path / ".github" / "workflows" / "sub.yml").mkdir()
    scope = Scope(tmp_path, plugin, "p")
    assert scope.repo_files(".github/workflows/*.yml") == [
        tmp_path / ".github" / "workflows" / "a.yml",
        tmp_path / ".github" / "workflows" / "b.yml",
    ]


@pytest.mark.parametrize("glob", ["/etc/*", "../*", "a/../../*", ""], ids=["絶対パス", "親へ出る", "途中で親へ出る", "空"])
def test_repo_files_rejects_a_glob_that_is_not_repository_relative(tmp_path: Path, glob: str) -> None:
    with pytest.raises(CheckError, match="相対のグロブでない"):
        Scope(tmp_path, tmp_path, "p").repo_files(glob)


def test_repo_files_skips_a_symlink_that_resolves_outside_the_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "w").mkdir(parents=True)
    (tmp_path / "secret.yml").write_text("x\n", encoding="utf-8")
    (repo / "w" / "out.yml").symlink_to(tmp_path / "secret.yml")
    (repo / "w" / "in.yml").write_text("on: push\n", encoding="utf-8")
    assert Scope(repo, repo, "p").repo_files("w/*.yml") == [repo / "w" / "in.yml"]


def test_settings_default_to_an_empty_dict(tmp_path: Path) -> None:
    assert Scope(tmp_path, tmp_path, "p").settings == {}


def test_settings_are_read_only_when_a_rule_uses_them(tmp_path: Path) -> None:
    path = _settings_file(tmp_path)
    path.parent.mkdir()
    path.write_text("{", encoding="utf-8")
    scope = Scope(tmp_path, tmp_path, "p")
    with pytest.raises(CheckError, match="project.json"):
        scope.settings


def test_load_settings_reads_the_repository_settings(tmp_path: Path) -> None:
    path = _settings_file(tmp_path)
    path.parent.mkdir()
    path.write_text(json.dumps({"version": 1, "budget": BUDGET}), encoding="utf-8")
    assert load_settings(tmp_path)["budget"] == BUDGET


def test_load_settings_is_empty_without_the_settings_file(tmp_path: Path) -> None:
    assert load_settings(tmp_path) == {}


def test_load_settings_stops_on_a_settings_file_that_resolves_outside_the_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"version": 1}), encoding="utf-8")
    path = _settings_file(repo)
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)
    with pytest.raises(CheckError, match="リポジトリの外"):
        load_settings(repo)


@pytest.mark.parametrize("text", ["{", "[]"], ids=["JSON でない", "オブジェクトでない"])
def test_load_settings_stops_on_a_broken_settings_file(tmp_path: Path, text: str) -> None:
    path = _settings_file(tmp_path)
    path.parent.mkdir()
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CheckError, match="project.json"):
        load_settings(tmp_path)
