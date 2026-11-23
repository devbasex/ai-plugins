"""コントラクトの表（`contracts/`）の保存先と、JSON とスキーマの読み込み。

コントラクトの表はプラグインのコードと一緒に配るため、保存先はプラグインの根（`identity.ROOT`。ファイルの位置から求める
1 通り）の下に置く（環境変数を読まない。ほかのプラグインの `CLAUDE_PLUGIN_ROOT` が残っていても、そのプラグインの表を読まない）。
表ごとの読み手（`facts`・`causes`・`limits`・`result`・`chosen`・`version`）はここを通して読み、1 プロセスに 1 回だけ読む。
D1 を閉じた後のコントラクトの変更は #3284 の追補（D1+）の PR で出す（オーナーは `contracts/version.json` の `owner`）。
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from functools import cache
from pathlib import Path
from typing import Any, TypeVar

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ndf.identity import ROOT

DEFAULT_DIR = ROOT / "contracts"
SCHEMAS = "schemas"
# 分けた表のディレクトリで、この文字で始まるファイルは項目でなく表そのものの値（`_meta.json` など）
META_PREFIX = "_"
META = "_meta.json"
# 分ける表の一覧（事実の表。表の名前 → 項目の単位とキーの出所）
SPLIT_TABLES = "domains/split-tables.json"
T = TypeVar("T")

_dir = DEFAULT_DIR
# 保存先を差し替えたときに捨てる、読み手ごとのキャッシュ
_clearers: list[Callable[[], None]] = []


class ContractError(Exception):
    """コントラクトのファイルが無い・JSON でない・形が違う・引いた値が無い。"""


def contracts_dir() -> Path:
    return _dir


def use_dir(path: str | Path | None) -> None:
    """コントラクトの保存先を差し替え、読んだ表をすべて捨てる（テストが使う）。`None` でプラグインの保存先へ戻す。"""
    global _dir
    _dir = Path(path) if path is not None else DEFAULT_DIR
    for clear in _clearers:
        clear()


def cached_reader(fn: Callable[..., T]) -> Callable[..., T]:
    """`fn` を 1 プロセスに 1 回だけ読む関数にし、`use_dir` で保存先を差し替えたときに読み直させる。"""
    cached = cache(fn)
    _clearers.append(cached.cache_clear)
    return cached


def duplicates(values: Iterable[Any]) -> list:
    """2 回以上現れる値（昇順）。"""
    return sorted(v for v, n in Counter(values).items() if n > 1)


def _refuse_duplicate_keys(pairs: list) -> dict:
    dup = duplicates(k for k, _ in pairs)
    if dup:
        raise ContractError(f"同じキーが 2 つある: {', '.join(dup)}")
    return dict(pairs)


def parse_json(text: str) -> Any:
    """JSON の本文の値。JSON でなければ `ValueError`、同じキーが 2 つあれば `ContractError`（後の値で黙って上書きしない）。"""
    return json.loads(text, object_pairs_hook=_refuse_duplicate_keys)


def load_json(rel: str) -> Any:
    """`contracts/<rel>` を読む。同じキーが 2 つあれば断る（後の値で黙って上書きしない）。"""
    path = _dir / rel
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"コントラクト {path} を読めない: {exc.strerror or exc}") from None
    try:
        return parse_json(text)
    except ContractError as exc:
        raise ContractError(f"コントラクト {path}: {exc}") from None
    except ValueError as exc:
        raise ContractError(f"コントラクト {path} が JSON として壊れている: {exc}") from None


def item_keys(names: Iterable[str]) -> list[str]:
    """分けた表のディレクトリのファイル名の集まりから、項目のキー（拡張子 `.json` を除いたもの。ファイル名の昇順）。

    `META_PREFIX` で始まるファイルは項目でないので読まない。ほかのファイルは拡張子が小文字の `.json` でなければ断る
    （黙って飛ばすと、書いたつもりの項目が表から消える）。大小文字だけが違うキーは、大小文字を区別しないファイルシステムで
    1 つに潰れるため重なりとして断る。
    """
    paths = sorted(Path(n) for n in names if not n.startswith(META_PREFIX))
    odd = [p.name for p in paths if p.suffix != ".json"]
    if odd:
        raise ContractError(f"拡張子が .json でないファイルがある: {', '.join(odd)}")
    keys = [p.stem for p in paths]
    folded = set(duplicates(k.casefold() for k in keys))
    if folded:
        clash = [k for k in keys if k.casefold() in folded]
        raise ContractError(f"大小文字だけが違うキーがある: {', '.join(clash)}")
    return keys


def _list_keys(rel: str) -> list[str]:
    """`contracts/<rel>/` の項目のキー（`item_keys`）。中身は読まない。"""
    root = _dir / rel
    if not root.is_dir():
        raise ContractError(f"分けた表のディレクトリ {root} が無い")
    try:
        return item_keys(p.name for p in root.iterdir())
    except ContractError as exc:
        raise ContractError(f"分けた表 {root} に{exc}") from None


@cached_reader
def _load_item(rel: str, key: str) -> dict[str, Any]:
    """項目 1 つの中身。全件（`load_split`）と 1 項目（`load_item`）のどちらが先でも、ファイルは 1 プロセスに 1 回だけ読む。"""
    item = load_json(f"{rel}/{key}.json")
    if not isinstance(item, dict):
        raise ContractError(f"分けた表の項目 {_dir / rel / key}.json がオブジェクトでない（{type(item).__name__}）")
    return item


def load_split(rel: str) -> dict[str, Any]:
    """分けた表 `contracts/<rel>/` の、キー → 中身の辞書（ファイル名の昇順）。すべての項目のファイルを読む。"""
    return {key: _load_item(rel, key) for key in _list_keys(rel)}


@cached_reader
def split_keys(rel: str) -> frozenset[str]:
    """分けた表 `contracts/<rel>/` のキーの集合。ディレクトリの一覧を 1 プロセスに 1 回取るだけで、項目のファイルは読まない。"""
    return frozenset(_list_keys(rel))


def load_item(rel: str, key: str) -> dict[str, Any] | None:
    """分けた表 `contracts/<rel>/` の項目 1 つの中身。そのファイルだけを読む。項目が無ければ None。"""
    return _load_item(rel, key) if key in split_keys(rel) else None


def load_table(rel: str, error_cls: type[ContractError], label: str) -> dict[str, Any]:
    """分けた表 `rel` の `_meta.json` の形を確かめ、項目（`load_split`）を返す。項目が 1 つも無い表も `error_cls` で断る。"""
    with raised_as(error_cls):
        meta = load_json(f"{rel}/{META}")
        require_schema(meta, schema_path("split-meta"), error_cls, f"{label} {rel}/{META}")
        items = load_split(rel)
    if not items:
        raise error_cls(f"{label} {rel} に項目が 1 つも無い")
    return items


def split_tables() -> dict[str, dict[str, str]]:
    """分ける表の一覧（`contracts/domains/split-tables.json`）の、表の名前 → 項目の単位・キーの出所・もとのキー名の列。

    事実の表の読み手（`ndf.contracts.facts`）はこのモジュールを import するため、ここでは JSON として読むだけにする
    （import の輪を作らない）。表の形は事実の表として `ndf check` の `fact-table` と `facts.table` が確かめる。
    """
    return {r["id"]: {c: v for c, v in r.items() if c != "id"} for r in load_json(SPLIT_TABLES)["rows"]}


@contextmanager
def raised_as(error_cls: type[ContractError]) -> Iterator[None]:
    """中で起きた `ContractError` を読み手の例外 `error_cls` へ包み直す（`error_cls` はそのまま通す）。"""
    try:
        yield
    except error_cls:
        raise
    except ContractError as exc:
        raise error_cls(str(exc)) from None


def load_as(rel: str, error_cls: type[ContractError]) -> Any:
    """`load_json` の誤りを読み手の例外 `error_cls` で返す。"""
    with raised_as(error_cls):
        return load_json(rel)


@cached_reader
def _schema_validator(rel: str) -> Draft202012Validator:
    """`rel` の検証器。`<ファイル>#/$defs/<名前>` の形なら、そのファイルの `$defs` の 1 つで確かめる。"""
    path, _, pointer = rel.partition("#")
    schema = load_json(path)
    if pointer:
        if "$defs" not in schema:
            raise ContractError(f"スキーマ {path} に $defs が無い（{rel} を引けない）")
        schema = {"$schema": schema.get("$schema"), "$defs": schema["$defs"], "$ref": f"#{pointer}"}
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ContractError(f"スキーマ {_dir / rel} がスキーマとして誤り: {exc.message}") from None
    return Draft202012Validator(schema)


def schema_errors(instance: Any, rel: str) -> list[str]:
    """`instance` を `contracts/<rel>` のスキーマで確かめ、誤りを `<場所>: <理由>` の並びで返す。空なら合う。"""
    validator = _schema_validator(rel)
    found = sorted(("/".join(str(p) for p in e.absolute_path) or "(最上位)", e.message) for e in validator.iter_errors(instance))
    return [f"{where}: {message}" for where, message in found]


def require_schema(instance: Any, rel: str, error_cls: type[ContractError], label: str) -> None:
    """`instance` が `contracts/<rel>` のスキーマに合わなければ `error_cls`（`<label> の形が違う: <誤り>`）を投げる。"""
    errs = schema_errors(instance, rel)
    if errs:
        raise error_cls(f"{label} の形が違う: {'; '.join(errs)}")


def schema_path(name: str) -> str:
    """`contracts/schemas/<name>.schema.json` の `contracts/` からの相対パス。"""
    return f"{SCHEMAS}/{name}.schema.json"
