"""内部データ（記録・実行の状態）の読み書きの核: 種類ごとの型・形のバージョン・古いバージョンの移行（#2339 の決定 9・10・11）。

種類ごとの型は `contracts/schemas/internal/<種類>.schema.json` にあり、書く前と読んだ後の両方で確かめる。形に合わなければ
`InternalError` を投げ、サブコマンドのエントリポイント（`ndf.cli`）が `stopped` にする。古いバージョンの形を知るのは `KINDS` の移し方だけで、
読み手は今のバージョンの形だけを扱う。種類を足すときは、スキーマを置き、`KINDS` に 1 行を足す。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple, Optional

from jsonschema import Draft202012Validator, FormatChecker

from ndf import contracts

NAMES = "data/internal-names.json"
NAMES_SCHEMA = contracts.schema_path("internal-names")
COMMON = contracts.schema_path("internal/common")
# 工程の文脈のスキーマ
CONTEXT = "workflow/context.schema.json"
# 名前の対応表の、書いた時刻の意味
TIME = "書いた時刻"

Row = dict[str, Any]


class InternalError(Exception):
    """内部データの形が違う・種類が無い・新しいバージョン。`kind` は停止理由の種類で、文に欄の値を含まない。"""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind


class Kind(NamedTuple):
    """内部データの種類 1 つ。`steps[n]` はバージョン n の行をバージョン n + 1 の形へ移す。`marker` は、バージョン 1 から必ず持つ欄で、`version` が
    整数の別の意味だった種類だけが持つ（この欄の無い行をバージョン 0 と見分ける）。"""

    schema: str
    steps: tuple[Callable[[Row], Row], ...]
    marker: Optional[str] = None


# --- バージョン 0 → 1 の移し方 ------------------------------------------------------------------------------


def _time_to_at(row: Row) -> Row:
    """書いた時刻を、名前の対応表の消した別名から `at` へ移す。"""
    found = next((k for k in names()[TIME]["aliases_removed"] if k in row), None)
    if "at" in row or found is None:
        return row
    return {"at" if k == found else k: v for k, v in row.items()}


def _signal_v1(row: Row) -> Row:
    """`version` は MVV の設定のバージョンだった。"""
    return {"mvv_version": None, **_renamed(row, "version", "mvv_version")}


def _decision_v1(row: Row) -> Row:
    """旧い形のバージョン（`schema`）と、消した方針の列を外す。種類の無い行は判定の行。"""
    kept = {k: v for k, v in _time_to_at(row).items() if k not in ("schema", "policy", "margin", "config_error")}
    return {"event": "判定", **kept}


def _renamed(row: Row, old: str, new: str) -> Row:
    return {new if k == old else k: v for k, v in row.items()}


def _context_v1(row: Row) -> Row:
    """`version` は出した製品のバージョンだった。"""
    return _renamed(row, "version", "product_version")


def stage_value_key(key: str) -> str:
    """工程どうしで渡す値（プランの出力・工程の入力。形のバージョンを持たない）の今の名前。旧い `version` は出した製品のバージョンだった。"""
    return "product_version" if key == "version" else key


def stage_values(raw: Mapping[str, Any]) -> Row:
    """保存済みの、工程どうしで渡す値を今の形へ移す。旧い名前と今の名前の両方があれば今の名前の値を残す。"""
    return {new: v for k, v in raw.items() if (new := stage_value_key(k)) == k or new not in raw}


def _snapshot_v1(row: Row) -> Row:
    """`version` は工程の定義の文書のバージョンで、工程の入出力のキーの `version` は出した製品のバージョンだった。"""
    moved = _renamed(row, "version", "definition_version")
    if isinstance(moved.get("stages"), list):
        moved["stages"] = [_stage_v1(s) if isinstance(s, dict) else s for s in moved["stages"]]
    return moved


def _stage_v1(stage: Row) -> Row:
    keys = {k: stage[k] for k in ("inputs", "outputs") if isinstance(stage.get(k), list)}
    return {**stage, **{k: [stage_value_key(name) for name in names] for k, names in keys.items()}}


def _loop_event_v1(row: Row) -> Row:
    """`at` は UNIX 時刻の秒で、文字列の `version` はリリースした製品のバージョンだった。"""
    out = {"product_version" if k == "version" and isinstance(v, str) else k: v for k, v in row.items()}
    if "at" in out and type(out["at"]) in (int, float):
        out["at"] = stamp(out["at"])
    return out


KINDS: dict[str, Kind] = {
    "usage": Kind(contracts.schema_path("internal/usage"), (dict,)),
    "mvv-signals": Kind(contracts.schema_path("internal/mvv-signals"), (_signal_v1,), marker="mvv_version"),
    "mvv-validation": Kind(contracts.schema_path("internal/mvv-validation"), (_time_to_at,)),
    "context-records": Kind(contracts.schema_path("internal/context-records"), (_decision_v1,)),
    "context": Kind(CONTEXT, (_context_v1,)),
    "stage-snapshot": Kind(contracts.schema_path("internal/stage-snapshot"), (_snapshot_v1,)),
    "loop-event": Kind(contracts.schema_path("internal/loop-event"), (_loop_event_v1,)),
    "reading-check": Kind(contracts.schema_path("internal/reading-check"), (dict,)),
    "reading-docs": Kind(contracts.schema_path("internal/reading-docs"), (dict,)),
    "mvv-gate-failure": Kind(contracts.schema_path("internal/mvv-gate-failure"), (dict,)),
}


@contracts.cached_reader
def version() -> int:
    """内部データの今の形のバージョン（共通の欄のスキーマの、読めるバージョンの並びの最後）。"""
    return contracts.load_json(COMMON)["$defs"]["version"]["enum"][-1]


# --- 読み書き ----------------------------------------------------------------------------------------


def read_record(kind: str, raw: Mapping[str, Any]) -> Row:
    """1 行を読む。古いバージョンは今のバージョンの形へ移してから確かめる。形が違う・新しいバージョンなら `InternalError`。"""
    row = migrate(kind, raw)
    _require(kind, row)
    return row


def read_line(kind: str, line: str) -> Row:
    """JSONL の 1 行を読む。JSON として読めない・辞書でない行も `InternalError`（`invalid`）。"""
    return read_record(kind, parse_line(line))


def parse_line(line: str) -> dict[str, Any]:
    """JSONL の 1 行を辞書にする。JSON として読めない・辞書でないなら `InternalError`（`invalid`）。"""
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        raise InternalError("invalid", f"JSON として読めない（{exc.msg}）") from exc
    if not isinstance(raw, dict):
        raise InternalError("invalid", "辞書でない")
    return raw


def write_record(kind: str, row: Mapping[str, Any]) -> Row:
    """書く行を今のバージョンの形で確かめ、`version` と `at`（種類が持ち、呼び手が渡さなければ今の時刻）を埋めて返す。"""
    out: Row = {"version": version(), **row}
    if "at" not in out and "at" in _validator(_kind(kind).schema).schema["properties"]:
        out["at"] = stamp(time.time())
    _require(kind, out)
    return out


def migrate(kind: str, raw: Mapping[str, Any]) -> Row:
    """古いバージョンの行を今のバージョンの形へ移す。新しいバージョンなら `InternalError`（`precondition`）。"""
    found, current = readable_version(kind, raw), version()
    row = dict(raw)
    if found == current:
        return row
    for step in _kind(kind).steps[found:current]:
        row = step(row)
    return {**row, "version": current}


def readable_version(kind: str, raw: Mapping[str, Any]) -> int:
    """行の形のバージョン（移さず、形も確かめない）。新しいバージョンなら `InternalError`（`precondition`）。"""
    found = _version_of(_kind(kind), raw)
    if found > version():
        raise InternalError(
            "precondition", f"新しいバージョンの記録（{kind}・バージョン {found}）を古い NDF が読んだ。プラグインを更新する"
        )
    return found


def _kind(kind: str) -> Kind:
    if kind not in KINDS:
        raise InternalError("config_broken", f"内部データの種類 {kind!r} が無い（{'・'.join(KINDS)} のどれか）")
    return KINDS[kind]


def _version_of(spec: Kind, raw: Mapping[str, Any]) -> int:
    """行の形のバージョン。整数の `version` を持たない行と、`marker` の欄の無い行はバージョン 0。1 未満の整数は今のバージョンと見て、形の確かめで落とす。"""
    found = raw.get("version")
    if type(found) is not int or (spec.marker is not None and spec.marker not in raw):
        return 0
    return found if found >= 1 else version()


# --- 形の確かめ --------------------------------------------------------------------------------------

_FORMATS = FormatChecker(formats=())


@_FORMATS.checks("date-time")
def _is_utc(value: object) -> bool:
    return not isinstance(value, str) or _utc(value) is not None


def _utc(text: str) -> Optional[datetime]:
    """UTC（時差が 0）の時刻として読む。読めない・時差が無い・0 でないなら None。"""
    try:
        at = datetime.fromisoformat(text)
    except ValueError:  # noqa: NDF401 ファイル: 読めない時刻は呼び手が形の違いとして扱う
        return None
    return at if at.utcoffset() == timedelta(0) else None


@contracts.cached_reader
def _validator(schema: str) -> Draft202012Validator:
    """種類のスキーマの検証器。共通の欄のスキーマを `$defs` に埋め込み、書式の検査器を渡す（ほかのコントラクトの `format` は効かせない）。"""
    own = contracts.load_json(schema)
    common = contracts.load_json(COMMON)
    defs = {**(own["$defs"] if "$defs" in own else {}), "common": common}
    return Draft202012Validator({**own, "$defs": defs}, format_checker=_FORMATS)


def _require(kind: str, row: Row) -> None:
    """種類のスキーマに合わなければ `InternalError`。誤りの文はスキーマの側の語だけで組む（キーが値を持つ辞書と、スキーマに無い
    キーがあるため、行の値もキーも含まない）。"""
    errors = {contracts.broken_rule(e) for e in _validator(_kind(kind).schema).iter_errors(row)}
    if errors:
        raise InternalError("invalid", f"内部データ {kind} の形が違う: {'; '.join(sorted(errors))}")


@contracts.cached_reader
def names() -> Mapping[str, Mapping[str, Any]]:
    """名前の対応表（意味 → 使う 1 つの名前 `key`・消した別名 `aliases_removed`・使ってよい種類 `where`）。"""
    raw = contracts.load_json(NAMES)
    contracts.require_schema(raw, NAMES_SCHEMA, contracts.ContractError, f"名前の対応表 {NAMES}")
    return raw["names"]


# --- 時刻 --------------------------------------------------------------------------------------------


def stamp(seconds: float) -> str:
    """秒（UNIX 時刻）→ `at` の文字列（UTC・マイクロ秒まで）。時刻にできない数なら `InternalError`。"""
    try:
        return datetime.fromtimestamp(seconds, UTC).isoformat(timespec="microseconds")
    except (ValueError, OverflowError, OSError) as exc:
        raise InternalError("invalid", "秒を時刻にできない") from exc


def epoch(at: str) -> float:
    """`at` の文字列 → 秒（UNIX 時刻）。UTC の時刻として読めなければ `InternalError`。"""
    found = _utc(at)
    if found is None:
        raise InternalError("invalid", "時刻が UTC の ISO 8601 でない")
    return found.timestamp()
