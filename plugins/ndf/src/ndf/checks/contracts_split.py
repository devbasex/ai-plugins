"""規則 contracts-split: 分ける表の一覧に挙げた表が、1 項目 1 ファイルのままであることを確かめる（#3687 の受け入れ条件 2）。

一覧（`contracts.SPLIT_TABLES`）の表ごとに、次を違反にする。1 ファイル（表のディレクトリ・一覧を含む）につき 1 件。

- 表のディレクトリが無い
- 分ける前の 1 ファイル（`<表>.json`）が在る
- 項目のファイルが 2 つ以上の項目を持つ形か、項目の読み手（`contracts.load_split`）が断る形である

項目か項目の集まりかは、項目のスキーマ（`schemas/<表>.schema.json`）の欄の名前と、欄に書いた `type` で見分ける。読むのは
検査の対象のプラグインのファイルのうち一覧・項目のスキーマ・項目だけで、検査が集めたファイルがあればその本文を使う。
一覧を持たないプラグインには当てない（一覧が在るのに読めないときは違反にする）。
項目の値の形そのものは `ndf contracts check` がスキーマで確かめる。
"""

from __future__ import annotations

import os
import re
from collections.abc import Generator, Iterator
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
        raise contracts.ContractError(repr(exc)) from None
    if not named:
        raise contracts.ContractError("id（ディレクトリの名前 1 つ）か source_key の形が違う")
    return tables


@dataclass(frozen=True)
class _Files:
    """規則が読むファイル。検査が集めたもの（`sources`。パス → ファイル）は読み直さない。どちらもプラグインの中のものだけを読む。"""

    repo_root: Path
    plugin_root: Path
    sources: dict[Path, Source]

    def text(self, path: Path) -> str:
        """`path` の本文。プラグインの外を指す・ファイルでない・UTF-8 でなければ `ContractError`。"""
        contracts.require_inside(path, self.plugin_root)
        src = self.sources[path] if path in self.sources else None
        if src is None:
            if not path.is_file():
                raise contracts.ContractError("ファイルが無い")
            src = Source(path, path.relative_to(self.repo_root).as_posix())
        try:
            return src.text
        except UnicodeDecodeError as exc:
            raise contracts.ContractError(f"UTF-8 でない（位置 {exc.start}）") from None
        except OSError as exc:
            raise contracts.ContractError(f"{exc.strerror}") from None


def _refuses_object(field: Any) -> bool:
    """欄のスキーマが、そこに書いた `type` でオブジェクトを断るか。`type` の無い欄（参照・合成など）は断るとみなさない。"""
    if not isinstance(field, dict) or "type" not in field:
        return False
    types = field["type"]
    return "object" not in (types if isinstance(types, list) else [types])


def _object_fields(text: str) -> frozenset[str]:
    """項目のスキーマの本文 `text` の欄のうち、値がオブジェクトでありうるものの名前。スキーマとして読めなければ `ContractError`。

    欄の中身は評価しない（参照を解かず、合成の結果も見ない）。中身の誤りは `ndf contracts check` が報告する。
    """
    try:
        fields = contracts.parse_json(text)["properties"]
        return frozenset(name for name, field in fields.items() if not _refuses_object(field))
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise contracts.ContractError(repr(exc)) from None


def _holds_items(data: dict[str, Any], source_key: str, object_fields: frozenset[str]) -> bool:
    """1 項目でなく、項目の集まりを持つ形か。

    分ける前のキー（`source_key`）を持つか、オブジェクトでありうる欄（`object_fields`）でない名前に辞書（別の項目）を持つ。
    """
    return source_key in data or any(isinstance(v, dict) and k not in object_fields for k, v in data.items())


def _item_error(files: _Files, path: Path, source_key: str, object_fields: frozenset[str]) -> str | None:
    """項目のファイル 1 つの誤り。無ければ `None`。"""
    try:
        data = contracts.parse_json(files.text(path))
    except (ValueError, contracts.ContractError) as exc:
        return f"項目のファイルを読めない: {exc}"
    if not isinstance(data, dict):
        return f"項目がオブジェクトでない（{type(data).__name__}）"
    return "1 ファイルが 2 つ以上の項目を持つ形になっている" if _holds_items(data, source_key, object_fields) else None


@dataclass(frozen=True)
class _Table:
    """分けた表 1 つ。`root` は表のディレクトリ、`schema` は項目のスキーマのパス。"""

    root: Path
    schema: Path
    source_key: str
    files: _Files

    def errors(self) -> Iterator[tuple[Path, str]]:
        """違反のパスと中身。"""
        names = yield from self._directory_errors()
        if names is not None:
            yield from self._item_errors(names)

    def _directory_errors(self) -> Generator[tuple[Path, str], None, list[str] | None]:
        """ディレクトリの形の違反を出し、直下のエントリ名を返す。項目を読める形でなければ `None` を返す。"""
        # 列挙と読み取りの前に、親のディレクトリのリンクも含めて解決先を確かめる（外のエントリ名を指摘に載せない）
        try:
            contracts.require_inside(self.root, self.files.plugin_root)
        except contracts.ContractError as exc:
            yield self.root, str(exc)
            return None
        old = self.root.with_name(f"{self.root.name}.json")
        # リンク先の有無で結果を変えない（プラグインの外のファイルの有無を指摘から判別させない）
        if os.path.lexists(old):
            yield old, f"分ける前の 1 ファイルが在る（項目は {self.root.name}/ に 1 項目 1 ファイルで置く）"
        # リンクのディレクトリへは、検査がファイルを集めるときも降りない
        if self.root.is_symlink() or not self.root.is_dir():
            yield self.root, "分けた表のディレクトリが無い"
            return None
        names = sorted(p.name for p in self.root.iterdir())
        try:
            contracts.item_keys(names)
        except contracts.ContractError as exc:
            yield self.root, f"分けた表に{exc}"
        return names

    def _item_errors(self, names: list[str]) -> Iterator[tuple[Path, str]]:
        """エントリ名 `names` のうち項目のファイルの違反。項目のスキーマを読めなければ、その違反だけを出す。"""
        try:
            object_fields = _object_fields(self.files.text(self.schema))
        except contracts.ContractError as exc:
            yield self.schema, f"項目のスキーマを読めない: {exc}"
            return
        for name in names:
            path = self.root / name
            if name.startswith(contracts.META_PREFIX) or path.suffix != ".json":
                continue
            error = _item_error(self.files, path, self.source_key, object_fields)
            if error is not None:
                yield path, error


def check(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    """分けた表の形の違反を返す。分ける表の一覧を持たないプラグインでは空。"""
    top = scope.plugin_root / rules["facts"]["contracts_dir"]
    files = _Files(scope.repo_root, scope.plugin_root, {s.path: s for s in scope.sources})
    listed = top / contracts.SPLIT_TABLES
    errors: list[tuple[Path, str]] = []
    try:
        # 一覧の有無を見る前に、親のディレクトリのリンクの解決先を確かめる（外のファイルの有無で結果を変えない）
        contracts.require_inside(listed.parent, scope.plugin_root)
        # 読めない一覧（プラグインの外を指す・先の無いリンクなど）を、一覧の無いプラグインと取り違えない
        if not os.path.lexists(listed):
            return []
        for name, source_key in _tables(files.text(listed)).items():
            errors += _Table(top / name, top / contracts.schema_path(name), source_key, files).errors()
    except contracts.ContractError as exc:
        errors.append((listed, f"分ける表の一覧を読めない: {exc}"))
    return [Finding(RULE, scope.rel(path), 1, f"{rules[KEY]['summary']}: {detail}") for path, detail in errors]
