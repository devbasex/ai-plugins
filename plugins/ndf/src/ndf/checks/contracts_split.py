"""規則 contracts-split: 分ける表の一覧に挙げた表が、1 項目 1 ファイルのままであることを確かめる（#3687 の受け入れ条件 2）。

一覧（`contracts.SPLIT_TABLES`）の表ごとに、次を違反にする。1 ファイル（表のディレクトリ・一覧を含む）につき 1 件。

- 表のディレクトリが無い
- 分ける前の 1 ファイル（`<表>.json`）が在る
- 項目のファイルが 2 つ以上の項目を持つ形か、項目の読み手（`contracts.load_split`）が断る形である

項目か項目の集まりかは、項目のスキーマ（`schemas/<表>.schema.json`）がオブジェクトの値を認める欄の名前で見分ける。読むのは
検査の対象のプラグインのファイルだけで、一覧を持たないプラグインには当てない（一覧が在るのに読めないときは違反にする）。
項目の値の形そのものは `ndf contracts check` がスキーマで確かめる。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ndf import contracts
from ndf.checks.model import Finding, Scope, Source

# 規則のデータ（`rules.json`）のキー
KEY = __name__.rsplit(".", 1)[-1]
RULE = KEY.replace("_", "-")
# 表の名前の形（ディレクトリの名前 1 つ。コントラクトの保存先の外を指させない）
TABLE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
# 値がオブジェクトであることそのものを断りうる、スキーマのキーワード（`None` は何も認めないスキーマ `false`）
REFUSES_OBJECT = frozenset({"type", "enum", "const", "anyOf", "oneOf", None})
# 参照を書くスキーマのキーワード
REF_KEYWORDS = ("$ref", "$dynamicRef")


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


def _refs(node: Any) -> Iterator[str]:
    """スキーマの中の参照の値。"""
    values = node.values() if isinstance(node, dict) else node if isinstance(node, list) else ()
    if isinstance(node, dict):
        yield from (node[k] for k in REF_KEYWORDS if isinstance(node.get(k), str))
    for value in values:
        yield from _refs(value)


def _defined(schema: Any, ref: str) -> bool:
    """参照がスキーマの中の定義（`#` と JSON ポインター）を指すか。"""
    root, *parts = ref.split("/")
    node = schema
    for part in parts:
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return root == "#"


def _schema_validator(src: Source | None) -> Draft202012Validator:
    """項目のスキーマ `src` の検証器。スキーマとして読めなければ `ContractError`。"""
    if src is None:
        raise contracts.ContractError("項目のスキーマを読めない（無い・UTF-8 でない・リポジトリの外を指す）")
    try:
        schema = contracts.parse_json(src.text)
        Draft202012Validator.check_schema(schema)
        _ = schema["properties"].keys()
    except (ValueError, KeyError, TypeError, SchemaError) as exc:
        raise contracts.ContractError(f"項目のスキーマを読めない: {exc!r}") from None
    # 検証器はスキーマの外を指す参照をネットワークへ取りに行くので、渡す前に断る
    unknown = sorted({ref for ref in _refs(schema) if not _defined(schema, ref)})
    if unknown:
        raise contracts.ContractError(f"項目のスキーマの参照をこのファイルの中で解けない: {', '.join(unknown)}")
    return Draft202012Validator(schema)


def _is_field(name: str, value: dict[str, Any], validator: Draft202012Validator) -> bool:
    """辞書の値が、項目のスキーマがオブジェクトを認める欄か。欄のスキーマの参照・型の配列・合成は検証器が解く。

    欄の中身の誤り（必須の欄が無いなど）は見ない。
    """
    fields = validator.schema["properties"]
    if name not in fields:
        return False
    return not any(e.validator in REFUSES_OBJECT and not e.path for e in validator.descend(value, fields[name]))


def _holds_items(data: dict[str, Any], source_key: str, validator: Draft202012Validator) -> bool:
    """1 項目でなく、項目の集まりを持つ形か。

    分ける前のキー（`source_key`）を持つか、オブジェクトの欄（`_is_field`）でない名前に辞書（別の項目）を持つ。
    """
    return source_key in data or any(isinstance(v, dict) and not _is_field(k, v, validator) for k, v in data.items())


def _item_error(src: Source | None, source_key: str, validator: Draft202012Validator) -> str | None:
    """項目のファイル 1 つの誤り。無ければ `None`。`src` が `None` なら、検査が集めなかった（読めない）ファイル。"""
    if src is None:
        return "項目のファイルを読めない（UTF-8 でない・リポジトリの外を指す・ファイルでない）"
    try:
        data = contracts.parse_json(src.text)
    except (OSError, ValueError, contracts.ContractError) as exc:
        return f"項目のファイルを読めない: {exc}"
    if not isinstance(data, dict):
        return f"項目がオブジェクトでない（{type(data).__name__}）"
    return "1 ファイルが 2 つ以上の項目を持つ形になっている" if _holds_items(data, source_key, validator) else None


@dataclass(frozen=True)
class _Table:
    """分けた表 1 つ。`root` は表のディレクトリ、`schema` は項目のスキーマのパス、`sources` は検査が集めたファイル（パス → ファイル）。"""

    root: Path
    schema: Path
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
        try:
            validator = _schema_validator(self.sources.get(self.schema))
        except contracts.ContractError as exc:
            yield self.schema, str(exc)
            return
        for name in names:
            path = self.root / name
            if name.startswith(contracts.META_PREFIX) or path.suffix != ".json":
                continue
            error = _item_error(self.sources.get(path), self.source_key, validator)
            if error is not None:
                yield path, error


def check(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    """分けた表の形の違反を返す。分ける表の一覧を持たないプラグインでは空。"""
    top = scope.plugin_root / rules["facts"]["contracts_dir"]
    sources = {s.path: s for s in scope.sources}
    listed = top / contracts.SPLIT_TABLES
    # 検査が集めない一覧（UTF-8 でない・リポジトリの外を指す）を、一覧の無いプラグインと取り違えない
    if listed not in sources and not (listed.is_symlink() or listed.exists()):
        return []
    errors: list[tuple[Path, str]] = []
    try:
        if listed not in sources:
            raise contracts.ContractError("分ける表の一覧を読めない（UTF-8 でない・リポジトリの外を指す・ファイルでない）")
        for name, source_key in _tables(sources[listed].text).items():
            errors += _Table(top / name, top / contracts.schema_path(name), source_key, sources).errors()
    except contracts.ContractError as exc:
        errors.append((listed, str(exc)))
    return [Finding(RULE, scope.rel(path), 1, f"{rules[KEY]['summary']}: {detail}") for path, detail in errors]
