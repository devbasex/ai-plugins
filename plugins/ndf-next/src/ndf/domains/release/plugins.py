"""リポジトリがリリースするプラグインを、マーケットプレイスの登録から読み、版を書き換える。

プラグインの名前と置き場は、リポジトリの根の `.claude-plugin/marketplace.json` の `plugins`（`name`・`source`）
だけから求め、名前で決め打たない（#1756）。版の正本はプラグインの定義（`<置き場>/.claude-plugin/plugin.json` と、
あれば `<置き場>/.codex-plugin/plugin.json`）の `version` で、2 本は同じ版でなければならない。

版を上げるときに書き換えるのは次のものだけである。

- プラグインの定義の `version`
- マーケットプレイスのそのプラグインの `description` の中の `(v<今の版>)`
- ブランチ戦略の宣言の追加のファイル（`.ndf/project.json` の `branch_strategy.release.version_store.files`）。`pointer` の
  無い行は、そのプラグインの名前を含む行の中の今の版（前後に版の続きが無いもの）、`pointer` のある行は JSON のその位置の版
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ndf.core import json_pointer

# マーケットプレイスの登録（リポジトリの根から）
MARKETPLACE = Path(".claude-plugin") / "marketplace.json"
# プラグインの定義（プラグインの置き場から）。Claude Code の定義は必ずあり、Codex の定義はあるものだけ
CLAUDE_MANIFEST = Path(".claude-plugin") / "plugin.json"
CODEX_MANIFEST = Path(".codex-plugin") / "plugin.json"
MANIFESTS = (CLAUDE_MANIFEST, CODEX_MANIFEST)
# 名前で引くプラグインの置き場の親（リポジトリの根からの `<PLUGINS_DIR>/<名前>`。版数の置き場の候補と測定が使う）
PLUGINS_DIR = "plugins"


class PluginsError(Exception):
    """マーケットプレイスの登録・プラグインの定義が読めない・形が違う・版がそろわない。"""


@dataclass(frozen=True)
class Plugin:
    """リリースするプラグイン 1 つ。`dir` はリポジトリの根からの置き場（`plugins/<名前>` の形とは限らない）。"""

    name: str
    dir: str

    def manifests(self, root: Path) -> list[Path]:
        """あるプラグインの定義のファイル（先頭が無ければ `PluginsError`）。"""
        found = [root / self.dir / m for m in MANIFESTS]
        if not found[0].is_file():
            raise PluginsError(f"{self.name} のプラグインの定義が無い: {found[0]}")
        return [p for p in found if p.is_file()]


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
        raise PluginsError(f"{raw['name']} の source がリポジトリの中の置き場（./ で始まる）でない: {source}")
    return Plugin(raw["name"], source[2:].rstrip("/"))


def load(root: Path) -> list[Plugin]:
    """マーケットプレイスに登録されたプラグイン（登録の順）。"""
    return entries(_read_json(root / MARKETPLACE))


def entries(marketplace: dict) -> list[Plugin]:
    """読んだマーケットプレイスの登録（`marketplace.json` の中身）のプラグイン（登録の順）。手元に無い版（PR の head・
    本番の先端）の登録を読む側（`land.store_version`）も、置き場をここの `source` の解決で求める。"""
    rows = marketplace.get("plugins")
    if not isinstance(rows, list):
        raise PluginsError(f"{MARKETPLACE} に plugins の配列が無い")
    return [_entry(r) for r in rows]


def by_name(found: Sequence[Plugin]) -> dict[str, Plugin]:
    return {p.name: p for p in found}


def version(root: Path, plugin: Plugin) -> str:
    """プラグインの版。定義が 2 本あれば同じ版でなければ `PluginsError`。"""
    seen = {}
    for path in plugin.manifests(root):
        value = _read_json(path).get("version")
        if not isinstance(value, str):
            raise PluginsError(f"{path} に version の文字列が無い")
        seen[path] = value
    if len(set(seen.values())) != 1:
        raise PluginsError(f"{plugin.name} の版がプラグインの定義の間でそろわない: {seen}")
    return next(iter(seen.values()))


def _token(version_text: str) -> re.Pattern[str]:
    """版を、前後に版の続きが無いときだけ当てる正規表現。"""
    return re.compile(r"(?<![0-9A-Za-z.\-])" + re.escape(version_text) + r"(?![0-9A-Za-z\-]|\.[0-9A-Za-z])")


def _bump_marketplace(root: Path, plugin: Plugin, old: str, new: str) -> bool:
    path = root / MARKETPLACE
    data = _read_json(path)
    changed = False
    for row in data["plugins"]:
        if isinstance(row, dict) and row.get("name") == plugin.name and isinstance(row.get("description"), str):
            text = row["description"].replace(f"(v{old})", f"(v{new})")
            changed, row["description"] = changed or text != row["description"], text
    if changed:
        _write_json(path, data)
    return changed


def _bump_lines(path: Path, plugin: Plugin, old: str, new: str) -> bool:
    """`path` の、プラグインの名前を含む行の中の今の版を書き換える。書き換えたか。"""
    text = path.read_text(encoding="utf-8")
    token = _token(old)
    lines = [token.sub(new, line) if plugin.name in line else line for line in text.split("\n")]
    out = "\n".join(lines)
    if out == text:
        return False
    path.write_text(out, encoding="utf-8")
    return True


def _bump_pointer(path: Path, pointer: str, old: str, new: str) -> bool:
    """JSON のファイル `path` の `pointer` の位置の版が `old` なら `new` へ書き換える。書き換えたか。

    object のキーと配列の添字（`/packages/0/version`）をたどる（読む側の `land.store_version` と同じ `json_pointer.resolve`）。"""
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


def _bump_file(root: Path, row: dict, plugin: Plugin, old: str, new: str) -> bool:
    path = root / row["path"]
    if not path.is_file():
        return False
    pointer = row.get("pointer")
    return _bump_pointer(path, pointer, old, new) if pointer else _bump_lines(path, plugin, old, new)


def bump(root: Path, plugin: Plugin, new: str, extra_files: Sequence[dict] = ()) -> list[str]:
    """版を `new` へ書き換え、書き換えたファイル（リポジトリの根から）を返す。`extra_files` は宣言の
    `release.version_store.files` の行（`path` と、あれば `pointer`）。"""
    old = version(root, plugin)
    written = []
    for path in plugin.manifests(root):
        data = _read_json(path)
        data["version"] = new
        _write_json(path, data)
        written.append(path.relative_to(root).as_posix())
    if _bump_marketplace(root, plugin, old, new):
        written.append(MARKETPLACE.as_posix())
    written += [row["path"] for row in extra_files if _bump_file(root, row, plugin, old, new)]
    return written
