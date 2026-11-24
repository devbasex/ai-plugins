"""上限の設定（`contracts/limits/<方針の名前>.json`。1 方針 1 ファイル）の読み手。

時間・回数・間隔・量の上限は設定だけが値を持ち、コードは方針の名前で引く（値を写さない）。
方針 1 つの形は `contracts/schemas/limits.schema.json` にある。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple, Optional, get_type_hints

from ndf import contracts

NDF_SETTINGS = "limits"
SCHEMA = contracts.schema_path("limits")
# 方針の名前の形（SSOT は設定のスキーマの $defs）
NAME_SCHEMA = SCHEMA + "#/$defs/name"


class LimitsError(contracts.ContractError):
    """設定が無い・形が違う・引いた方針や値が無い。"""


class EnvError(ValueError):
    """上限を替える環境変数の値の形が違う。"""


class Policy(NamedTuple):
    """設定の 1 行。方針に無い値は None。"""

    name: str
    summary: str
    seconds: Optional[float] = None
    interval: Optional[float] = None
    factor: Optional[float] = None
    max_interval: Optional[float] = None
    per_call: Optional[float] = None
    attempts: Optional[int] = None
    same_cause: Optional[int] = None
    count: Optional[int] = None
    # hook の予算（`hook.budget.<事象>`）だけが持つ値
    p95_ms: Optional[float] = None
    deadline: Optional[float] = None
    margin: Optional[float] = None
    timeout: Optional[float] = None
    inject_chars: Optional[int] = None
    # 重い起動のスロットの重み
    gib: Optional[float] = None
    # API キーの helper（`relay.key-helper`）だけが持つ値
    lock: Optional[float] = None
    gate: Optional[float] = None
    first: Optional[float] = None
    # 本文の待ちの判定（`hook.stop-wait.judge`。#4000）だけが持つ値
    max_chars: Optional[int] = None
    rest_seconds: Optional[float] = None
    lock_seconds: Optional[float] = None
    rounds: Optional[int] = None
    model: Optional[str] = None
    runtimes: Optional[list[str]] = None
    auth_markers: Optional[list[str]] = None
    endpoint: Optional[dict[str, Any]] = None
    # 公開の記述の取得（`samples.fetch`。#3687）だけが持つ値
    retries: Optional[int] = None
    max_bytes: Optional[int] = None
    peers: Optional[dict[str, int]] = None
    # プロンプトのキャッシュの起床（`cache.*`。#4068）だけが持つ値
    ratios: Optional[dict[str, float]] = None
    default: Optional[float] = None
    ratio: Optional[float] = None
    versions: Optional[tuple[str, ...]] = None
    calls: Optional[int] = None
    start: Optional[int] = None
    # 読み手の指定: 方針を引くモジュールの名前（規則 `limit-readers` が実際の読み手と突き合わせる。値として引かない）
    read_by: tuple[str, ...] = ()


# 方針の数の値の名前（型が数の Policy の項目）。数でない値（判定のモデル・ランタイム・文字列の並び・送信先の確かめ・相手ごとの
# 上限）は `value` で引かず、判定の子・hook の表・見本の突き合わせが項目から読む
VALUE_KEYS = tuple(f for f, t in get_type_hints(Policy).items() if t in (Optional[int], Optional[float]))
# 方針の文字列の並びの名前（型が文字列の tuple の Policy の項目。`texts` で引く）
TEXT_KEYS = tuple(f for f, t in get_type_hints(Policy).items() if t == Optional[tuple[str, ...]])


def validate(raw: dict[str, Any]) -> dict[str, Policy]:
    """方針の名前 → 中身の辞書を確かめて方針の表にする。形の誤りは `LimitsError`。"""
    errs = [
        f"{name}: {e}"
        for name, row in raw.items()
        for e in (*contracts.schema_errors(name, NAME_SCHEMA), *contracts.schema_errors(row, SCHEMA))
    ]
    if errs:
        raise LimitsError(f"上限の設定 {NDF_SETTINGS} の形が違う: {'; '.join(errs)}")
    return {name: Policy(name=name, **{**row, **_tuples(row)}) for name, row in raw.items()}


def _tuples(row: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """配列で書く文字列の並び（`read_by` と `TEXT_KEYS`）を tuple にする。"""
    return {k: tuple(row[k]) for k in ("read_by", *TEXT_KEYS) if k in row}


@contracts.cached_reader
def _checked() -> dict[str, Policy]:
    """このプロセスで読んで確かめた方針のキャッシュ。`table` と `policy` が足し、`policy` が引く。"""
    return {}


@contracts.cached_reader
def table() -> dict[str, Policy]:
    """設定の全件を 1 プロセスに 1 回読む（表全体の検査に使う。方針 1 つの参照は `policy`）。"""
    found = validate(contracts.load_table(NDF_SETTINGS, LimitsError, "上限の設定"))
    _checked().update(found)
    return found


def policy(name: str) -> Policy:
    """方針の名前で 1 行を引く。まだ読んでいない方針ならそのファイルだけを読む。無ければ `LimitsError`。"""
    checked = _checked()
    if name not in checked:
        with contracts.raised_as(LimitsError):
            body = contracts.load_item(NDF_SETTINGS, name)
        if body is None:
            raise LimitsError(f"上限の設定に方針 {name} が無い")
        checked.update(validate({name: body}))
    return checked[name]


def value(name: str, key: str) -> float:
    """方針の 1 つの数の値。方針にその値が無い・数でない（`ratios`・`versions`）なら `LimitsError`。"""
    got = getattr(policy(name), key) if key in VALUE_KEYS else None
    if got is None:
        raise LimitsError(f"上限の設定の方針 {name} に {key} が無い")
    return got


def texts(name: str, key: str) -> tuple[str, ...]:
    """方針の文字列の並び（`versions`）。方針にその値が無い・文字列の並びでなければ `LimitsError`。"""
    got = getattr(policy(name), key) if key in TEXT_KEYS else None
    if got is None:
        raise LimitsError(f"上限の設定の方針 {name} に文字列の並びの {key} が無い")
    return got


def seconds(name: str) -> float:
    """方針の上限の秒。"""
    return value(name, "seconds")


def scaled(name: str, measured: Optional[float]) -> float:
    """実測から決める上限 = max(seconds, factor × 実測)。実測が無ければ seconds。

    seconds は下限で、実測の記録が少なく短くても上限はこれを下回らない。
    """
    floor = seconds(name)
    if measured is None:
        return floor
    return max(floor, value(name, "factor") * float(measured))


def from_env(environ: Mapping[str, str], name: str, policy_name: str, key: str) -> float:
    """環境変数 `name` があればその値、無ければ設定の値。数でない・0 以下なら `EnvError`。

    リレーハブとマシン全体の本数の判定が同じ環境変数を同じ規則で読む（片方だけが替わった上限で数えない）。
    """
    raw = environ.get(name)
    if not raw:
        return value(policy_name, key)
    try:
        got = float(raw)
    except ValueError as exc:
        raise EnvError(f"{name} の値が数でない: {raw}") from exc
    if got <= 0:
        raise EnvError(f"{name} の値は 0 より大きくする: {raw}")
    return got
