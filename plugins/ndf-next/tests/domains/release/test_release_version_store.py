"""バージョンの保存先のアダプター（#3615 の決定 2・3・I2）。"""

from __future__ import annotations

import json
from pathlib import Path

import git_sandbox
import pytest

from ndf import contracts
from ndf.adapters import git as git_mod
from ndf.core.branch_strategy import Strategy
from ndf.domains.release import names_decide
from ndf.domains.release import plugins as plugins_mod
from ndf.domains.release import version_store as store_mod

from ._fakes import configure, section


def _release(kind: str, **store: str) -> Strategy:
    got = section("web-develop")
    got["release"].update(version_store={"kind": kind, **store}, tag="v{version}", github_release=False, cd={"workflow": "x.yml"})
    return Strategy(got)


def _repo(tmp_path: Path, files: dict[str, str]) -> git_mod.Git:
    git_sandbox.sh(tmp_path, "init", "-q", "-b", "main")
    configure(tmp_path)
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    git_sandbox.sh(tmp_path, "add", "-A")
    git_sandbox.sh(tmp_path, "commit", "-q", "-m", "init")
    return git_mod.Git(tmp_path)


def test_kinds_match_the_schema() -> None:
    """保存先の種類の表と、設定のスキーマの `version_store` の `oneOf` の種類が一致する。"""
    doc = contracts.load_json("schemas/ndf-settings/project.schema.json")
    rows = doc["$defs"]["BranchStrategy"]["properties"]["release"]["properties"]["version_store"]["oneOf"]
    assert {r["properties"]["kind"]["const"] for r in rows} == set(store_mod.KINDS)


@pytest.mark.parametrize(
    ("kind", "store", "cls"),
    [
        ("json", {"path": "package.json", "pointer": "/version"}, store_mod.JsonStore),
        ("pubspec", {"path": "pubspec.yaml"}, store_mod.PubspecStore),
        ("tag", {}, store_mod.TagStore),
        ("none", {}, store_mod.NoneStore),
        ("plugin", {}, store_mod.PluginStore),
    ],
)
def test_load_picks_the_adapter_of_the_kind(tmp_path: Path, kind: str, store: dict, cls: type) -> None:
    got = store_mod.load(tmp_path, _release(kind, **store), "o/app")
    assert isinstance(got, cls) and got.kind == kind


def test_load_refuses_an_unknown_kind(tmp_path: Path) -> None:
    with pytest.raises(store_mod.StoreError, match="取れる種類"):
        store_mod.load(tmp_path, _release("gradle"), "o/app")


def test_repository_unit_is_named_by_the_setting_or_the_repository(tmp_path: Path) -> None:
    assert store_mod.load(tmp_path, _release("none"), "o/app").units() == [store_mod.Unit("app", "")]
    named = _release("none")
    named.section["release"]["plugin"] = "web"
    assert store_mod.load(tmp_path, named, "o/app").units() == [store_mod.Unit("web", "")]


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("name: a\nversion: 1.2.3+45\n", "1.2.3"),
        ("name: a\nversion: 1.2.3\n", "1.2.3"),
        ('version: "1.2.3+1" # c\n', "1.2.3"),
        ("name: a\nenv:\n  version: 1.0.0\n", None),
        ("name: a\n", None),
    ],
)
def test_pubspec_version(text: str, want: str | None) -> None:
    assert store_mod.pubspec_version(text) == want


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("version: 1.2.3+45\n", "version: 1.2.4+46\n"),
        ("version: 1.2.3\n", "version: 1.2.4\n"),
        ("version: 1.2.3+abc\n", "version: 1.2.4+abc\n"),
        (
            "# c\nname: a  # x\nversion: 1.2.3+1 # note\nenv:\n  version: 9\n",
            "# c\nname: a  # x\nversion: 1.2.4+2 # note\nenv:\n  version: 9\n",
        ),
    ],
)
def test_pubspec_replace_changes_only_the_version_line(before: str, after: str) -> None:
    """決定 3: version: の 1 行だけを書き換え、数のビルド番号は 1 つ上げる。ほかの行は 1 文字も変えない。"""
    assert store_mod.pubspec_replace(before, "1.2.4") == after


def test_pubspec_replace_refuses_two_lines() -> None:
    with pytest.raises(store_mod.StoreError, match="2 行"):
        store_mod.pubspec_replace("version: 1.0.0\nversion: 1.0.1\n", "1.2.4")


def test_json_store_reads_and_writes(tmp_path: Path) -> None:
    repo_git = _repo(tmp_path, {"package.json": json.dumps({"name": "app", "version": "1.2.2"}) + "\n"})
    store = store_mod.load(tmp_path, _release("json", path="package.json", pointer="/version"), "o/app")
    unit = store.units()[0]
    assert store.read(repo_git, "HEAD", unit) == "1.2.2" and store.paths() == frozenset({"package.json"})
    assert store.write(unit, "1.2.3") == ["package.json"]
    assert json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))["version"] == "1.2.3"


def test_json_store_refuses_a_non_string_value(tmp_path: Path) -> None:
    repo_git = _repo(tmp_path, {"package.json": json.dumps({"version": 3}) + "\n"})
    store = store_mod.load(tmp_path, _release("json", path="package.json", pointer="/version"), "o/app")
    with pytest.raises(store_mod.StoreError, match="/version"):
        store.read(repo_git, "HEAD", store.units()[0])


def test_json_store_reads_none_without_the_file(tmp_path: Path) -> None:
    repo_git = _repo(tmp_path, {"README.md": "x\n"})
    store = store_mod.load(tmp_path, _release("json", path="package.json", pointer="/version"), "o/app")
    assert store.read(repo_git, "HEAD", store.units()[0]) is None


def test_pubspec_store_reads_and_writes(tmp_path: Path) -> None:
    repo_git = _repo(tmp_path, {"pubspec.yaml": "name: app\nversion: 1.2.2+7\n"})
    store = store_mod.load(tmp_path, _release("pubspec", path="pubspec.yaml"), "o/app")
    unit = store.units()[0]
    assert store.read(repo_git, "HEAD", unit) == "1.2.2"
    assert store.write(unit, "1.2.3") == ["pubspec.yaml"]
    assert (tmp_path / "pubspec.yaml").read_text(encoding="utf-8") == "name: app\nversion: 1.2.3+8\n"


def test_tag_store_reads_reachable_tags_and_writes_nothing(tmp_path: Path) -> None:
    repo_git = _repo(tmp_path, {"a.txt": "a\n"})
    git_sandbox.sh(tmp_path, "tag", "v1.2.2")
    git_sandbox.sh(tmp_path, "tag", "v2.0.0-dev.1")
    git_sandbox.sh(tmp_path, "switch", "-q", "-c", "other")
    git_sandbox.sh(tmp_path, "commit", "-q", "--allow-empty", "-m", "other")
    git_sandbox.sh(tmp_path, "tag", "v9.9.9")
    store = store_mod.load(tmp_path, _release("tag"), "o/app")
    unit = store.units()[0]
    assert store.read(repo_git, "main", unit) == "1.2.2"
    assert sorted(store_mod.tags_at(repo_git, "main")) == ["v1.2.2", "v2.0.0-dev.1"]
    assert store.write(unit, "1.2.3") == [] and store.paths() == frozenset()


def test_tag_store_without_tags_reads_none(tmp_path: Path) -> None:
    repo_git = _repo(tmp_path, {"a.txt": "a\n"})
    store = store_mod.TagStore("app", names_decide.NameFormats("v{version}"))
    assert store.read(repo_git, "HEAD", store.units()[0]) is None
    with pytest.raises(git_mod.GitError):
        store_mod.tags_at(repo_git, "no-such-ref")


def test_none_store_has_no_version(tmp_path: Path) -> None:
    repo_git = _repo(tmp_path, {"a.txt": "a\n"})
    store = store_mod.load(tmp_path, _release("none"), "o/app")
    unit = store.units()[0]
    assert store.read(repo_git, "HEAD", unit) is None and store.write(unit, "1.0.0") == [] and store.paths() == frozenset()


def test_plugin_store_reads_from_the_ref_and_writes_the_manifests(tmp_path: Path) -> None:
    files = {
        ".claude-plugin/marketplace.json": json.dumps(
            {"plugins": [{"name": "kit", "source": "./tools/kit", "description": "Kit (v1.2.3)"}]}
        ),
        "tools/kit/.claude-plugin/plugin.json": json.dumps({"name": "kit", "version": "1.2.3"}),
    }
    repo_git = _repo(tmp_path, files)
    store = store_mod.load(tmp_path, Strategy(section("plugin")), "o/r")
    unit = store.units()[0]
    assert unit == store_mod.Unit("kit", "tools/kit") and store.read(repo_git, "HEAD", unit) == "1.2.3"
    assert store.paths() == frozenset({".claude-plugin/marketplace.json", "tools/kit/.claude-plugin/plugin.json"})
    assert ".claude-plugin/marketplace.json" in store.write(unit, "1.2.4")
    assert plugins_mod.version(tmp_path, plugins_mod.Plugin("kit", "tools/kit")) == "1.2.4"
    (tmp_path / "tools/kit/.claude-plugin/plugin.json").write_text("{", encoding="utf-8")
    git_sandbox.sh(tmp_path, "commit", "-q", "-am", "broken json")
    assert store.read(repo_git, "HEAD~1", unit) == "1.2.3"
    with pytest.raises(plugins_mod.PluginsError):
        store.read(repo_git, "HEAD", unit)


def test_json_store_write_also_bumps_the_declared_files(tmp_path: Path) -> None:
    """現状固定（I-004）: json の保存先の `files` の行は、pointer の無い行は単位の名前を含む行の今のバージョンを、
    ある行は JSON のその位置（値が今のバージョンのときだけ）を書き換える。無いファイル・書き換えの無いファイルは返さない。"""
    files = {
        "package.json": json.dumps({"name": "app", "version": "1.2.2"}) + "\n",
        "README.md": "app 1.2.2\nother 1.2.2\napp 1.2.20 v1.2.2.1\n",
        "NOTES.md": "nothing 1.2.2\n",
        "meta.json": json.dumps({"packages": [{"version": "1.2.2"}], "other": "1.2.2"}) + "\n",
        "stale.json": json.dumps({"version": "0.0.1"}) + "\n",
    }
    _repo(tmp_path, files)
    rows = [
        {"path": "README.md"},
        {"path": "NOTES.md"},
        {"path": "meta.json", "pointer": "/packages/0/version"},
        {"path": "stale.json", "pointer": "/version"},
        {"path": "missing.md"},
    ]
    strategy = _release("json", path="package.json", pointer="/version")
    strategy.section["release"]["version_store"]["files"] = rows
    store = store_mod.load(tmp_path, strategy, "o/app")
    assert store.paths() == frozenset({"package.json", "README.md", "NOTES.md", "meta.json", "stale.json", "missing.md"})
    assert store.write(store.units()[0], "1.2.3") == ["package.json", "README.md", "meta.json"]
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "app 1.2.3\nother 1.2.2\napp 1.2.20 v1.2.2.1\n"
    assert (tmp_path / "NOTES.md").read_text(encoding="utf-8") == "nothing 1.2.2\n"
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8")) == {"packages": [{"version": "1.2.3"}], "other": "1.2.2"}
    assert json.loads((tmp_path / "stale.json").read_text(encoding="utf-8")) == {"version": "0.0.1"}
    assert not (tmp_path / "missing.md").exists()


def test_json_store_write_refuses_when_the_pointer_cannot_be_written(tmp_path: Path) -> None:
    """現状固定（I-004）: 保存先の位置を書き換えられない（親が配列で添字が範囲外）ときは `StoreError`。"""
    _repo(tmp_path, {"package.json": json.dumps({"versions": ["1.2.2"]}) + "\n"})
    store = store_mod.load(tmp_path, _release("json", path="package.json", pointer="/versions/0"), "o/app")
    assert store.write(store.units()[0], "1.2.3") == ["package.json"]
    assert json.loads((tmp_path / "package.json").read_text(encoding="utf-8")) == {"versions": ["1.2.3"]}
