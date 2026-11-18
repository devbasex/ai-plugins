"""リポジトリがリリースするプラグインを、マーケットプレイスの登録から読み、バージョンを書き換える。

プラグインの名前と保存先は、リポジトリの根の `.claude-plugin/marketplace.json` の `plugins`（`name`・`source`）
だけから求め、名前で決め打たない（#1756）。バージョンの SSOT はプラグインの定義（`<保存先>/.claude-plugin/plugin.json` と、
あれば `<保存先>/.codex-plugin/plugin.json`）の `version` で、2 本は同じバージョンでなければならない。

バージョンを上げるときに書き換えるのは次のものだけである。

- プラグインの定義の `version`
- マーケットプレイスのそのプラグインの `description` の中の `(v<今のバージョン>)`
- ブランチ戦略の設定の追加のファイル（`.ndf/project.json` の `branch_strategy.release.version_store.files`）。`pointer` の
  無い行は、そのプラグインの名前を含む行の中の今のバージョン（前後にバージョンの続きが無いもの）、`pointer` のある行は JSON のその位置のバージョン
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ndf.adapters import git as git_mod
from ndf.core import json_pointer

# マーケットプレイスの登録（リポジトリの根から）
MARKETPLACE = Path(".claude-plugin") / "marketplace.json"
# プラグインの定義（プラグインの保存先から）。Claude Code の定義は必ずあり、Codex の定義はあるものだけ
CLAUDE_MANIFEST = Path(".claude-plugin") / "plugin.json"
CODEX_MANIFEST = Path(".codex-plugin") / "plugin.json"
MANIFESTS = (CLAUDE_MANIFEST, CODEX_MANIFEST)
# 名前で引くプラグインの保存先の親（リポジトリの根からの `<PLUGINS_DIR>/<名前>`。版数の保存先の候補と測定が使う）
PLUGINS_DIR = "plugins"


class PluginsError(Exception):
    """マーケットプレイスの登録・プラグインの定義が読めない・形が違う・バージョンがそろわない。"""


@dataclass(frozen=True)
class Plugin:
    """リリースするプラグイン 1 つ。`dir` はリポジトリの根からの保存先（`plugins/<名前>` の形とは限らない）。"""

    name: str
    dir: str

    def manifests(self, root: Path) -> list[Path]:
        """あるプラグインの定義のファイル（先頭が無ければ `PluginsError`）。"""
        found = [inside(root, (Path(self.dir) / m).as_posix()) for m in MANIFESTS]
        if not found[0].is_file():
            raise PluginsError(f"{self.name} のプラグインの定義が無い: {found[0]}")
        return [p for p in found if p.is_file()]


def repo_path(relative: str) -> str:
    """設定に書いたリポジトリ相対のパスを、Git が返す表記（`./` や `a/../` を畳み、末尾の `/` を落とす）へそろえる。
    設定のパスと `git diff --name-only`・`git status` のパスを照らす前に通す（`./package.json` と `package.json` を同じにする）。"""
    return posixpath.normpath(relative)


def repo_rows(rows: Sequence[dict]) -> tuple[dict, ...]:
    """設定の `files` の行（`path` と、あれば `pointer`）の `path` を `repo_path` でそろえた行。"""
    return tuple({**row, "path": repo_path(row["path"])} for row in rows)


def inside(root: Path, relative: str) -> Path:
    """リポジトリの根 `root` からのパス `relative` の書き換え先。親ディレクトリ参照やシンボリックリンクの解決先が根の外なら
    `PluginsError`（設定や登録の値で作業場所の外のファイルを書き換えない。書き換えの前に必ず通す）。"""
    base = Path(root).resolve()
    target = (base / relative).resolve()
    if not target.is_relative_to(base):
        raise PluginsError(f"{relative} の解決先 {target} がリポジトリ（{base}）の外にある")
    return target


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginsError(f"{path} を読めない: {exc}") from None
    if not isinstance(data, dict):
        raise PluginsError(f"{path} が JSON のオブジェクトでない")
    return data


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _entry(raw: object) -> Plugin:
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not isinstance(raw.get("source"), str):
        raise PluginsError(f"マーケットプレイスの登録の行の形が違う（name と source の文字列が要る）: {raw!r}")
    source = raw["source"]
    if not source.startswith("./"):
        raise PluginsError(f"{raw['name']} の source がリポジトリの中の保存先（./ で始まる）でない: {source}")
    return Plugin(raw["name"], source[2:].rstrip("/"))


def load(root: Path) -> list[Plugin]:
    """マーケットプレイスに登録されたプラグイン（登録の順）。"""
    return entries(_read_json(root / MARKETPLACE))


def entries(marketplace: dict) -> list[Plugin]:
    """読んだマーケットプレイスの登録（`marketplace.json` の中身）のプラグイン（登録の順）。手元に無いバージョン（PR の head・
    本番の HEAD）の登録を読む側（`land.read_version`）も、保存先をここの `source` の解決で求める。"""
    rows = marketplace.get("plugins")
    if not isinstance(rows, list):
        raise PluginsError(f"{MARKETPLACE} に plugins の配列が無い")
    return [_entry(r) for r in rows]


def by_name(found: Sequence[Plugin]) -> dict[str, Plugin]:
    return {p.name: p for p in found}


def version(root: Path, plugin: Plugin) -> str:
    """プラグインのバージョン。定義が 2 本あれば同じバージョンでなければ `PluginsError`。"""
    seen = {}
    for path in plugin.manifests(root):
        value = _read_json(path).get("version")
        if not isinstance(value, str):
            raise PluginsError(f"{path} に version の文字列が無い")
        seen[path] = value
    if len(set(seen.values())) != 1:
        raise PluginsError(f"{plugin.name} のバージョンがプラグインの定義の間でそろわない: {seen}")
    return next(iter(seen.values()))


def manifest_path(plugin: Plugin) -> str:
    """バージョンを読むプラグインの定義（先頭）の、リポジトリの根からのパス。"""
    return f"{plugin.dir}/{MANIFESTS[0].as_posix()}" if plugin.dir else MANIFESTS[0].as_posix()


def version_at(repo_git: git_mod.Git, ref: str, plugin: Plugin) -> str | None:
    """`ref` のプラグインの定義（先頭）のバージョン。`ref` にまだ定義が無ければ None。定義が JSON として読めなければ `PluginsError`。"""
    path = manifest_path(plugin)
    if not repo_git.run("ls-tree", "--name-only", git_mod.END_OF_OPTIONS, ref, path).strip():
        return None
    try:
        value = json.loads(repo_git.run("show", git_mod.END_OF_OPTIONS, f"{ref}:{path}")).get("version")
    except (json.JSONDecodeError, AttributeError) as exc:
        raise PluginsError(f"{ref} の {path} を読めない: {exc}") from None
    return value if isinstance(value, str) else None


def _token(version_text: str) -> re.Pattern[str]:
    """バージョンを、前後にバージョンの続きが無いときだけ当てる正規表現。"""
    return re.compile(r"(?<![0-9A-Za-z.\-])" + re.escape(version_text) + r"(?![0-9A-Za-z\-]|\.[0-9A-Za-z])")


def _bump_marketplace(root: Path, plugin: Plugin, old: str, new: str) -> bool:
    path = inside(root, MARKETPLACE.as_posix())
    data = _read_json(path)
    changed = False
    for row in data["plugins"]:
        if isinstance(row, dict) and row.get("name") == plugin.name and isinstance(row.get("description"), str):
            text = row["description"].replace(f"(v{old})", f"(v{new})")
            changed, row["description"] = changed or text != row["description"], text
    if changed:
        _write_json(path, data)
    return changed


def _bump_lines(path: Path, name: str, old: str, new: str) -> bool:
    """`path` の、単位の名前 `name` を含む行の中の今のバージョンを書き換える。書き換えたか。"""
    text = path.read_text(encoding="utf-8")
    token = _token(old)
    lines = [token.sub(new, line) if name in line else line for line in text.split("\n")]
    out = "\n".join(lines)
    if out == text:
        return False
    path.write_text(out, encoding="utf-8")
    return True


def bump_pointer(path: Path, pointer: str, old: str, new: str) -> bool:
    """JSON のファイル `path` の `pointer` の位置のバージョンが `old` なら `new` へ書き換える。書き換えたか。

    object のキーと配列の添字（`/packages/0/version`）をたどる（読む側の `land.read_version` と同じ `json_pointer.resolve`）。"""
    data = _read_json(path)
    keys = json_pointer.tokens(pointer)
    if not keys:
        return False
    _, cur = json_pointer.resolve(data, json_pointer.join(keys[:-1]))
    last = keys[-1]
    if isinstance(cur, dict) and cur.get(last) == old:
        cur[last] = new
    elif isinstance(cur, list) and last.isdigit() and int(last) < len(cur) and cur[int(last)] == old:
        cur[int(last)] = new
    else:
        return False
    _write_json(path, data)
    return True


def _bump_file(root: Path, row: dict, name: str, old: str, new: str) -> bool:
    path = inside(root, row["path"])
    if not path.is_file():
        return False
    pointer = row.get("pointer")
    return bump_pointer(path, pointer, old, new) if pointer else _bump_lines(path, name, old, new)


def bump_files(root: Path, files: Sequence[dict], name: str, old: str, new: str) -> list[str]:
    """設定の `release.version_store.files` の行（`path` と、あれば `pointer`）を単位 `name` の `old` から `new` へ書き換え、
    書き換えたパスを設定の順に返す（無いファイル・書き換えの無いファイルは返さない）。"""
    return [row["path"] for row in files if _bump_file(root, row, name, old, new)]


def bump(root: Path, plugin: Plugin, new: str, extra_files: Sequence[dict] = ()) -> list[str]:
    """バージョンを `new` へ書き換え、書き換えたファイル（リポジトリの根から）を返す。`extra_files` は設定の
    `release.version_store.files` の行（`path` と、あれば `pointer`）。"""
    old = version(root, plugin)
    for row in extra_files:  # 1 つでも外を指せば、どれも書き換えずに止まる
        inside(root, row["path"])
    written = []
    for path in plugin.manifests(root):
        data = _read_json(path)
        data["version"] = new
        _write_json(path, data)
        written.append(path.relative_to(Path(root).resolve()).as_posix())
    if _bump_marketplace(root, plugin, old, new):
        written.append(MARKETPLACE.as_posix())
    written += bump_files(root, extra_files, plugin.name, old, new)
    return written
