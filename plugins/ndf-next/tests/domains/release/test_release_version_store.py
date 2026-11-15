"""バージョンの置き場のインタフェースとアダプタ plugin・json（本物の git。#3572 の決定 22・I18）。"""

from __future__ import annotations

import json
from pathlib import Path

import git_sandbox
import pytest

from ndf.adapters import git as git_mod
from ndf.domains.release import changes_decide, version_store
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release import plugins as plugins_mod

from ._fakes import init_repo, write_json

WEB = version_store.Unit("web", version_store.ROOT_UNIT_DIR)


def _commit(root: Path, message: str) -> str:
    git_sandbox.sh(root, "add", "-A")
    git_sandbox.sh(root, "commit", "-q", "-m", message)
    return git_sandbox.sh(root, "rev-parse", "HEAD").strip()


def _json_repo(tmp_path: Path) -> tuple[Path, version_store.JsonStore]:
    root = init_repo(tmp_path / "web")
    write_json(root / "package.json", {"name": "web", "version": "1.2.3", "scripts": {"build": "x"}})
    return root, version_store.JsonStore(root, "package.json", "version", "web")


def _plugin_repo(tmp_path: Path) -> tuple[Path, version_store.PluginStore]:
    root = init_repo(tmp_path / "kit")
    write_json(root / ".claude-plugin" / "marketplace.json", {"plugins": [{"name": "kit", "source": "./tools/kit"}]})
    write_json(root / "tools" / "kit" / ".claude-plugin" / "plugin.json", {"name": "kit", "version": "1.2.3"})
    (root / "README.md").write_text("kit 1.2.3\n", encoding="utf-8")
    return root, version_store.PluginStore(root, ("README.md",))


# --- アダプタの選び方（of） ---------------------------------------------------------------------------


def test_of_picks_the_declared_store_and_the_plugin_store_for_other_rows(tmp_path: Path) -> None:
    decl = decl_mod.Declaration(version_store={"kind": "json", "path": "package.json", "key": "version", "name": "web"})
    assert isinstance(version_store.of(tmp_path, decl, decl_mod.GITHUB_FLOW), version_store.JsonStore)
    plugin = decl_mod.Declaration(version_store={"kind": "plugin"}, version_files=("README.md",))
    got = version_store.of(tmp_path, plugin, decl_mod.GITHUB_FLOW)
    assert isinstance(got, version_store.PluginStore) and got.version_files == ("README.md",)
    assert isinstance(version_store.of(tmp_path, decl_mod.Declaration(), "promote-whole"), version_store.PluginStore)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {"kind": "pubspec"},
        {"kind": "json", "path": "package.json", "key": "version"},
        {"kind": "json", "path": "", "key": "v", "name": "x"},
    ],
)
def test_of_refuses_a_missing_or_unknown_store_on_the_github_flow(tmp_path: Path, raw: dict | None) -> None:
    """I18: github-flow で置き場が無い・種類が表に無い・鍵が欠ければ、既定へ倒さずに止まる。"""
    with pytest.raises(version_store.VersionStoreError):
        version_store.of(tmp_path, decl_mod.Declaration(version_store=raw), decl_mod.GITHUB_FLOW)


def test_the_release_declaration_carries_the_version_store(tmp_path: Path) -> None:
    write_json(decl_mod.path_of(tmp_path), {"version": 1, "version_store": {"kind": "plugin"}})
    assert decl_mod.load(tmp_path).version_store == {"kind": "plugin"}
    write_json(decl_mod.path_of(tmp_path), {"version": 1, "version_store": "plugin"})
    with pytest.raises(decl_mod.DeclarationError):
        decl_mod.load(tmp_path)


# --- 置き場 json ---------------------------------------------------------------------------------------


def test_json_store_reads_writes_only_the_key_and_keeps_the_order(tmp_path: Path) -> None:
    root, store = _json_repo(tmp_path)
    sha = _commit(root, "init")
    repo_git = git_mod.Git(root)
    assert store.units() == [WEB] and store.read(repo_git, sha, WEB) == "1.2.3" and store.paths() == frozenset({"package.json"})
    assert store.write(WEB, "1.3.0") == ["package.json"]
    after = json.loads((root / "package.json").read_text(encoding="utf-8"))
    assert list(after) == ["name", "version", "scripts"] and after == {"name": "web", "version": "1.3.0", "scripts": {"build": "x"}}


def test_json_store_reads_none_without_the_file_and_refuses_a_non_string(tmp_path: Path) -> None:
    root, store = _json_repo(tmp_path)
    write_json(root / "other.json", {})
    sha = _commit(root, "init")
    missing = version_store.JsonStore(root, "nothing.json", "version", "web")
    assert missing.read(git_mod.Git(root), sha, WEB) is None
    write_json(root / "package.json", {"name": "web", "version": 3})
    with pytest.raises(version_store.VersionStoreError):
        store.read(git_mod.Git(root), _commit(root, "number"), WEB)
    with pytest.raises(version_store.VersionStoreError):
        version_store.JsonStore(root, "other.json", "version", "web").write(WEB, "1.0.0")


def test_json_store_counts_every_path_under_the_root_as_one_unit() -> None:
    store = version_store.JsonStore(Path("."), "package.json", "version", "web")
    (got,) = store.changes(["src/a.js", "tests/test_a.py"], decl_mod.Declaration())
    assert got == changes_decide.Change("web", ("src/a.js",), ("tests/test_a.py",))
    (only_tests,) = store.changes(["tests/test_a.py"], decl_mod.Declaration())
    assert not changes_decide.needs_bump(only_tests, False)


# --- 置き場 plugin ------------------------------------------------------------------------------------


def test_plugin_store_wraps_the_marketplace_and_the_version_files(tmp_path: Path) -> None:
    root, store = _plugin_repo(tmp_path)
    sha = _commit(root, "init")
    (unit,) = store.units()
    assert unit == version_store.Unit("kit", "tools/kit") and store.read(git_mod.Git(root), sha, unit) == "1.2.3"
    assert store.paths() == frozenset({".claude-plugin/marketplace.json", "tools/kit/.claude-plugin/plugin.json", "README.md"})
    assert set(store.write(unit, "1.3.0")) == {"tools/kit/.claude-plugin/plugin.json", "README.md"}
    assert plugins_mod.version(root, plugins_mod.load(root)[0]) == "1.3.0"
    assert store.changes(["tools/kit/a.py"], decl_mod.Declaration()) == [changes_decide.Change("kit", ("tools/kit/a.py",), ())]


def test_plugin_store_without_a_marketplace_is_a_store_error(tmp_path: Path) -> None:
    with pytest.raises(version_store.VersionStoreError, match="marketplace.json"):
        version_store.PluginStore(tmp_path).units()


# --- インタフェースを使う予定・変わった単位・タグの単位 --------------------------------------------------


def test_plan_bumps_past_the_tip_and_the_tags(tmp_path: Path) -> None:
    root, store = _json_repo(tmp_path)
    sha = _commit(root, "init")
    origin = tmp_path / "origin.git"
    git_sandbox.sh(tmp_path, "clone", "-q", "--bare", str(root), str(origin))
    git_sandbox.sh(root, "remote", "add", "origin", str(origin))
    repo_git = git_mod.Git(root)
    (planned,) = version_store.plan(store, repo_git, ["src/a.js"], decl_mod.Declaration(), "minor", sha)
    assert (planned.plugin, planned.current, planned.next) == ("web", "1.2.3", "1.3.0")
    git_sandbox.sh(root, "push", "-q", "origin", f"{sha}:refs/tags/web--v1.3.0")
    (planned,) = version_store.plan(store, repo_git, ["src/a.js"], decl_mod.Declaration(), "minor", sha)
    assert planned.next == "1.4.0"
    assert version_store.plan(store, repo_git, ["tests/test_a.py"], decl_mod.Declaration(), "minor", sha) == []


def test_bumped_and_unit_at_read_the_units_whose_version_changed(tmp_path: Path) -> None:
    root, store = _json_repo(tmp_path)
    first = _commit(root, "init")
    repo_git = git_mod.Git(root)
    assert version_store.bumped(store, repo_git, first) == []
    write_json(root / "package.json", {"name": "web", "version": "1.3.0", "scripts": {"build": "x"}})
    second = _commit(root, "Release: web v1.3.0")
    assert version_store.bumped(store, repo_git, second) == [WEB]
    assert version_store.unit_at(store, repo_git, second, "1.3.0") == WEB
    assert version_store.unit_at(store, repo_git, first, "9.9.9") == WEB
