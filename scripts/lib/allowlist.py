"""構造チェックの例外リスト（1 項目 1 ファイル）の共通部品。

`scripts/check-script-structure.py` と `scripts/check-ndf-contracts.py` が同じ規則で例外の項目を読む。
1 項目は `{"path", "name", "kind", "reason"}`（チェックによって足すキーがある）を持つ JSON で、
ファイル名は `allow_file_name()` が path・name・kind から決める。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path

BASE_KEYS = frozenset({"path", "name", "kind", "reason"})


class AllowError(Exception):
    """例外リストが読めない・形が違う。"""


def allow_file_name(row: dict) -> str:
    """例外リストの 1 項目のファイル名。path の `/` を `__` に、各部を `--` でつなぐ（空の部は省く）。"""
    parts = [row["path"].replace("/", "__"), row["name"], row["kind"]]
    return "--".join(p for p in parts if p) + ".json"


def load_allow(
    path: Path,
    kinds: Iterable[str],
    extra_keys: Callable[[dict], set[str]] = lambda r: set(),
    validate: Callable[[Path, dict], None] | None = None,
) -> list[dict]:
    """置き場（ディレクトリ）の `*.json` を 1 ファイル 1 項目として読む。置き場が無ければ空。

    `extra_keys` は項目ごとに足すキー、`validate` はチェック固有の検査（`AllowError` を上げる）。
    """
    if not path.exists():
        return []
    if not path.is_dir():
        raise AllowError(f"例外リストの置き場はディレクトリにする: {path}")
    kinds = tuple(kinds)
    rows = []
    for f in sorted(path.glob("*.json")):
        try:
            r = json.loads(f.read_text())
        except ValueError as e:
            raise AllowError(f"例外リストを読めない: {f}: {e}")
        keys = set(BASE_KEYS) | (extra_keys(r) if isinstance(r, dict) else set())
        if not isinstance(r, dict) or set(r) != keys:
            raise AllowError(f"例外リストの {f.name} は {'・'.join(sorted(keys))} を持つ: {r}")
        if validate is not None:
            validate(f, r)
        if r["kind"] not in kinds:
            raise AllowError(f"例外リストの {f.name} の kind は {'/'.join(kinds)} のどれか: {r['kind']}")
        if not isinstance(r["reason"], str) or not r["reason"].strip():
            raise AllowError(f"例外リストの {f.name} に理由が無い: {r['path']}:{r['name']}")
        if not all(isinstance(r[k], str) for k in ("path", "name")) or f.name != allow_file_name(r):
            raise AllowError(f"例外リストの {f.name} は {allow_file_name(r)} という名前にする")
        rows.append(r)
    return rows


def rel_target(root: Path, target: str) -> str:
    """範囲の指定を根からの相対パス（`/` 区切り・先頭の `./` と末尾の `/` なし）にする。根の外の絶対パスはそのまま返す。"""
    p = Path(target)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(root)
        except ValueError:
            return p.as_posix()
    parts = [x for x in p.as_posix().split("/") if x not in ("", ".")]
    return "/".join(parts) or "."


def under(path: str, targets: Iterable[str]) -> bool:
    """`path` が範囲の指定のどれかと同じか、指定のディレクトリの下にあるか（`.` は木全体）。"""
    return any(t == "." or path == t or path.startswith(t + "/") for t in targets)


def unused(allow: Iterable[dict], hit: set[tuple[str, str, str]]) -> list[dict]:
    """違反に当たらなかった項目（`unused-allow` の候補）。`hit` は当たった `(path, name, kind)`。"""
    return [r for r in allow if (r["path"], r["name"], r["kind"]) not in hit]
