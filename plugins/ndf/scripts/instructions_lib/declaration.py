"""リポジトリ側の宣言（`.ndf/instructions.json`）の読み込みと検査（`instructions-check.py` から分けた。#1142 の C7）。
"""
from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import refresh as refresh_lib

from instructions_lib.model import CheckError


# 宣言の既定値。**配布物は閾値を持たない** ── ランタイムごとに助言の値が違い、版で動く。
# 落とすのは利用者が宣言に書いたときだけである。
DEFAULT_FILES = ["AGENTS.md", "CLAUDE.md", "KIRO.md"]
# 即時読み込みの記法を解釈する指示書。Codex / Kiro はただの文字列として扱うため、
# 解釈しない指示書へこの判定を掛けると、書いてよい文字列で落ちる。
DEFAULT_IMPORT_SYNTAX = ["CLAUDE.md"]
# たどる深さの上限。根拠は Claude Code のドキュメント（memory）が最大 4 階層と書いていること。
DEFAULT_IMPORT_DEPTH = 4
DEFAULT_REVIEW_INTERVAL_DAYS = 90
SUPPORTED_DECLARATION_VERSIONS = (1,)
DECLARATION_RELATIVE_PATH = ".ndf/instructions.json"


@dataclass
class Declaration:
    """リポジトリ側の宣言。**無くても作る**（既定値を持ち、`None` の判定は動かない）。"""

    present: bool = False
    files: list[str] = field(default_factory=lambda: list(DEFAULT_FILES))
    imports: dict | None = None
    released: dict | None = None
    decisions: str | None = None
    pending_marker: str | None = None
    budget: dict | None = None
    scopes: dict = field(default_factory=dict)
    import_syntax: list[str] = field(default_factory=lambda: list(DEFAULT_IMPORT_SYNTAX))
    import_depth: int = DEFAULT_IMPORT_DEPTH
    refresh_timeout_seconds: int = refresh_lib.DEFAULT_TIMEOUT_SECONDS
    review_interval_days: int = DEFAULT_REVIEW_INTERVAL_DAYS
    reviewed_at: str | None = None

    @staticmethod
    def load(root: Path) -> "Declaration":
        path = root / DECLARATION_RELATIVE_PATH
        if not path.is_file():
            return Declaration()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CheckError(f"{DECLARATION_RELATIVE_PATH} を読めない: {exc}") from exc
        if not isinstance(raw, dict):
            raise CheckError(f"{DECLARATION_RELATIVE_PATH} の中身がオブジェクトではない")
        return _declaration_from(raw)


def _declaration_from(raw: dict) -> Declaration:
    version = raw.get("version")
    if version not in SUPPORTED_DECLARATION_VERSIONS:
        raise CheckError(f"宣言の version が無いか未対応である: {version!r}")

    decl = _populate_declaration(raw)
    _apply_field_constraints(raw, decl)
    if decl.released is not None:
        _validate_released(decl.released)
    if decl.imports is not None:
        _validate_imports(decl.imports, "imports")
    _validate_scopes(decl.scopes)
    return decl


def _populate_declaration(raw: dict) -> Declaration:
    decl = Declaration(present=True)
    decl.files = _typed(raw, "files", list, decl.files)
    if any(not isinstance(name, str) for name in decl.files):
        raise CheckError("宣言の files は文字列の配列である")
    decl.imports = _typed(raw, "imports", dict, None)
    decl.released = _typed(raw, "released", dict, None)
    decl.decisions = _typed(raw, "decisions", str, None)
    decl.budget = _typed(raw, "budget", dict, None)
    decl.scopes = _typed(raw, "scopes", dict, {})
    decl.import_syntax = _typed(raw, "import_syntax", list, decl.import_syntax)
    if any(not isinstance(name, str) for name in decl.import_syntax):
        raise CheckError("宣言の import_syntax は文字列の配列である")
    decl.reviewed_at = _typed(raw, "reviewed_at", str, None)
    return decl


def _apply_field_constraints(raw: dict, decl: Declaration) -> None:
    if "pending_marker" in raw:
        marker = _typed(raw, "pending_marker", str, None)
        # **空文字列はすべての段落へ当たり、出た版の判定が働かなくなる。**
        if marker == "":
            raise CheckError("宣言の pending_marker が空文字列である")
        decl.pending_marker = marker

    decl.import_depth = _positive(raw, "import_depth", decl.import_depth)
    decl.refresh_timeout_seconds = _positive(
        raw, "refresh_timeout_seconds", decl.refresh_timeout_seconds)
    decl.review_interval_days = _positive(
        raw, "review_interval_days", decl.review_interval_days)

    if decl.budget is not None and "bytes" in decl.budget:
        decl.budget["bytes"] = _positive(decl.budget, "bytes", 0)
    if decl.reviewed_at is not None:
        _parse_declared_date(decl.reviewed_at, "reviewed_at")


def _validate_scopes(scopes: dict) -> None:
    for name in ("user", "plugins"):
        entries = scopes.get(name)
        if entries is None:
            continue
        if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
            raise CheckError(f"宣言の scopes.{name} はオブジェクトの配列である")
        for index, entry in enumerate(entries):
            allow = entry.get("imports")
            if allow is not None:
                _validate_imports(allow, f"scopes.{name}[{index}].imports")


def _validate_imports(allow, label: str) -> None:
    """許可の形を `{指示書: {参照先: 理由}}` まで見る。

    **読み取りの時点で止める。** 途中まで判定してから型の誤りで落ちると、宣言の不正が
    終了コード 2 ではなく未捕捉の例外として現れる。
    """
    if not isinstance(allow, dict):
        raise CheckError(f"宣言の {label} はオブジェクトである")
    for key, entries in allow.items():
        if not isinstance(entries, dict):
            raise CheckError(f"宣言の {label}.{key} は "
                             "{参照先: 理由} のオブジェクトである")
        for name, reason in entries.items():
            if not isinstance(reason, str):
                raise CheckError(f"宣言の {label}.{key}.{name} の理由は文字列である")


def _typed(raw: dict, key: str, kind: type, fallback):
    if key not in raw or raw[key] is None:
        return fallback
    value = raw[key]
    if not isinstance(value, kind) or isinstance(value, bool):
        raise CheckError(f"宣言の {key} の型が違う（{kind.__name__} を期待した）")
    return value


def _positive(raw: dict, key: str, fallback: int) -> int:
    if key not in raw or raw[key] is None:
        return fallback
    value = raw[key]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        # 0 や負の値を「制限なし」と読むか「すべて落とす」と読むかは実装で分かれる。
        raise CheckError(f"宣言の {key} は 1 以上の整数である: {value!r}")
    return value


def _parse_declared_date(value: str, label: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise CheckError(f"{label} が日付として読めない: {value!r}") from exc


def _validate_released(released: dict) -> None:
    source = released.get("source")
    if source not in ("changelog", "tags"):
        raise CheckError(f"released.source は changelog か tags である: {source!r}")
    pattern = released.get("pattern")
    if not isinstance(pattern, str):
        raise CheckError("released.pattern が無いか文字列ではない")
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise CheckError(f"released.pattern が正規表現として読めない: {exc}") from exc
    if "version" not in (compiled.groupindex or {}):
        raise CheckError("released.pattern に名前付きの捕捉 (?P<version>…) が無い")
    if source == "changelog" and not isinstance(released.get("path"), str):
        raise CheckError("released.path が無いか文字列ではない")
