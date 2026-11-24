"""規則 experimental: 既定で動くものから実験版の保存先への参照を違反にする。

実験版はプラグインの根（取得元が配るディレクトリ）の外の保存先（保存先の表 `locations` の項目 `experimental`）に置き、`ndf` のパッケージに
入らない。そのため import の形は見ず、プラグインの根の下の見るファイル（検査が集めた文字のファイル）の本文に保存先のパスが
現れることを参照とみなす。
利用者向けの説明（`README.md`・`CHANGELOG.md`・`docs/`）は実験版を紹介してよく、保存先のパスを持つのが役目のもの
（`EXEMPT_FILES` と `EXEMPT_VALUES`）は数えない。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ndf import contracts
from ndf.checks.model import CheckError, Finding, Scope, Source
from ndf.contracts import facts

# 規則の名前と、保存先の表の実験版の項目の id は、どちらもこのモジュールの名前（表の id を文字列リテラルで書かない）
RULE = ROW_ID = __name__.rsplit(".", 1)[-1]
# 保存先の表の名前
TABLE = "locations"
# 利用者向けの説明（プラグインの根からの相対）。実験版を紹介してよい
DOC_FILES = frozenset({"README.md", "CHANGELOG.md"})
DOC_DIR = "docs/"
# 規則の実装とそのテスト（プラグインの根からの相対）。保存先のパスを持つのが役目
EXEMPT_FILES = ("src/ndf/checks/experimental.py", "tests/checks/test_experimental.py")


def table_rel() -> str:
    """保存先の表のファイル（プラグインの根からの相対）。"""
    return f"{contracts.contracts_dir().name}/{facts.location(TABLE)}"


# JSON のファイル（プラグインの根からの相対）→ 数えない値のキーの道筋。配列の要素は `id` を持てばその値で指す
EXEMPT_VALUES: dict[str, tuple[str, ...]] = {
    table_rel(): ("rows", ROW_ID, "path"),
    "src/ndf/checks/rules.json": (RULE,),
}


def _needle(sources: list[Source], table: Path) -> str | None:
    """保存先の表の、実験版の項目の保存先。表かその項目が無ければ `None`、表が読めなければ `CheckError`。"""
    src = next((s for s in sources if s.path == table), None)
    if src is None:
        return None
    try:
        rows = json.loads(src.text)["rows"]
        found = [r["path"] for r in rows if r["id"] == ROW_ID]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CheckError(f"保存先の表を読めない: {table}: {exc}") from exc
    return found[0] if found else None


def _strings(value: Any, route: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], str]]:
    """JSON の値の中の文字列（キーを含む）と、そこまでのキーの道筋。"""
    if isinstance(value, dict):
        for key, item in value.items():
            inner = (*route, key)
            yield inner, key
            yield from _strings(item, inner)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            step = item["id"] if isinstance(item, dict) and isinstance(item.get("id"), str) else str(i)
            yield from _strings(item, (*route, step))
    elif isinstance(value, str):
        yield route, value


def _json_lines(path: Path, text: str, needle: str, exempt: tuple[str, ...]) -> list[int]:
    """数えない値の道筋（`exempt` の下）を除いて、保存先のパスを持つ文字列の行。"""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CheckError(f"JSON を読めない: {path}: {exc}") from exc
    hits = {s for route, s in _strings(data) if needle in s and route[: len(exempt)] != exempt}
    return sorted({text[: max(text.find(json.dumps(s, ensure_ascii=False)[1:-1]), 0)].count("\n") + 1 for s in hits})


def _lines(src: Source, rel: str, needle: str) -> list[int]:
    """保存先のパスが現れる行。"""
    text = src.text
    if needle not in text:
        return []
    if rel in EXEMPT_VALUES:
        return _json_lines(src.path, text, needle, EXEMPT_VALUES[rel])
    return [n for n, line in enumerate(text.splitlines(), 1) if needle in line]


def _allowed(rel: str) -> bool:
    return rel in DOC_FILES or rel.startswith(DOC_DIR) or rel in EXEMPT_FILES


def check(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    """既定で動くもの（プラグインの根の下の見るファイル）から実験版の保存先への参照を違反にする。

    ファイルの列挙と本文は検査が集めた `scope.sources` を使い、ここで列挙し直さない（リポジトリの外を指すリンクは
    集める時点で除かれている）。
    """
    sources = scope.sources
    needle = _needle(sources, scope.plugin_root / table_rel())
    if needle is None:
        return []
    found = []
    for src in sources:
        rel = scope.plugin_rel(src)
        if _allowed(rel):
            continue
        found += [Finding(RULE, src.rel, n, rules[RULE]["summary"]) for n in _lines(src, rel, needle)]
    return found
