"""`ndf relay cache report`: 起床の記録（`cache-log.jsonl`）の集計（#4068・#4199 の詳細設計の「集計」）。

`relay/cli.py` から分けた。結果 JSON の tool の名前は `ndf relay` と同じ。費用は起床の hook が行に書いた倍率で出し、
上限の設定を読まない（値を直した後も、過去の行の費用は書いた時点の値のまま）。避けた書き直しは反実仮想の推定で、
項目の名前と要約に「推定」を付ける（前提は設計の決定 9）。
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable, Iterable, Mapping, Optional

from ndf import cli, identity
from ndf.contracts import result
from ndf.domains.hooks.rewake import LIMIT
from ndf.domains.relay import registry, signals
from ndf.domains.relay.keep_cache import TTL_SECONDS

# 結果 JSON の tool の名前はサブコマンドの名前（SSOT は `cli.COMMANDS`）
TOOL = cli.tool_of(f"{__package__}.cli")
CACHE_COMMANDS = {"report": "起床の記録から待ち・起床・当たりの率・費用・避けた書き直し（推定）・バージョンごとの取り合いの率を集計する"}
# 作業を待つ Stop の行の種類
STOP_KINDS = frozenset({"work_stop", "contest", "arrival"})
# 当たりの率を分ける `age` の幅（分）。60 分を越えたものは `60m+`
AGE_BUCKETS = (30, 45, 50, 55, 60)
OVER = "60m+"
Row = Mapping[str, Any]
Wait = defaultdict[str, list]


def row_seconds(value: Any) -> Optional[float]:
    """時刻の文字列（ISO 8601）の UNIX 秒。タイムゾーンが無ければ UTC。読めなければ None。"""
    if not isinstance(value, str):
        return None
    try:
        at = datetime.fromisoformat(value)
    except ValueError:  # noqa: NDF401 ファイル: 読めない時刻は None（記録の行は飛ばして数え、--since は引数の誤りにする）
        return None
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).timestamp()


def _field_number(row: Row, key: str) -> float:
    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _write_cost(row: Row) -> float:
    """起床の `d`（寿命別の書き込み × 倍率 + 出力 × 倍率）。"""
    return (
        _field_number(row, "cache_write_1h") * _field_number(row, "w_1h")
        + _field_number(row, "cache_write_5m") * _field_number(row, "w_5m")
        + _field_number(row, "output") * _field_number(row, "out")
    )


def _cost(row: Row) -> float:
    """起床 1 回の実費（読み出しを含む）。"""
    return _field_number(row, "cache_read") * _field_number(row, "r") + _write_cost(row)


def quantile(values: list[float], q: float) -> Optional[float]:
    """線形補間の分位点。空なら None。"""
    if not values:
        return None
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _bucket(age: float) -> str:
    return next((f"{m}m" for m in AGE_BUCKETS if age <= m * 60), OVER)


def _wait_kinds() -> tuple[str, ...]:
    """待ちにまとめる行の種類。"""
    return ("wait_start", "wake", "wake_result", "wait_end", "stop_waking", "wait_void")


def _by_wait(rows: list[Row]) -> list[Wait]:
    """待ちの行を（会話, `wait_id`）でまとめ、種類ごとの並びにする。"""
    waits: dict[tuple, Wait] = {}
    for row in rows:
        if row.get("kind") in _wait_kinds() and row.get("wait_id") is not None:
            waits.setdefault((row.get("session_id"), row["wait_id"]), defaultdict(list))[row["kind"]].append(row)
    return list(waits.values())


def _by_id(wait: Wait, kind: str) -> dict[Any, Row]:
    return {r.get("wake_id"): r for r in wait[kind]}


def _length(wait: Wait) -> Optional[float]:
    """待ちの長さ（待ちの始まりの応答から待ちを終えた入力まで）。待ちが終わっていなければ None。"""
    ends = wait.get("wait_end")
    return _field_number(ends[0], "opened_at") - _field_number(ends[0], "wait_id") if ends else None


def _unpriced(wait: Wait) -> bool:
    """`wake_result` の無い `wake` か、`priced` が偽の `wake_result` を持つ待ち。"""
    results = _by_id(wait, "wake_result")
    return any(i not in results for i in _by_id(wait, "wake")) or any(r.get("priced") is not True for r in results.values())


def _avoided(wait: Wait) -> Optional[tuple[float, float]]:
    """避けた書き直しの推定（token, 費用の差）。数えない待ちは None。"""
    ends, length = wait.get("wait_end"), _length(wait)
    start_ttl = TTL_SECONDS.get(wait["wait_start"][0].get("ttl"))
    if not ends or ends[0].get("hit") is not True or length is None or start_ttl is None or length <= start_ttl:
        return None
    end, wakes = ends[0], _by_id(wait, "wake")
    hit_at = [
        row_seconds(wakes[i].get("at"))
        for i, r in _by_id(wait, "wake_result").items()
        if i in wakes and r.get("outcome") == "woke" and r.get("hit") is True
    ]
    last_hit = max((a for a in hit_at if a is not None), default=None)
    end_ttl = TTL_SECONDS.get(end.get("ttl"))
    if last_hit is None or end_ttl is None or _field_number(end, "opened_at") - last_hit > end_ttl:
        return None
    tokens = _field_number(end, "tokens")
    return tokens, (_field_number(end, "w") - _field_number(end, "r")) * tokens - sum(_cost(r) for r in wait["wake_result"])


def _split(waits: list[Wait], since: Optional[float]) -> tuple[list[Wait], int, int]:
    """（集計する待ち, 取り消した待ちの数, 途中の待ちの数）。"""
    kept, voided, partial = [], 0, 0
    for wait in waits:
        start = wait.get("wait_start")
        if "wait_void" in wait:
            voided += 1
        elif not start or since is not None and (row_seconds(start[0].get("at")) or -math.inf) < since:
            partial += 1
        else:
            kept.append(wait)
    return kept, voided, partial


def _wakes(waits: list[Wait]) -> dict[str, Any]:
    """起床の回数・記録の欠け・当たりの率・`d` と実費の中央値。"""
    ages = {label: [0, 0] for label in (*(f"{m}m" for m in AGE_BUCKETS), OVER)}
    ids: set = set()
    unmatched = orphan = preempted = 0
    ds: list[float] = []
    costs: list[float] = []
    for wait in waits:
        wakes, results = _by_id(wait, "wake"), _by_id(wait, "wake_result")
        ids |= {(id(wait), i) for i in (*wakes, *results)}
        unmatched += sum(1 for i in wakes if i not in results)
        orphan += sum(1 for i in results if i not in wakes)
        preempted += sum(1 for r in results.values() if r.get("outcome") == "preempted")
        for i, r in results.items():
            if i not in wakes or r.get("outcome") != "woke":
                continue
            if r.get("hit") is not None:
                cell = ages[_bucket(_field_number(wakes[i], "age"))]
                cell[0] += 1
                cell[1] += r.get("hit") is True
            if r.get("priced") is True:
                ds.append(_write_cost(r))
                costs.append(_cost(r))
    return {
        "wakes": len(ids),
        "hit_rate_by_age": {k: {"wakes": n, "hits": h, "rate": h / n if n else None} for k, (n, h) in ages.items()},
        "d_median": median(ds) if ds else None,
        "wake_cost_median": median(costs) if costs else None,
        "unmatched_wakes": unmatched,
        "orphan_results": orphan,
        "preempted_wakes": preempted,
    }


def by_version(rows: list[Row], since: Optional[float]) -> dict[str, dict[str, Any]]:
    """claude のバージョンごとの、作業を待つ Stop のうち取り合いか到着による失効で起床させられなくなった数と率。"""
    picked = [r for r in rows if r.get("kind") in STOP_KINDS and (since is None or (row_seconds(r.get("at")) or -math.inf) >= since)]

    def keys(kind: str, cond: Callable[[Row], bool]) -> set:
        return {(r.get("session_id"), r["message_id"]) for r in picked if r["kind"] == kind and r.get("message_id") is not None and cond(r)}

    contested, revoked = keys("contest", lambda r: True), keys("arrival", lambda r: r.get("revoked") is True)
    out: dict[str, dict[str, Any]] = {}
    for r in picked:
        v = out.setdefault(
            r.get("claude_version") or "unknown",
            dict.fromkeys(("contests", "arrivals", "work_stops", "contested_work_stops", "arrival_work_stops"), 0),
        )
        kind, key, works = r["kind"], (r.get("session_id"), r.get("message_id")), r.get("works")
        v["contests"] += kind == "contest"
        v["arrivals"] += kind == "arrival"
        if kind != "work_stop" and not (isinstance(works, int) and works >= 1):
            continue
        v["work_stops"] += 1
        if kind == "contest" or kind == "work_stop" and key in contested:
            v["contested_work_stops"] += 1
        elif kind == "arrival" or kind == "work_stop" and key in revoked:
            v["arrival_work_stops"] += 1
    for v in out.values():
        lost = v["contested_work_stops"] + v["arrival_work_stops"]
        v.update(lost_work_stops=lost, lost_ratio=lost / v["work_stops"] if v["work_stops"] else None)
    return out


def report(rows: Iterable[Row], since: Optional[float] = None) -> dict[str, Any]:
    """起床の記録の行を集計する。`since` があれば、`wait_start` がそれ以後の待ちと、`at` がそれ以後の Stop の行だけを数える。"""
    rows = list(rows)
    waits, voided, partial = _split(_by_wait(rows), since)
    lengths = [n for n in map(_length, waits) if n is not None]
    unpriced = [w for w in waits if _unpriced(w)]
    avoided = [a for a in (_avoided(w) for w in waits if not _unpriced(w)) if a is not None]
    return {
        "waits": len(waits),
        "limit_stopped_waits": sum(
            1 for w in waits if any(r.get("scope") == "wait" and r.get("reason") == LIMIT for r in w["stop_waking"])
        ),
        "unmatched_stopped_waits": sum(1 for w in waits if any(r.get("outcome") == "unknown" for r in w["wake_result"])),
        **_wakes(waits),
        "wait_seconds": {f"p{int(q * 100)}": quantile(lengths, q) for q in (0.5, 0.75, 0.9)},
        "estimated_avoided_rewrites": len(avoided),
        "estimated_avoided_tokens": sum(a[0] for a in avoided),
        "estimated_avoided_cost": sum(a[1] for a in avoided),
        "voided_waits": voided,
        "partial_waits": partial,
        "unpriced_waits": len(unpriced),
        "by_version": by_version(rows, since),
    }


def cache_summary(got: Mapping[str, Any]) -> str:
    return (
        f"待ち {got['waits']} 件・起床 {got['wakes']} 回・避けた書き直し（推定）{got['estimated_avoided_rewrites']} 回"
        f"（費用の差の推定 {got['estimated_avoided_cost']:.0f}）・費用の分からない待ち {got['unpriced_waits']} 件"
    )


def read_logs(root: Path) -> tuple[list[dict[str, Any]], int, int]:
    """全ラッパーの起床の記録の行（種類と時刻の読める行だけ）・読めずに飛ばした行の数・読んだ記録の数。"""
    rows: list[dict[str, Any]] = []
    skipped = 0
    paths = sorted(root.glob(f"*/{signals.CACHE_LOG}")) if root.is_dir() else []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                skipped += 1
                continue
            if isinstance(row, dict) and isinstance(row.get("kind"), str) and row_seconds(row.get("at")) is not None:
                rows.append(row)
            else:
                skipped += 1
    return rows, skipped, len(paths)


def _since(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    at = row_seconds(text)
    if at is None:
        raise ValueError(f"--since は ISO 8601 の日付か時刻: {text}")
    return at


def run(args: argparse.Namespace) -> int:
    try:
        since = _since(args.since)
    except ValueError as exc:
        return result.emit(result.stopped(TOOL, "invalid", str(exc)))
    root = registry.wrappers_dir(identity.load().state_dir)
    try:
        rows, skipped, logs = read_logs(root)
    except OSError as exc:
        return result.emit(result.stopped(TOOL, "unknown", f"起床の記録を読めない（{root}）: {exc}"))
    got = report(rows, since)
    return result.emit(result.result(TOOL, result.OK, cache_summary(got), items=[], metrics={**got, "skipped": skipped, "logs": logs}))


CACHE_HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {"report": run}


def parser(sub: Any, help_text: str) -> None:
    """`cache` のサブコマンドの parser を `sub` へ足す（`help_text` は `ndf relay` の一覧の説明）。"""
    cache = sub.add_parser(
        "cache", help=help_text, description="サブコマンド: " + " / ".join(f"{k}（{v}）" for k, v in CACHE_COMMANDS.items())
    )
    cache_sub = cache.add_subparsers(dest="cache_action", required=True)
    p = {name: cache_sub.add_parser(name, help=text, description=text) for name, text in CACHE_COMMANDS.items()}
    p["report"].add_argument("--since", help="この日時（ISO 8601）以後に始まった待ちと、以後の Stop の行だけを集計する")
