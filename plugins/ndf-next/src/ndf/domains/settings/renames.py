"""利用者の設定（`.ndf/`）のキーの改名（#3613）。改名表 `contracts/data/ndf-settings-renames.json` を読む。

改名表のうち、同じファイルの中でキーの名前と値を改める行だけを扱う。ブランチ戦略のセクションへ移す行と移した後に消す行は
`ndf project branch-strategy migrate` の受け持ちで、振り分けは `ndf.contracts.settings_renames` が持つ。

旧いキーの名前と旧い値はコードに書かず、改名表から引く（同じ事実を 2 か所に持たない）。この関数群は設定ファイルを
読み書きせず、終了コードを持たない。移行のコマンド（`ndf settings migrate`）・設定を読む側の止める検査・規約の規則
spec-decl が同じ関数を通して改名表を読む。

位置は JSON Pointer（RFC 6901）で書き、`*` は配列のすべての要素を指す。改名表は親のキーの行を子のキーの行より先に置き、
子の行の `old` は親を改めた後の位置で書く。旧いキーを探すときは各行の `old` を今の中身にそのまま当てるため、未移行の
中身では親の行だけが当たる。
"""

from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ndf import contracts
from ndf.contracts import facts, settings_renames

# 配列のすべての要素を指す参照トークン
EVERY = "*"


@dataclass(frozen=True)
class Rename:
    """改名表の 1 行。`values` は旧い値 → 新しい値（無ければ空）。"""

    id: str
    file: str
    old: str
    new: str
    values: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LegacyKey:
    """設定に実在する旧いキー。`old` と `new` は配列の添字を広げた位置。"""

    file: str
    old: str
    new: str


def relpath(file: str) -> str:
    """設定ファイルの根からの相対パス（表 ndf-files の location から表 locations の path を引き、ndf-files の path を継ぐ）。"""
    location = facts.value("ndf-files", file, "location")
    return f"{facts.value('locations', location, 'path')}/{facts.value('ndf-files', file, 'path')}"


def load() -> tuple[Rename, ...]:
    """改名表のうち、同じファイルの中でキーの名前と値を改める行を、表の並びのまま返す。"""
    data = contracts.load_as(settings_renames.TABLE, contracts.ContractError)
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise contracts.ContractError(f"{settings_renames.TABLE}: rows が配列でない")
    return tuple(
        Rename(r["id"], r["file"], r["old"], r["new"], dict(r["values"]) if "values" in r else {})
        for r in rows
        if settings_renames.renames_in_place(r)
    )


def tokens(pointer: str) -> list[str]:
    """JSON Pointer を参照トークンの並びに割る（`~1` → `/`・`~0` → `~` の順で戻す）。"""
    if not pointer:
        return []
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer.lstrip("/").split("/")]


def _pointer(parts: list[str]) -> str:
    return "/" + "/".join(p.replace("~", "~0").replace("/", "~1") for p in parts)


def _pointers(data: Any, parts: list[str], done: list[str]) -> Iterator[list[str]]:
    """`parts`（`*` を含む）の親のコンテナが実在する位置を、`*` を添字へ広げて返す。最後のトークンの実在は見ない。"""
    if len(parts) == 1:
        if isinstance(data, dict):
            yield [*done, parts[0]]
        return
    head, rest = parts[0], parts[1:]
    if head == EVERY:
        if isinstance(data, list):
            for i, item in enumerate(data):
                yield from _pointers(item, rest, [*done, str(i)])
        return
    if isinstance(data, dict) and head in data:
        yield from _pointers(data[head], rest, [*done, head])


def _parent(data: Any, parts: list[str]) -> Any:
    for p in parts[:-1]:
        data = data[int(p)] if isinstance(data, list) else data[p]
    return data


def _expanded(row: Rename, data: Any) -> Iterator[tuple[list[str], list[str]]]:
    """行の `old` が今の中身に実在する位置と、それに当たる `new` の位置（同じ添字）。"""
    new_last = tokens(row.new)[-1]
    for parts in _pointers(data, tokens(row.old), []):
        if parts[-1] in _parent(data, parts):
            yield parts, [*parts[:-1], new_last]


def legacy_keys(file: str, data: Mapping[str, Any]) -> list[LegacyKey]:
    """設定の今の中身に実在する旧いキー。表の並び・配列の添字の順に並ぶ。表に無い `file` なら空。"""
    found: list[LegacyKey] = []
    for row in load():
        if row.file == file:
            found += [LegacyKey(file, _pointer(old), _pointer(new)) for old, new in _expanded(row, data)]
    return found


def _rename_in_place(obj: dict[str, Any], old: str, new: str) -> None:
    """`obj` のキー `old` を、元の並びの位置のまま `new` へ改める（同じ辞書を書き換え、参照を保つ）。"""
    items = list(obj.items())
    obj.clear()
    obj.update(((new if k == old else k), v) for k, v in items)


def _apply_row(row: Rename, out: dict[str, Any], patch: list[dict[str, Any]]) -> None:
    for old, new in list(_expanded(row, out)):
        parent = _parent(out, old)
        if new[-1] in parent:
            continue  # 利用者が旧いキーと新しいキーの両方を書いた設定は、機械が選ばずに残す
        value = parent[old[-1]]
        patch += [
            {"op": "test", "path": _pointer(old), "value": copy.deepcopy(value)},
            {"op": "move", "from": _pointer(old), "path": _pointer(new)},
        ]
        _rename_in_place(parent, old[-1], new[-1])
        if isinstance(value, str) and value in row.values and row.values[value] != value:
            patch += [
                {"op": "test", "path": _pointer(new), "value": value},
                {"op": "replace", "path": _pointer(new), "value": row.values[value]},
            ]
            _parent(out, new)[new[-1]] = row.values[value]


def migrate(file: str, data: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """旧いキーを新しいキーへ、旧い値を新しい値へ変えた中身と、JSON Patch（RFC 6902）の操作の列。`data` は変えない。

    操作はキーの改名が `move`、値の対応が `replace` で、どちらの直前にも元の位置の今の値を持つ `test` を置く。
    表に無いキーと、`values` に無い値は変えない。
    """
    out = copy.deepcopy(dict(data))
    patch: list[dict[str, Any]] = []
    for row in load():
        if row.file == file:
            _apply_row(row, out, patch)
    return out, patch


def legacy_detail(file: str, data: Mapping[str, Any], root: Path) -> Optional[str]:
    """旧いキーが残れば、停止理由の文（ファイル・旧いキーの位置 → 新しいキーの位置・打つコマンド）。無ければ `None`。"""
    found = legacy_keys(file, data)
    if not found:
        return None
    pairs = "、".join(f"{k.old} → {k.new}" for k in found)
    return f"{relpath(file)} に旧いキーが残る: {pairs}。ndf settings migrate --root {root} で差分を示し、承認してから --apply で書き換える"
