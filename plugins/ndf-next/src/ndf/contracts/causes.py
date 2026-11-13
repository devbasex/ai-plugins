"""止まった理由の表（`contracts/causes.json`）の読み手と、結果 JSON の `cause` の組み立てと確かめ。

種類の語彙・終了コード・既定の行き先は表だけが持ち、再試行の値は上限の宣言だけが持つ。
コードは種類を `cause("<種類>", ...)` の引数の文字列でだけ書き、種類の定数を置かない。
行き先を選ぶ関数は `routing` にある。
"""

from __future__ import annotations

from typing import Any, NamedTuple, Optional

from ndf import contracts
from ndf.contracts import exit_codes, limits

TABLE = "causes.json"
SCHEMA = contracts.schema_path("causes")
# 結果 JSON の `cause` の形（正本は結果のスキーマの $defs）
CAUSE_SCHEMA = contracts.schema_path("result") + "#/$defs/cause"


class CausesError(contracts.ContractError):
    """表が無い・形が違う・表に無い種類を引いた。"""


class CauseRow(NamedTuple):
    kind: str
    meaning: str
    std_type: str
    exit: int
    route: str
    retry: Optional[str]
    requires_retry_after: bool = False


def _policy_errors(row: CauseRow) -> list[str]:
    if row.retry is None:
        return []
    try:
        p = limits.policy(row.retry)
    except limits.LimitsError as exc:
        return [f"{row.kind} の再試行の方針: {exc}"]
    errs = [] if p.attempts is not None else [f"{row.kind} の方針 {row.retry} に attempts が無い"]
    if p.interval is None and not row.requires_retry_after:
        errs.append(f"{row.kind} の方針 {row.retry} に interval が無い（retry_after を待つ種類でもない）")
    return errs


def validate(raw: Any) -> dict[str, CauseRow]:
    """表の形・種類の重なり・終了コードの区分・再試行の方針の有無を確かめ、種類ごとの行を返す。"""
    errs = contracts.schema_errors(raw, SCHEMA)
    if errs:
        raise CausesError(f"止まった理由の表 {TABLE} の形が違う: {'; '.join(errs)}")
    out: dict[str, CauseRow] = {}
    codes = exit_codes.codes("stopped")
    for item in raw["causes"]:
        row = CauseRow(**item)
        if row.kind in out:
            errs.append(f"種類 {row.kind} が 2 つある")
        if row.exit not in codes:
            errs.append(f"{row.kind} の終了コード {row.exit} は stopped の終了コード {sorted(codes)} に無い")
        errs += _policy_errors(row)
        out[row.kind] = row
    if errs:
        raise CausesError(f"止まった理由の表 {TABLE} の誤り: {'; '.join(errs)}")
    return out


@contracts.cached_reader
def table() -> dict[str, CauseRow]:
    """表を 1 プロセスに 1 回読み、種類ごとの行を返す。"""
    with contracts.raised_as(CausesError):
        return validate(contracts.load_json(TABLE))


def row(kind: str) -> CauseRow:
    found = table().get(kind)
    if found is None:
        raise CausesError(f"止まった理由の表に種類 {kind!r} が無い")
    return found


def build(kind: str, detail: str, *, instance: Optional[str] = None, retry_after: Optional[float] = None) -> dict[str, Any]:
    """結果 JSON の `cause` を確かめずに組む。`instance`・`retry_after` は渡したときだけ置く。"""
    out: dict[str, Any] = {"kind": kind, "detail": detail}
    if instance is not None:
        out["instance"] = instance
    if retry_after is not None:
        out["retry_after"] = retry_after
    return out


def cause(kind: str, detail: str, *, instance: Optional[str] = None, retry_after: Optional[float] = None) -> dict[str, Any]:
    """結果 JSON の `cause` を組む。表に無い種類・空の説明は `ValueError`。"""
    out = build(kind, detail, instance=instance, retry_after=retry_after)
    bad = errors(out)
    if bad:
        raise ValueError("; ".join(bad))
    return out


def kind_of(value: object) -> Optional[str]:
    """`cause` の値の種類。dict で kind が文字列ならそれ、ほかは None。"""
    kind = value.get("kind") if isinstance(value, dict) else None
    return kind if isinstance(kind, str) else None


def kind_errors(value: object) -> list[str]:
    """`cause.kind` が文字列なのに止まった理由の表に無いときの 1 行。形の誤りはスキーマが報告する。"""
    kind = kind_of(value)
    if kind is not None and kind not in table():
        return [f"cause.kind {kind!r} は止まった理由の表に無い"]
    return []


def errors(value: object) -> list[str]:
    """`cause` の値の誤りを並べる。形は result.schema.json の `$defs/cause`、種類の有無は表と照らす。"""
    return contracts.schema_errors(value, CAUSE_SCHEMA) + kind_errors(value)


def exit_code(kind: str) -> int:
    """種類の終了コード。"""
    return row(kind).exit


def policy_of(kind: str) -> Optional[limits.Policy]:
    """種類の再試行の方針。既定の行き先が打ち直しでない種類は None。"""
    name = row(kind).retry
    return limits.policy(name) if name else None
