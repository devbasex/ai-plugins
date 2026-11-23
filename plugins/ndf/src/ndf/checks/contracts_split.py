"""規則 contracts-split: 分ける表の一覧に挙げた表が、1 項目 1 ファイルのままであることを確かめる（#3687 の受け入れ条件 2）。

一覧（`contracts.SPLIT_TABLES`）の表ごとに、次を違反にする。1 ファイル（表のディレクトリ・一覧を含む）につき 1 件。

- 表のディレクトリが無い
- 分ける前の 1 ファイル（`<表>.json`）が在る
- 項目のファイルが 2 つ以上の項目を持つ形か、項目の読み手（`contracts.load_split`）が断る形である

読むのは検査の対象のプラグインのファイルだけで、一覧を持たないプラグインには当てない。項目の値の形は表ごとのスキーマ
（`ndf contracts check`）が確かめる。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ndf import contracts
from ndf.checks.model import Finding, Scope, Source

# 規則のデータ（`rules.json`）のキー
KEY = __name__.rsplit(".", 1)[-1]
RULE = KEY.replace("_", "-")
# 表の名前の形（ディレクトリの名前 1 つ。コントラクトの保存先の外を指させない）
TABLE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def _tables(text: str) -> dict[str, str]:
    """分ける表の一覧の、表の名前 → 分ける前に項目を並べていたキー。一覧として読めなければ `ContractError`。"""
    try:
        tables = {r["id"]: r["source_key"] for r in contracts.parse_json(text)["rows"]}
        named = all(TABLE_NAME.fullmatch(t) and isinstance(k, str) for t, k in tables.items())
    except (ValueError, KeyError, TypeError) as exc:
        raise contracts.ContractError(f"分ける表の一覧を読めない: {exc!r}") from None
    if not named:
        raise contracts.ContractError("分ける表の一覧の id（ディレクトリの名前 1 つ）か source_key の形が違う")
    return tables


def _holds_items(data: dict[str, Any], source_key: str) -> bool:
    """1 項目でなく、項目の集まりを持つ形か。

    分ける前のキー（`source_key`）を持つ・値がすべて辞書（キー → 項目）・自分と同じ名前の欄を持つ辞書を中に持つ、のどれか。
    """
    nested = [v for v in data.values() if isinstance(v, dict)]
    return source_key in data or len(nested) == len(data) > 0 or any(v.keys() & data.keys() for v in nested)


def _item_error(src: Source | None, source_key: str) -> str | None:
    """項目のファイル 1 つの誤り。無ければ `None`。`src` が `None` なら、検査が集めなかった（読めない）ファイル。"""
    if src is None:
        return "項目のファイルを読めない（UTF-8 でない・リポジトリの外を指す・ファイルでない）"
    try:
        data = contracts.parse_json(src.text)
    except (OSError, ValueError, contracts.ContractError) as exc:
        return f"項目のファイルを読めない: {exc}"
    if not isinstance(data, dict):
        return f"項目がオブジェクトでない（{type(data).__name__}）"
    return "1 ファイルが 2 つ以上の項目を持つ形になっている" if _holds_items(data, source_key) else None


@dataclass(frozen=True)
class _Table:
    """分けた表 1 つ。`root` は表のディレクトリ、`sources` は検査が集めたファイル（パス → ファイル）。"""

    root: Path
    source_key: str
    sources: dict[Path, Source]

    def errors(self) -> Iterator[tuple[Path, str]]:
        """違反のパスと中身。"""
        old = self.root.with_name(f"{self.root.name}.json")
        if old.exists():
            yield old, f"分ける前の 1 ファイルが在る（項目は {self.root.name}/ に 1 項目 1 ファイルで置く）"
        # リンクの先はリポジトリの外でありうるので、一覧も取らない（検査がファイルを集めるときもリンクのディレクトリへ降りない）
        if self.root.is_symlink() or not self.root.is_dir():
            yield self.root, "分けた表のディレクトリが無い"
            return
        names = sorted(p.name for p in self.root.iterdir())
        try:
            contracts.item_keys(names)
        except contracts.ContractError as exc:
            yield self.root, f"分けた表に{exc}"
        for name in names:
            path = self.root / name
            if name.startswith(contracts.META_PREFIX) or path.suffix != ".json":
                continue
            error = _item_error(self.sources[path] if path in self.sources else None, self.source_key)
            if error is not None:
                yield path, error


def check(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    """分けた表の形の違反を返す。分ける表の一覧を持たないプラグインでは空。"""
    top = scope.plugin_root / rules["facts"]["contracts_dir"]
    sources = {s.path: s for s in scope.sources}
    listed = top / contracts.SPLIT_TABLES
    if listed not in sources:
        return []
    errors: list[tuple[Path, str]] = []
    try:
        for name, source_key in _tables(sources[listed].text).items():
            errors += _Table(top / name, source_key, sources).errors()
    except contracts.ContractError as exc:
        errors.append((listed, str(exc)))
    return [Finding(RULE, scope.rel(path), 1, f"{rules[KEY]['summary']}: {detail}") for path, detail in errors]
