"""バージョンの保存先のアダプター（#3572 の決定 22 のインタフェース、#3615 の決定 2）。

材料の収集・組み立て・タグは保存先の種類の名前で分岐せず、`load` が設定（`.ndf/project.json` の
`branch_strategy.release.version_store`）から選んだアダプターのインタフェース（`units`・`read`・`write`・`paths`）だけを呼ぶ（I2）。

| 種類 | バージョンの単位 | 読む | 書く | 書き換えてよいパス |
| --- | --- | --- | --- | --- |
| `plugin` | マーケットプレイスの登録の各プラグイン | ref のプラグインの定義の `version` | `plugins.bump`（定義・登録の説明・`files`） | 登録・定義・`files` |
| `json` | リポジトリ全体の 1 つ | ref の `path` の `pointer` の値 | 同じ位置と `files` | `path`・`files` |
| `pubspec` | リポジトリ全体の 1 つ | ref の `path` の最上位の `version:` の行（`+` の前） | その 1 行（数のビルド番号は 1 つ上げる） | `path` |
| `tag` | リポジトリ全体の 1 つ | ref から辿れるタグのうち、タグの書式に合う最も大きいバージョン | 書かない | 無し |
| `none` | リポジトリ全体の 1 つ | 無し（None） | 書かない | 無し |

リポジトリ全体の単位の名前は、設定の `release.plugin` があればその値、無ければリポジトリの名前（`owner/name` の `name`）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Optional, Protocol

import semver

from ndf.adapters import git as git_mod
from ndf.core import json_pointer
from ndf.core.branch_strategy import Strategy
from ndf.domains.release import names_decide
from ndf.domains.release import plugins as plugins_mod

# `read` が None の単位を上げるときの起点（まだ出していない単位の今のバージョン）
INITIAL = str(semver.Version(0, 0, 0))
# `pubspec.yaml` の最上位の `version:` の行（インデントの無い行だけ）
_PUBSPEC_LINE = re.compile(r"^version:[ \t]*(?P<quote>['\"]?)(?P<value>[^'\"\s#]+)(?P=quote)(?P<tail>[ \t]*(?:#.*)?)$", re.MULTILINE)


class StoreError(Exception):
    """保存先の種類が表に無い・保存先のバージョンが読めない・書けない。"""


class Unit(NamedTuple):
    """バージョンの単位。`dir` はリポジトリの根からの保存先で、リポジトリ全体の単位は空文字。"""

    name: str
    dir: str


class VersionStore(Protocol):
    """保存先のアダプターのインタフェース。"""

    kind: str

    def units(self) -> list[Unit]: ...

    def read(self, repo_git: git_mod.Git, ref: str, unit: Unit) -> Optional[str]: ...

    def write(self, unit: Unit, new: str) -> list[str]: ...

    def paths(self) -> frozenset[str]: ...


def tags_at(repo_git: git_mod.Git, ref: str) -> list[str]:
    """`ref` から辿れるタグの名前（手元へ取得したタグだけ。順は不定）。"""
    return [t for t in repo_git.run("tag", "--list", "--merged", ref).splitlines() if t.strip()]


def _show(repo_git: git_mod.Git, ref: str, path: str) -> Optional[str]:
    """`ref` の `path` の中身。`ref` に無ければ None。"""
    done = repo_git.attempt("show", git_mod.END_OF_OPTIONS, f"{ref}:{path}")
    return done.stdout if done.returncode == 0 else None


def pubspec_version(text: str) -> Optional[str]:
    """`pubspec.yaml` の最上位の `version:` の値の `+` の前（ビルド番号は任意）。行が無ければ None。"""
    m = _PUBSPEC_LINE.search(text)
    return m.group("value").partition("+")[0] if m is not None else None


def pubspec_replace(text: str, new: str) -> str:
    """最上位の `version:` の行だけを `new` に置き換える。ビルド番号が数なら 1 つ上げ、数でなければそのまま残し、無ければ付けない。

    行が無いか 2 つ以上なら `StoreError`。ほかの行は 1 文字も変えない（release-please の Dart の書き換えに倣う）。"""
    found = list(_PUBSPEC_LINE.finditer(text))
    if len(found) != 1:
        raise StoreError(f"pubspec.yaml の最上位の version: の行が 1 つでない（{len(found)} 行）")
    m = found[0]
    _, plus, build = m.group("value").partition("+")
    value = new
    if plus:
        value = f"{new}+{int(build) + 1}" if build.isdigit() else f"{new}+{build}"
    line = f"version: {m.group('quote')}{value}{m.group('quote')}{m.group('tail')}"
    return text[: m.start()] + line + text[m.end() :]


@dataclass
class PluginStore:
    """マーケットプレイスに登録したプラグインごとにバージョンを持つ保存先。"""

    root: Path
    files: tuple[dict, ...] = ()
    kind: str = "plugin"

    @classmethod
    def from_setting(cls, root: Path, name: str, store: dict, release: dict) -> "PluginStore":
        return cls(Path(root), tuple(store["files"]) if "files" in store else ())

    def _plugins(self) -> dict[str, plugins_mod.Plugin]:
        return plugins_mod.by_name(plugins_mod.load(self.root))

    def units(self) -> list[Unit]:
        return [Unit(p.name, p.dir) for p in plugins_mod.load(self.root)]

    def read(self, repo_git: git_mod.Git, ref: str, unit: Unit) -> Optional[str]:
        return plugins_mod.version_at(repo_git, ref, plugins_mod.Plugin(unit.name, unit.dir))

    def write(self, unit: Unit, new: str) -> list[str]:
        return plugins_mod.bump(self.root, self._plugins()[unit.name], new, self.files)

    def paths(self) -> frozenset[str]:
        found = {plugins_mod.MARKETPLACE.as_posix(), *(row["path"] for row in self.files)}
        for p in self._plugins().values():
            found.update(m.relative_to(self.root).as_posix() for m in p.manifests(self.root))
        return frozenset(found)


@dataclass
class JsonStore:
    """JSON のファイルの 1 つの位置にリポジトリ全体のバージョンを持つ保存先（`package.json` の `/version` など）。"""

    root: Path
    name: str
    path: str
    pointer: str
    files: tuple[dict, ...] = ()
    kind: str = "json"

    @classmethod
    def from_setting(cls, root: Path, name: str, store: dict, release: dict) -> "JsonStore":
        return cls(Path(root), name, store["path"], store["pointer"], tuple(store["files"]) if "files" in store else ())

    def units(self) -> list[Unit]:
        return [Unit(self.name, "")]

    def _value(self, text: str) -> str:
        try:
            doc = json.loads(text)
        except ValueError as exc:
            raise StoreError(f"{self.path} を JSON として読めない: {exc}") from None
        _, value = json_pointer.resolve(doc, self.pointer)
        if not isinstance(value, str) or not value:
            raise StoreError(f"{self.path} の {self.pointer} がバージョンの文字列でない: {value!r}")
        return value

    def read(self, repo_git: git_mod.Git, ref: str, unit: Unit) -> Optional[str]:
        text = _show(repo_git, ref, self.path)
        return None if text is None else self._value(text)

    def write(self, unit: Unit, new: str) -> list[str]:
        target = self.root / self.path
        old = self._value(target.read_text(encoding="utf-8"))
        if not plugins_mod.bump_pointer(target, self.pointer, old, new):
            raise StoreError(f"{self.path} の {self.pointer} を書き換えられない")
        return [self.path, *plugins_mod.bump_files(self.root, self.files, unit.name, old, new)]

    def paths(self) -> frozenset[str]:
        return frozenset({self.path, *(row["path"] for row in self.files)})


@dataclass
class PubspecStore:
    """Flutter の `pubspec.yaml` の `version:` にリポジトリ全体のバージョンを持つ保存先。"""

    root: Path
    name: str
    path: str
    kind: str = "pubspec"

    @classmethod
    def from_setting(cls, root: Path, name: str, store: dict, release: dict) -> "PubspecStore":
        return cls(Path(root), name, store["path"])

    def units(self) -> list[Unit]:
        return [Unit(self.name, "")]

    def read(self, repo_git: git_mod.Git, ref: str, unit: Unit) -> Optional[str]:
        text = _show(repo_git, ref, self.path)
        return None if text is None else pubspec_version(text)

    def write(self, unit: Unit, new: str) -> list[str]:
        target = self.root / self.path
        if not target.is_file():
            raise StoreError(f"{self.path} が無い")
        try:
            replaced = pubspec_replace(target.read_text(encoding="utf-8"), new)
        except StoreError as exc:
            raise StoreError(f"{self.path}: {exc}") from None
        target.write_text(replaced, encoding="utf-8")
        return [self.path]

    def paths(self) -> frozenset[str]:
        return frozenset({self.path})


@dataclass
class TagStore:
    """タグだけでバージョンを持つ保存先（バージョンのファイルを持たない）。"""

    name: str
    formats: names_decide.NameFormats
    kind: str = "tag"

    @classmethod
    def from_setting(cls, root: Path, name: str, store: dict, release: dict) -> "TagStore":
        return cls(name, names_decide.formats_of(release))

    def units(self) -> list[Unit]:
        return [Unit(self.name, "")]

    def read(self, repo_git: git_mod.Git, ref: str, unit: Unit) -> Optional[str]:
        top = names_decide.highest_tag(self.formats, unit.name, tags_at(repo_git, ref))
        return top[1] if top is not None else None

    def write(self, unit: Unit, new: str) -> list[str]:
        return []

    def paths(self) -> frozenset[str]:
        return frozenset()


@dataclass
class NoneStore:
    """バージョンを持たない保存先。"""

    name: str
    kind: str = "none"

    @classmethod
    def from_setting(cls, root: Path, name: str, store: dict, release: dict) -> "NoneStore":
        return cls(name)

    def units(self) -> list[Unit]:
        return [Unit(self.name, "")]

    def read(self, repo_git: git_mod.Git, ref: str, unit: Unit) -> Optional[str]:
        return None

    def write(self, unit: Unit, new: str) -> list[str]:
        return []

    def paths(self) -> frozenset[str]:
        return frozenset()


# 種類 → アダプターのクラス（設定のスキーマの `version_store` の `oneOf` の種類と同じ集合。テストが照らす）
KINDS: dict[str, type] = {cls.kind: cls for cls in (PluginStore, JsonStore, PubspecStore, TagStore, NoneStore)}


def load(root: Path, strategy: Strategy, repo_name: str) -> VersionStore:
    """設定の保存先の種類のアダプター。表に無い種類は `StoreError`。`repo_name` は `owner/name` か `name`。"""
    store = strategy.version_store
    kind = store.get("kind")
    if kind not in KINDS:
        raise StoreError(f"バージョンの保存先の種類 {kind!r} は扱えない。取れる種類: {', '.join(KINDS)}")
    plugin = strategy.release_plugin
    name = plugin if plugin is not None else repo_name.rsplit("/", 1)[-1]
    return KINDS[kind].from_setting(root, name, store, strategy.section["release"])
