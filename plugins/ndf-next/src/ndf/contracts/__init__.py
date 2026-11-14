"""契約の表（`contracts/`）の置き場と、JSON とスキーマの読み込み。

契約の表はプラグインのコードと一緒に配るため、置き場はこのファイルの位置だけから求める（環境変数を読まない。
ほかのプラグインの `CLAUDE_PLUGIN_ROOT` が残っていても、そのプラグインの表を読まない）。
表ごとの読み手（`facts`・`causes`・`limits`・`result`・`chosen`・`version`）はここを通して読み、1 プロセスに 1 回だけ読む。
D1 を閉じた後の契約の変更は #3284 の追補（D1+）の PR で出す（持ち主は `contracts/version.json` の `owner`）。
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

DEFAULT_DIR = Path(__file__).resolve().parents[3] / "contracts"
SCHEMAS = "schemas"
T = TypeVar("T")

_dir = DEFAULT_DIR
# 置き場を差し替えたときに捨てる、読み手ごとのキャッシュ
_clearers: list = []


class ContractError(Exception):
    """契約のファイルが無い・JSON でない・形が違う・引いた値が無い。"""


def contracts_dir() -> Path:
    return _dir


def use_dir(path: str | Path | None) -> None:
    """契約の置き場を差し替え、読んだ表をすべて捨てる（テストが使う）。`None` でプラグインの置き場へ戻す。"""
    global _dir
    _dir = Path(path) if path is not None else DEFAULT_DIR
    _schema_validator.cache_clear()
    for clear in _clearers:
        clear()


def _on_reset(clear) -> None:
    """`use_dir` のときに呼ぶキャッシュの消し方を登録する。"""
    _clearers.append(clear)


def cached_reader(fn: Callable[..., T]) -> Callable[..., T]:
    """`fn` を 1 プロセスに 1 回だけ読む関数にし、`use_dir` で置き場を差し替えたときに読み直させる。"""
    cached = cache(fn)
    _on_reset(cached.cache_clear)
    return cached


def duplicates(values: Iterable[Any]) -> list:
    """2 回以上現れる値（昇順）。"""
    return sorted(v for v, n in Counter(values).items() if n > 1)


def _refuse_duplicate_keys(pairs: list) -> dict:
    dup = duplicates(k for k, _ in pairs)
    if dup:
        raise ContractError(f"同じキーが 2 つある: {', '.join(dup)}")
    return dict(pairs)


def load_json(rel: str) -> Any:
    """`contracts/<rel>` を読む。同じキーが 2 つあれば断る（後の値で黙って上書きしない）。"""
    path = _dir / rel
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"契約 {path} を読めない: {exc.strerror or exc}") from None
    try:
        return json.loads(text, object_pairs_hook=_refuse_duplicate_keys)
    except ContractError as exc:
        raise ContractError(f"契約 {path}: {exc}") from None
    except ValueError as exc:
        raise ContractError(f"契約 {path} が JSON として壊れている: {exc}") from None


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


@cache
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
