"""コントラクトの表（`contracts/`）の保存先と、JSON とスキーマの読み込み。

コントラクトの表はプラグインのコードと一緒に配るため、保存先はプラグインの根（`identity.ROOT`。ファイルの位置から求める
1 通り）の下に置く（環境変数を読まない。ほかのプラグインの `CLAUDE_PLUGIN_ROOT` が残っていても、そのプラグインの表を読まない）。
表ごとの読み手（`facts`・`causes`・`limits`・`result`・`chosen`・`version`）はここを通して読み、1 プロセスに 1 回だけ読む。
D1 を閉じた後のコントラクトの変更は #3284 の追補（D1+）の PR で出す（オーナーは `contracts/version.json` の `owner`）。
"""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from functools import cache
from pathlib import Path
from typing import Any, TypeVar

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

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


def require_inside(path: Path, plugin_root: Path) -> Path:
    """`path` そのもの。シンボリックリンク（親のディレクトリのものを含む）を解いた先がプラグイン `plugin_root` の外なら、読む前に断る。

    外の値は読み手の誤りの文に載り、検査の結果として CI のログへ出る。プラグインの外は配られないので、正しいリンクでもない。
    """
    if not path.resolve().is_relative_to(plugin_root.resolve()):
        raise ContractError(f"コントラクト {path} がプラグインの外を指す")
    return path


def require_tree_inside(top: Path, plugin_root: Path) -> None:
    """コントラクトの保存先 `top` とその下のシンボリックリンクを `require_inside` で確かめる。本文は読まない。

    `top` の下のディレクトリのリンクは、プラグインの中を指しても断る。リンク先へは降りないので、その中にある外を指すリンクを確かめられない。
    """
    require_inside(top, plugin_root)
    for parent, dirs, files in os.walk(top):
        for path in (p for p in (Path(parent, name) for name in sorted(dirs + files)) if p.is_symlink()):
            require_inside(path, plugin_root)
            if path.is_dir():
                raise ContractError(f"コントラクト {path} がディレクトリのリンクである")


def load_json(rel: str) -> Any:
    """`contracts/<rel>` を読む。同じキーが 2 つあれば断る（後の値で黙って上書きしない）。"""
    return read_json(require_inside(_dir / rel, _dir.parent))


def load_text(rel: str) -> str:
    """`contracts/<rel>` の本文（JSON でないファイル。AI への指示に入れる文書など）。"""
    return _read_text(require_inside(_dir / rel, _dir.parent))


def read_json(path: Path) -> Any:
    """コントラクトのファイル 1 本の値。断るものは `load_json` と同じ。"""
    return parse_file(path, _read_text(path))


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"コントラクト {path} を読めない: {exc.strerror or exc}") from None
    except UnicodeDecodeError as exc:
        raise ContractError(f"コントラクト {path} を UTF-8 として読めない: 位置 {exc.start}") from None


def parse_file(path: Path, text: str) -> Any:
    """コントラクトのファイルの本文 `text` の値。断るものは `load_json` と同じ（検査が、読み込み済みの本文を同じ規則で読む）。"""
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
    root = require_inside(_dir / rel, _dir.parent)
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
def load_schema(path: str) -> Mapping[str, Any]:
    """スキーマのファイルを 1 プロセスに 1 回読む（検証器と、値をスキーマから組み立てる定数が共有する）。返す値は書き換えない。"""
    return load_json(path)


def schema_enum(path: str, pointer: str) -> tuple[str, ...]:
    """スキーマの JSON Pointer の先の `enum` を並びのまま返す。先か `enum` が無ければ `ContractError`。"""
    node: Any = load_schema(path)
    try:
        for key in pointer.split("/")[1:]:
            node = node[key]
        return tuple(node["enum"])
    except (KeyError, TypeError):
        raise ContractError(f"スキーマ {path} の {pointer} に enum が無い") from None


@cached_reader
def _shipped_validator(rel: str) -> Draft202012Validator:
    """`rel` の検証器（スキーマ自体は確かめない）。`<ファイル>#/$defs/<名前>` の形なら、そのファイルの `$defs` の 1 つで確かめる。"""
    path, _, pointer = rel.partition("#")
    schema = load_schema(path)
    if pointer:
        if "$defs" not in schema:
            raise ContractError(f"スキーマ {path} に $defs が無い（{rel} を引けない）")
        schema = {"$schema": schema.get("$schema"), "$defs": schema["$defs"], "$ref": f"#{pointer}"}
    return Draft202012Validator(schema)


@cached_reader
def _schema_validator(rel: str) -> Draft202012Validator:
    """`rel` の検証器。スキーマ自体も確かめる。"""
    validator = _shipped_validator(rel)
    try:
        Draft202012Validator.check_schema(validator.schema)
    except SchemaError as exc:
        raise ContractError(f"スキーマ {_dir / rel} がスキーマとして誤り: {exc.message}") from None
    return validator


def schema_errors(instance: Any, rel: str) -> list[str]:
    """`instance` を `contracts/<rel>` のスキーマで確かめ、誤りを `<場所>: <理由>` の並びで返す。空なら合う。"""
    return _errors(_schema_validator(rel), instance)


def _errors(validator: Draft202012Validator, instance: Any) -> list[str]:
    found = sorted(("/".join(str(p) for p in e.absolute_path) or "(最上位)", e.message) for e in validator.iter_errors(instance))
    return [f"{where}: {message}" for where, message in found]


def broken_rules(instance: Any, rel: str) -> list[str]:
    """`instance` が破った `contracts/<rel>` のスキーマの場所と規則の名前（誤り 1 件につき 1 つ・昇順）。値を含まない。"""
    found = _schema_validator(rel).iter_errors(instance)
    return sorted(broken_rule(e) for e in found)


def broken_rule(error: ValidationError) -> str:
    """破ったスキーマの場所と規則の名前。入力の値もキーも含まない。欠けた欄（`required`）の文はスキーマにある欄の名前だけを含むので添える。"""
    rule = f"{'/'.join(str(p) for p in error.absolute_schema_path)}（{error.validator}）"
    return f"{rule}: {error.message}" if error.validator == "required" else rule


def broken_places(instance: Any, rel: str) -> list[str]:
    """`instance` が破った `contracts/<rel>` のスキーマの、入力の場所と規則の名前（`<場所>（<規則>）`・昇順）。値を含まない。"""
    found = _schema_validator(rel).iter_errors(instance)
    return sorted(f"{'/'.join(str(p) for p in e.absolute_path) or '(最上位)'}（{e.validator}）" for e in found)


def require_schema(instance: Any, rel: str, error_cls: type[ContractError], label: str) -> None:
    """`instance` が `contracts/<rel>` のスキーマに合わなければ `error_cls`（`<label> の形が違う: <誤り>`）を投げる。"""
    _refuse(schema_errors(instance, rel), error_cls, label)


def require_shipped(instance: Any, rel: str, error_cls: type[ContractError], label: str) -> None:
    """`require_schema` と同じだが、プラグインのスキーマ自体は確かめ直さない（CI が確かめる。短い間に何度も起動する子が使う）。"""
    with raised_as(error_cls):
        errs = _errors(_shipped_validator(rel), instance)
    _refuse(errs, error_cls, label)


def _refuse(errs: list[str], error_cls: type[ContractError], label: str) -> None:
    if errs:
        raise error_cls(f"{label} の形が違う: {'; '.join(errs)}")


def schema_path(name: str) -> str:
    """`contracts/schemas/<name>.schema.json` の `contracts/` からの相対パス。"""
    return f"{SCHEMAS}/{name}.schema.json"
