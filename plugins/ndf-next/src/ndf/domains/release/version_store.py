"""バージョンの置き場（`.ndf/release.json` の `version_store`）のインタフェースとアダプタ（#3572 の決定 22・I18）。

バージョン上げ・PR の検査・CD の確かめ・タグは、このインタフェースだけを呼び、`plugins`（マーケットプレイスの登録）を
直接呼ばない。置き場はリリースの単位（名前と、変化を数える根のディレクトリ）の一覧と、単位ごとのバージョンの読み書きを持つ。

| `kind` | 持つもの |
| --- | --- |
| `plugin` | マーケットプレイスの登録（`.claude-plugin/marketplace.json`）の各プラグインの定義と、宣言の `version_files` |
| `json` | 1 つの JSON のファイル（`path`）の 1 つの鍵（`key`）。単位はリポジトリ全体の 1 つ（名前は `name`） |

置き場を書いていない宣言は、取り込み方の行が `github-flow` でなければ `plugin`（今の経路）を使い、`github-flow` なら止まる。
ほかのリリースの形のアダプタは #3615 が足す。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Protocol

from ndf.adapters import git as git_mod
from ndf.domains.release import changes_decide, gather, version_decide
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release import plugins as plugins_mod

# 置き場の種類（宣言のスキーマの列挙と同じ値）
KINDS = ("plugin", "json")
# `json` の置き場の単位の根（リポジトリ全体）
ROOT_UNIT_DIR = "."


class VersionStoreError(Exception):
    """置き場を選べない・読めない・書けない（種類が表に無い・鍵が欠ける・値が文字列でない）。"""


class Unit(NamedTuple):
    """リリースの単位（タグと CHANGELOG の見出しの名前と、変化を数える根のディレクトリ）。"""

    name: str
    dir: str


class VersionStore(Protocol):
    """バージョンの置き場のインタフェース（`root` は読み書きする作業場所）。"""

    kind: str
    root: Path

    def units(self) -> list[Unit]: ...

    def read(self, repo_git: git_mod.Git, ref: str, unit: Unit) -> str | None: ...

    def write(self, unit: Unit, new: str) -> list[str]: ...

    def paths(self) -> frozenset[str]: ...

    def changes(self, paths: Sequence[str], decl: decl_mod.Declaration) -> list[changes_decide.Change]: ...


@dataclass(frozen=True)
class PluginStore:
    """マーケットプレイスの登録とプラグインの定義に置くバージョン（ai-plugins が選ぶ置き場）。"""

    root: Path
    version_files: tuple[str, ...] = ()
    kind: str = KINDS[0]

    def _found(self) -> list[plugins_mod.Plugin]:
        try:
            return plugins_mod.load(self.root)
        except plugins_mod.PluginsError as exc:
            raise VersionStoreError(f"置き場 {self.kind} は {plugins_mod.MARKETPLACE.as_posix()} を要る: {exc}") from None

    def _plugin(self, unit: Unit) -> plugins_mod.Plugin:
        return plugins_mod.Plugin(unit.name, unit.dir)

    def units(self) -> list[Unit]:
        return [Unit(p.name, p.dir) for p in self._found()]

    def read(self, repo_git: git_mod.Git, ref: str, unit: Unit) -> str | None:
        try:
            return gather.version_at(repo_git, self._plugin(unit), ref)
        except plugins_mod.PluginsError as exc:
            raise VersionStoreError(str(exc)) from None

    def write(self, unit: Unit, new: str) -> list[str]:
        try:
            return plugins_mod.bump(self.root, self._plugin(unit), new, self.version_files)
        except plugins_mod.PluginsError as exc:
            raise VersionStoreError(str(exc)) from None

    def paths(self) -> frozenset[str]:
        out = {plugins_mod.MARKETPLACE.as_posix(), *self.version_files}
        try:
            for p in self._found():
                out |= {m.relative_to(self.root).as_posix() for m in p.manifests(self.root)}
        except plugins_mod.PluginsError as exc:
            raise VersionStoreError(str(exc)) from None
        return frozenset(out)

    def changes(self, paths: Sequence[str], decl: decl_mod.Declaration) -> list[changes_decide.Change]:
        return changes_decide.changes(paths, {u.name: u.dir for u in self.units()}, decl.undistributed)


@dataclass(frozen=True)
class JsonStore:
    """1 つの JSON のファイルの 1 つの鍵に置くバージョン（`package.json` の `version` など）。"""

    root: Path
    path: str
    key: str
    name: str
    kind: str = KINDS[1]

    def units(self) -> list[Unit]:
        return [Unit(self.name, ROOT_UNIT_DIR)]

    def _value(self, text: str, where: str) -> str:
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise VersionStoreError(f"{where} の {self.path} を JSON として読めない: {exc}") from None
        value = data.get(self.key) if isinstance(data, dict) else None
        if not isinstance(value, str):
            raise VersionStoreError(f"{where} の {self.path} の {self.key} が文字列でない: {value!r}")
        return value

    def read(self, repo_git: git_mod.Git, ref: str, unit: Unit) -> str | None:
        if not repo_git.run("ls-tree", "--name-only", git_mod.END_OF_OPTIONS, ref, self.path).strip():
            return None
        return self._value(repo_git.run("show", git_mod.END_OF_OPTIONS, f"{ref}:{self.path}"), ref)

    def write(self, unit: Unit, new: str) -> list[str]:
        target = self.root / self.path
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise VersionStoreError(f"{self.path} を読めない: {exc}") from None
        if not isinstance(data, dict) or not isinstance(data.get(self.key), str):
            raise VersionStoreError(f"{self.path} に {self.key} の文字列が無い")
        data[self.key] = new
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return [self.path]

    def paths(self) -> frozenset[str]:
        return frozenset({self.path})

    def changes(self, paths: Sequence[str], decl: decl_mod.Declaration) -> list[changes_decide.Change]:
        prefixed = [f"{ROOT_UNIT_DIR}/{p}" for p in paths]
        found = changes_decide.split(prefixed, self.name, ROOT_UNIT_DIR, decl.undistributed)
        cut = len(ROOT_UNIT_DIR) + 1  # 接頭辞を外し、パスをリポジトリの根からに戻す
        return [
            found._replace(distributed=tuple(p[cut:] for p in found.distributed), undistributed=tuple(p[cut:] for p in found.undistributed))
        ]


def _text(raw: dict, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        raise VersionStoreError(f"version_store の {key} が空でない文字列でない: {value!r}")
    return value


def of(root: Path, decl: decl_mod.Declaration, flow: str) -> VersionStore:
    """宣言の置き場からアダプタを選ぶ。置き場が無ければ、`github-flow` でない行は `plugin`、`github-flow` は止める。"""
    raw = decl.version_store
    if raw is None:
        if flow == decl_mod.GITHUB_FLOW:
            where = decl_mod.path_of(root).relative_to(root).as_posix()
            raise VersionStoreError(f"取り込み方 {flow} では {where} の version_store が要る（取れる種類: {', '.join(KINDS)}）")
        return PluginStore(root, decl.version_files)
    kind = raw.get("kind") if isinstance(raw, dict) else None
    if kind == KINDS[0]:
        return PluginStore(root, decl.version_files)
    if kind == KINDS[1]:
        return JsonStore(root, _text(raw, "path"), _text(raw, "key"), _text(raw, "name"))
    raise VersionStoreError(f"version_store.kind {kind!r} が置き場の表に無い（取れる種類: {', '.join(KINDS)}）")


def plan(
    store: VersionStore,
    repo_git: git_mod.Git,
    paths: Sequence[str],
    decl: decl_mod.Declaration,
    level: str,
    tip_ref: str,
) -> list[version_decide.Planned]:
    """範囲の変化から、バージョンを上げる単位と次のバージョン（`tip_ref` のバージョンとタグの最大より大きい）を決める。"""
    wanted = {c.plugin for c in store.changes(paths, decl) if changes_decide.needs_bump(c, decl.bump_undistributed)}
    tags = gather.remote_tags(repo_git) if wanted else []
    channel = version_decide.PRODUCTION
    out = []
    for unit in store.units():
        if unit.name not in wanted:
            continue
        current = store.read(repo_git, tip_ref, unit)
        if current is None:
            raise VersionStoreError(f"{tip_ref} に {unit.name} のバージョンが無い")
        tagged = [v for v in (version_decide.version_of_tag(unit.name, t) for t in tags) if v is not None]
        shipped = version_decide.highest([current, *tagged])
        nxt = version_decide.clear_of(version_decide.next_version(current, level, channel), shipped, level, channel)
        out.append(version_decide.Planned(unit.name, current, nxt))
    return out


def bumped(store: VersionStore, repo_git: git_mod.Git, revision: str) -> list[Unit]:
    """コミット `revision` で（第 1 親と比べて）バージョンが変わった単位。親が読めなければ空。"""
    try:
        parent = repo_git.rev_parse(f"{revision}^1")
    except git_mod.GitError:  # 根のコミットは親を持たず、バージョンを上げた単位は無い
        return []
    return [u for u in store.units() if store.read(repo_git, parent, u) != store.read(repo_git, revision, u)]


def unit_at(store: VersionStore, repo_git: git_mod.Git, revision: str, version: str) -> Unit:
    """タグを確かめる単位（`revision` でバージョンが `version` に変わった単位。無ければ変わった先頭、それも無ければ単位の先頭）。"""
    changed = bumped(store, repo_git, revision)
    hits = [u for u in changed if store.read(repo_git, revision, u) == version]
    listed = hits or changed or store.units()
    if not listed:
        raise VersionStoreError("置き場にリリースの単位が無い")
    return listed[0]
