"""時刻の読み書き（#933）。

状態ファイルの時刻（`statefile.now`）はタイムゾーンを持たず、コミットの時刻
（`git log --format=%cI`）は持つ。**比べる前に両方をタイムゾーン付きへ揃える。**
付けないまま比べると `TypeError` になる（`run_metrics._parse_time` と同じ規則）。
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Optional


def parse(value: Any) -> Optional[_dt.datetime]:
    """ISO 8601 を読む。タイムゾーンの無い時刻はこの機械の地方時として付ける。"""
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.astimezone()
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = _dt.datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed.astimezone() if parsed.tzinfo is None else parsed


def now() -> _dt.datetime:
    """今（タイムゾーン付き）。"""
    return _dt.datetime.now().astimezone()


def iso(value: _dt.datetime) -> str:
    """状態ファイルへ書く形（タイムゾーン付き、秒まで）。"""
    return value.isoformat(timespec="seconds")


def seconds_between(start: Any, end: Any) -> Optional[float]:
    """`end − start` の秒。どちらかが読めなければ `None`。"""
    s, e = parse(start), parse(end)
    if s is None or e is None:
        return None
    return (e - s).total_seconds()
