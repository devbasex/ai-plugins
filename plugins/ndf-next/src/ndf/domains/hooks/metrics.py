"""hook の計測（#3345 の決定 94・IH9）。

エントリポイントは起動ごとに `<状態の置き場>/hooks/<日付>.jsonl` へ 1 行を 1 回の追記（`O_APPEND`）で書く。書けなくても
判定は変えない。古い日のファイルを消すのは SessionStart のエントリポイントだけ（`prune`）で、ほかの事象ではディレクトリを
走査しない。`stats` は記録から事象・guard ごとの p50・p95・拒否と差し戻しの数・注入の最大を返す。
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from ndf.domains.hooks import tables

# 判定のうち、数えるもの（stats の欄の名前）
_COUNTED = {"deny": "denies", "pushback": "pushbacks", "limited": "limited"}


def keep_days() -> float:
    """計測を残す日数（`hook.metrics-keep` の秒を日へ換算）。prune と stats の窓の既定。"""
    return float(tables.policy_value("hook.metrics-keep", "seconds")) / 86400


def metrics_dir(state_dir: Path) -> Path:
    return state_dir / "hooks"


def _day_file(state_dir: Path, now: datetime) -> Path:
    return metrics_dir(state_dir) / f"{now.astimezone(timezone.utc).strftime('%Y-%m-%d')}.jsonl"


def write_line(state_dir: Path, record: dict[str, Any], now: Optional[datetime] = None) -> bool:
    """1 行を追記する。書けたら真。失敗は捨てる（判定を変えない）。"""
    at = now or datetime.now(timezone.utc)
    line = json.dumps({"at": at.isoformat(timespec="milliseconds"), **record}, ensure_ascii=False) + "\n"
    path = _day_file(state_dir, at)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    except OSError:  # noqa: NDF401 ファイル: 計測を書けなくても判定は変えない（IH9）
        return False
    try:
        os.write(fd, line.encode("utf-8"))
    except OSError:  # noqa: NDF401 ファイル: 計測を書けなくても判定は変えない（IH9）
        return False
    finally:
        os.close(fd)
    return True


def prune(state_dir: Path, days: float, now: Optional[datetime] = None) -> int:
    """`days` 日より古い日のファイルを消し、消した数を返す。SessionStart のエントリポイントだけが呼ぶ。"""
    cutoff = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - timedelta(days=days)
    removed = 0
    for path in sorted(metrics_dir(state_dir).glob("*.jsonl")):
        day = _day_of(path)
        if day is not None and day + timedelta(days=1) <= cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def _day_of(path: Path) -> Optional[datetime]:
    try:
        return datetime.strptime(path.stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:  # noqa: NDF401 ファイル: 日付の名前でないファイルは計測の記録でない
        return None


def read_records(state_dir: Path, days: float, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """`days` 日の中の記録。壊れた行は飛ばす。"""
    cutoff = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - timedelta(days=days)
    records = []
    for path in sorted(metrics_dir(state_dir).glob("*.jsonl")):
        day = _day_of(path)
        if day is None or day + timedelta(days=1) <= cutoff:
            continue
        records += [r for r in map(tables.json_object, path.read_text(encoding="utf-8").splitlines()) if r is not None]
    return records


def percentile(values: Iterable[float], q: float) -> Optional[float]:
    """最近傍の順位の百分位（q は 0〜100）。値が無ければ None。"""
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    """事象ごとと guard ごとの p50・p95（ミリ秒）・拒否と差し戻しの数・注入の最大の字数。"""
    events: dict[str, list[dict[str, Any]]] = {}
    guards: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        events.setdefault(f"{rec.get('runtime')}:{rec.get('event')}", []).append(rec)
        for g in rec.get("guards") or ():  # noqa: NDF501 ファイル: guard を呼ばなかった行は空
            guards.setdefault(str(g.get("name")), []).append({"total_ms": g.get("ms"), "verdict": g.get("kind")})
    return {
        "events": {k: _summary(v, inject=True) for k, v in sorted(events.items())},
        "guards": {k: _summary(v, inject=False) for k, v in sorted(guards.items())},
    }


def _summary(rows: list[dict[str, Any]], inject: bool) -> dict[str, Any]:
    times = [float(r["total_ms"]) for r in rows if isinstance(r.get("total_ms"), (int, float))]
    out: dict[str, Any] = {"count": len(rows), "p50_ms": percentile(times, 50), "p95_ms": percentile(times, 95)}
    for kind, col in _COUNTED.items():
        out[col] = sum(1 for r in rows if r.get("verdict") == kind)
    if inject:
        out["inject_max"] = max((int(r.get("inject_chars") or 0) for r in rows), default=0)  # noqa: NDF501 ファイル: 注入の無い行は 0
    return out
