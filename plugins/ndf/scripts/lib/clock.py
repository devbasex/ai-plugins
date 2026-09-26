"""時刻の読み書き（#1142 の L0）。標準ライブラリだけを import する（ラッパーのバージョンディレクトリにも入る）。

スクリプトごとに違っていた時刻の書き出しの形を、`iso` と `now_iso` の `form` で選ぶ。

| form | 例 | 使っていたところ |
| --- | --- | --- |
| `local` | `2026-09-26T16:00:00+09:00` | `supervise.py`・cross-review の `state.py`・`monitor_outcome.py` |
| `utc` | `2026-09-26T07:00:00+00:00` | `check-trigger.py`・`mvv-gate.py` |
| `naive` | `2026-09-26T16:00:00` | `statefile.now`（タイムゾーンを持たない地方時） |
| `z-ms` | `2026-09-26T07:00:00.123Z` | `relay.py` の `log.jsonl` |

読み取り（`parse`）はどの形も受ける。Python 3.10 の `fromisoformat` は `Z` を読めないため、先に `+00:00` へ置き換える。
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Optional

FORMS = ("local", "utc", "naive", "z-ms")


def now(utc: bool = False) -> _dt.datetime:
    """今（タイムゾーン付き）。`utc` なら UTC、そうでなければこの機械の地方時。"""
    return _dt.datetime.now(_dt.timezone.utc) if utc else _dt.datetime.now().astimezone()


def iso(value: _dt.datetime, form: str = "local") -> str:
    """`value` を `form` の形で書き出す（`z-ms` はミリ秒まで、ほかは秒まで）。"""
    if form not in FORMS:
        raise ValueError(f"時刻の形は {'/'.join(FORMS)} のどれか: {form}")
    if value.tzinfo is None:
        value = value.astimezone()
    if form == "naive":
        return value.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    if form == "local":
        return value.astimezone().isoformat(timespec="seconds")
    value = value.astimezone(_dt.timezone.utc)
    if form == "utc":
        return value.isoformat(timespec="seconds")
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def now_iso(form: str = "local") -> str:
    """今を `form` の形で書き出す。"""
    return iso(now(), form)


def parse(value: Any, naive: str = "local") -> Optional[_dt.datetime]:
    """ISO 8601（`Z` を含む）を読む。読めなければ `None`。

    タイムゾーンの無い時刻は `naive` で扱いを選ぶ: `local` は地方時として付け、`utc` は UTC として付け、
    `reject` は `None` を返す。付けないまま比べるとタイムゾーン付きの時刻との引き算が `TypeError` になる。
    """
    if isinstance(value, _dt.datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = _dt.datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is not None:
        return parsed
    if naive == "local":
        return parsed.astimezone()
    if naive == "utc":
        return parsed.replace(tzinfo=_dt.timezone.utc)
    return None


def seconds_between(start: Any, end: Any, naive: str = "local") -> Optional[float]:
    """`end − start` の秒。どちらかが読めなければ `None`。"""
    s, e = parse(start, naive), parse(end, naive)
    if s is None or e is None:
        return None
    return (e - s).total_seconds()
