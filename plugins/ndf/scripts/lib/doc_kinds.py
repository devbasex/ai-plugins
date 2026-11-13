"""設計文書の種類の契約（`contracts/design-docs.json`）の唯一の読み手（#3144 の決定 12）。

設計文書の置き場と、種類ごとの名前の末尾・行数の上限を見るか（`line_limit`）・用語チェックの既定の
対象か（`glossary`）・設計 Pull Request の文書か（`review`）・雛形のパスを、この契約の 1 か所だけが持つ。
読む側（`glossary.py`・`cross-review` の `classifications.py`・`categories.py`・`design_detail.py`）は
名前の末尾を定数に写さず、ここから引く。

読めない契約は `DocKindsError` で止める。既定値で続けると、設計文書の一部が行数の検査や分類から
黙って抜ける。標準ライブラリだけを使い、ほかの部品を読まない。
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Optional

# 置き場は `limits.DECLARATION` と同じ探し方: リリースしたプラグインでは `contracts/`、
# ラッパーの複製（バージョンディレクトリ）では `lib/` と並ぶ `contracts/`
_LIB_DIR = Path(__file__).resolve().parent
DECLARATION = next(
    (
        p
        for p in (_LIB_DIR.parents[1] / "contracts" / "design-docs.json", _LIB_DIR.parent / "contracts" / "design-docs.json")
        if p.is_file()
    ),
    _LIB_DIR.parents[1] / "contracts" / "design-docs.json",
)
VERSION = 1
_FLAGS = ("line_limit", "glossary", "review")


class DocKindsError(Exception):
    """設計文書の種類の契約が無い・壊れている・引いた名前が無い。"""


_path = DECLARATION


def use_contract(path) -> None:
    """読み込み先を差し替える（テストが使う）。`None` でプラグインの契約へ戻す。読んだ表は置き場ごとに控える。"""
    global _path
    _path = DECLARATION if path is None else Path(path)


def _validate(raw, where: str) -> tuple[str, tuple[dict, ...]]:
    if not isinstance(raw, dict) or raw.get("version") != VERSION:
        raise DocKindsError(f"設計文書の種類の契約 {where} の形が違う（version {VERSION} が要る）")
    place, kinds = raw.get("dir"), raw.get("kinds")
    if not isinstance(place, str) or not place.strip() or not isinstance(kinds, list) or not kinds:
        raise DocKindsError(f"設計文書の種類の契約 {where} に dir と空でない kinds が要る")
    seen: set[str] = set()
    for row in kinds:
        if not isinstance(row, dict) or not isinstance(row.get("kind"), str) or not isinstance(row.get("suffix"), str):
            raise DocKindsError(f"設計文書の種類の契約 {where} の行に kind と suffix が要る: {row!r}")
        if row["kind"] in seen:
            raise DocKindsError(f"設計文書の種類の契約 {where} に同じ種類が 2 つある: {row['kind']}")
        seen.add(row["kind"])
        for flag in _FLAGS:
            if not isinstance(row.get(flag), bool):
                raise DocKindsError(f"設計文書の種類の契約 {where} の {row['kind']} の {flag} は真偽の値: {row.get(flag)!r}")
        if "template" in row and not isinstance(row["template"], str):
            raise DocKindsError(f"設計文書の種類の契約 {where} の {row['kind']} の template は文字列")
    return place.strip("/"), tuple(kinds)


@functools.lru_cache(maxsize=4)
def _read_contract(path: Path) -> tuple[str, tuple[dict, ...]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DocKindsError(f"設計文書の種類の契約 {path} を読めない: {exc}") from None
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise DocKindsError(f"設計文書の種類の契約 {path} が JSON として壊れている: {exc}") from None
    return _validate(raw, str(path))


def _contract() -> tuple[str, tuple[dict, ...]]:
    """契約を置き場ごとに 1 プロセスに 1 回読み、置き場と種類の行（契約の並びのまま）を返す。"""
    return _read_contract(_path)


def suffixes(flag: str) -> tuple[str, ...]:
    """真偽の名前（`line_limit`・`glossary`・`review`）が真の種類の名前の末尾を、契約の並びで返す。"""
    if flag not in _FLAGS:
        raise DocKindsError(f"設計文書の種類の契約に真偽の名前 {flag} は無い（{', '.join(_FLAGS)}）")
    return tuple(row["suffix"] for row in _contract()[1] if row[flag])


def patterns(flag: str) -> list[str]:
    """`suffixes(flag)` を置き場と合わせたパスの形（`issues/*-design.md` など）で返す。"""
    place = _contract()[0]
    return [f"{place}/*{suffix}" for suffix in suffixes(flag)]


def doc_kind(rel: str, placed: bool = True) -> Optional[str]:
    """パスの名前の末尾から設計文書の種類を返す。最も長く一致する末尾の種類を選ぶ。

    `placed` が真なら、親のディレクトリが置き場でないパスは `None` を返す。
    """
    place, kinds = _contract()
    path = str(rel).replace("\\", "/")
    parent, _, name = path.rpartition("/")
    if placed and parent.strip("/").removeprefix("./") != place:
        return None
    hits = [row for row in kinds if name.endswith(row["suffix"])]
    if not hits:
        return None
    return max(hits, key=lambda row: len(row["suffix"]))["kind"]


def template_path(kind: str) -> Path:
    """種類の行の雛形のパスを、プラグインの根からの絶対パスで返す（在るかは見ない）。"""
    for row in _contract()[1]:
        if row["kind"] == kind:
            if not row.get("template"):
                raise DocKindsError(f"設計文書の種類 {kind} は雛形を持たない")
            return _path.parent.parent / row["template"]
    raise DocKindsError(f"設計文書の種類の契約に種類 {kind} は無い")
