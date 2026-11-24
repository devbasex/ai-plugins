"""停止理由の表（`contracts/causes/<種類>.json`。1 種類 1 ファイル）の読み手と、結果 JSON の `cause` の組み立てと確かめ。

種類の語彙・既定のルートは表だけが持ち、終了コードの値は終了コードの表（`exit_codes`）だけが、再試行の値は上限の設定だけが持つ。
表の `exit` は終了コードの表の stopped の行の id を指し、読み手が値に引き直して `CauseRow.exit` に置く。
コードは種類を `cause("<種類>", ...)` の引数の文字列でだけ書き、種類の定数を置かない。
ルートを選ぶ関数は `routing` にある。
"""

from __future__ import annotations

from typing import Any, NamedTuple, Optional

from ndf import contracts
from ndf.contracts import exit_codes, facts, limits

TABLE = "causes"
SCHEMA = contracts.schema_path("causes")
# 結果 JSON の `cause` の形（SSOT は結果のスキーマの $defs）
CAUSE_SCHEMA = contracts.schema_path("result") + "#/$defs/cause"


class CausesError(contracts.ContractError):
    """表が無い・形が違う・表に無い種類を引いた。"""


class CauseRow(NamedTuple):
    """表の 1 行。`exit` は終了コードの表から引き直した値、`exit_id` は表に書いた終了コードの表の id。"""

    kind: str
    meaning: str
    std_type: str
    exit: int
    route: str
    retry: Optional[str]
    exhausted: Optional[str] = None
    requires_retry_after: bool = False
    exit_id: str = ""


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


def _with_kind(kind: str, body: dict[str, Any]) -> dict[str, Any]:
    """項目の中身へ種類（ファイル名）を足す。中身の kind がファイル名と違えば `CausesError`。"""
    if "kind" in body and body["kind"] != kind:
        raise CausesError(f"停止理由の表 {TABLE}/{kind}.json の kind {body['kind']!r} がファイル名と違う")
    return {**body, "kind": kind}


def _rows_of(raw: dict[str, Any]) -> dict[str, CauseRow]:
    """種類（ファイル名）→ 中身の辞書の形と、exit が終了コードの表の stopped の id を指すことを確かめ、種類ごとの行を返す。"""
    items = [_with_kind(kind, body) for kind, body in raw.items()]
    shape = [f"{item['kind']}: {e}" for item in items for e in contracts.schema_errors(item, SCHEMA)]
    if shape:
        raise CausesError(f"停止理由の表 {TABLE} の形が違う: {'; '.join(shape)}")
    errs: list[str] = []
    out: dict[str, CauseRow] = {}
    stopped = {r["id"]: r["code"] for r in facts.rows(exit_codes.TABLE, status=exit_codes.STOPPED)}
    for item in items:
        exit_id = item["exit"]
        if exit_id not in stopped:
            errs.append(f"{item['kind']} の exit {exit_id!r} は終了コードの表の stopped の id {sorted(stopped)} に無い")
            continue
        out[item["kind"]] = CauseRow(**{**item, "exit": stopped[exit_id], "exit_id": exit_id})
    _raise_errors(errs)
    return out


def _raise_errors(errs: list[str]) -> None:
    if errs:
        raise CausesError(f"停止理由の表 {TABLE} の誤り: {'; '.join(errs)}")


def _with_policies_checked(found: dict[str, CauseRow]) -> dict[str, CauseRow]:
    """行が指す再試行の方針の有無と値を確かめる。方針のファイルを読む。"""
    _raise_errors([e for item in found.values() for e in _policy_errors(item)])
    return found


@contracts.cached_reader
def rows() -> dict[str, CauseRow]:
    """表の全件を 1 プロセスに 1 回読み、種類ごとの行を返す（一覧に使う）。再試行の方針は名前だけを返し、そのファイルを読まない。"""
    with contracts.raised_as(CausesError):
        return _rows_of(contracts.load_table(TABLE, CausesError, "停止理由の表"))


@contracts.cached_reader
def table() -> dict[str, CauseRow]:
    """`rows` に加えて再試行の方針の値まで確かめた表の全件（表全体の検査に使う。種類 1 つの参照は `row`・`has`）。"""
    with contracts.raised_as(CausesError):
        return _with_policies_checked(rows())


def has(kind: str) -> bool:
    """種類が表にあるか。項目のファイルは読まない。"""
    with contracts.raised_as(CausesError):
        return kind in contracts.split_keys(TABLE)


@contracts.cached_reader
def row(kind: str) -> CauseRow:
    """種類 1 つの行。その種類のファイルだけを 1 プロセスに 1 回読む。無ければ `CausesError`。"""
    with contracts.raised_as(CausesError):
        body = contracts.load_item(TABLE, kind)
        if body is None:
            raise CausesError(f"停止理由の表に種類 {kind!r} が無い")
        return _with_policies_checked(_rows_of({kind: body}))[kind]


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


def unknown_kind_errors(kind: str, label: str) -> list[str]:
    """種類 `kind` が停止理由の表に無いときの 1 行（`label` は誤りの文で種類を指す名前）。"""
    if not has(kind):
        return [f"{label} {kind!r} は停止理由の表に無い"]
    return []


def kind_errors(value: object) -> list[str]:
    """`cause.kind` が文字列なのに停止理由の表に無いときの 1 行。形の誤りはスキーマが報告する。"""
    kind = kind_of(value)
    return unknown_kind_errors(kind, "cause.kind") if kind is not None else []


def errors(value: object) -> list[str]:
    """`cause` の値の誤りを並べる。形は result.schema.json の `$defs/cause`、種類の有無は表と照らす。"""
    return contracts.schema_errors(value, CAUSE_SCHEMA) + kind_errors(value)


def exit_code(kind: str) -> int:
    """種類の終了コード。"""
    return row(kind).exit


def policy_of(kind: str) -> Optional[limits.Policy]:
    """種類の再試行の方針。既定のルートが打ち直しでない種類は None。"""
    name = row(kind).retry
    return limits.policy(name) if name else None


def _backoff(pol: limits.Policy, used: int) -> float:
    """方針の間隔 × 倍率 ^ 打ち直した回数の秒。頭打ちがあればそこまで。"""
    base = pol.interval if pol.interval is not None else 0.0
    delay = base * ((pol.factor if pol.factor is not None else 1.0) ** used)
    return min(delay, pol.max_interval) if pol.max_interval is not None else delay


def retry_delay(cause: Optional[dict[str, Any]], used: int) -> Optional[float]:
    """打ち直しで直る止まりの次の待ちの秒。打ち直さない（ルートが retry でない・回数が尽きた）なら None。"""
    kind = kind_of(cause)
    if cause is None or kind is None or not has(kind):
        return None
    found = row(kind)
    if found.route != "retry":
        return None
    pol = policy_of(kind)
    if pol is None or pol.attempts is None or used >= pol.attempts:
        return None
    if found.requires_retry_after:
        after = cause.get("retry_after")
        return float(after) if isinstance(after, (int, float)) else None
    return _backoff(pol, used)
