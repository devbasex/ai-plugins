"""止まっていた時間を測り、まだ来ていない締め切りをその分だけ後ろへずらす（#1491・#1743 の F3）。

想定最大時間は cross-refactoring が動いていた時間で数える。リファクタリング計画の後に中断して再開したとき、
止まっていた時間（再開の時刻 − 最後の動きの時刻）が余裕（`0.05·B`）を超えれば、上限の表のうち最後の動きの
時刻より後に来る締め切りだけをずらす（決定 4）。前に来る締め切りは開き直さない。

最後の動きの時刻は、作業ディレクトリの直下のファイルのうち最も新しい更新時刻である（決定 3）。状態ファイル・
CLI とテストのログ・結果ファイルのほかに、待つ側が 15 秒ごとに更新する心拍のファイル（`paths.alive_path`）を含む。

ファイルの更新時刻を読むのは `last_activity` だけで、`catch_up` は時刻を引数で受ける純粋な処理である。
"""

from __future__ import annotations

import datetime as _dt
import pathlib
from typing import Any, Optional

from . import clock, info, timeline

# ずらす上限の表のキー（I4）。`propose_end_at`・`plan_end_at` は計画の後には過ぎているため含めない。
SHIFT_KEYS = ("add_tests_end_at", "implement_end_at", "fix_end_at", "final_end_at", "stop_revert_end_at")

SHIFTED = "shifted"
WITHIN_MARGIN = "within_margin"
LEGACY = "legacy"


def empty() -> dict[str, Any]:
    """`merge-plan` が作る空の記録。"""
    return {"seconds": 0, "shifted_seconds": 0, "legacy": False, "events": []}


def last_activity(tmp_dir: Any) -> Optional[_dt.datetime]:
    """作業ディレクトリの直下のファイルのうち、最も新しい更新時刻（UTC）。ファイルが無ければ `None`。"""
    newest: Optional[float] = None
    try:
        entries = list(pathlib.Path(tmp_dir).iterdir())
    except OSError:
        return None
    for entry in entries:
        try:
            if not entry.is_file():
                continue
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        newest = mtime if newest is None else max(newest, mtime)
    if newest is None:
        return None
    return _dt.datetime.fromtimestamp(newest, tz=_dt.timezone.utc)


def _event(now: _dt.datetime, last_at: Optional[_dt.datetime], seconds: float, shifted: bool, reason: str) -> dict[str, Any]:
    return {
        "at": clock.iso(now),
        "last_activity_at": clock.iso(last_at) if last_at is not None else None,
        "seconds": round(seconds, 1),
        "shifted": shifted,
        "reason": reason,
    }


def catch_up(state: dict[str, Any], now: _dt.datetime, last_at: Optional[_dt.datetime]) -> Optional[dict[str, Any]]:
    """止まっていた時間を測り、状態の辞書を書き換えて、記録した 1 件を返す。記録しなければ `None`。

    - 計画の前（`plan` が無い）と最終ゲートに入った後（`final_gate` がある）は記録しない（I7）
    - `pause` を持たない状態（止まっていた時間を記録しない版で計画した）はずらさず、`legacy` を 1 度だけ残す（I6）
    - 止まっていた時間が余裕（`0.05·B`）以下ならずらさず、`within_margin` を残す（I5）
    - それより長ければ、最後の動きの時刻より後に来る上限の表の値をずらし、`shifted` を残す（I4）
    """
    if not state.get("plan") or state.get("final_gate"):
        return None
    record = state.get("pause")
    if not isinstance(record, dict):
        record = state["pause"] = {**empty(), "legacy": True}
        event = _event(now, last_at, 0.0, False, LEGACY)
        record["events"].append(event)
        return event
    if record.get("legacy") or last_at is None:
        return None
    seconds = max(0.0, (now - last_at).total_seconds())
    record["seconds"] = round(float(record.get("seconds") or 0.0) + seconds, 1)
    if seconds <= timeline.margin(int(state["budget_minutes"])):
        event = _event(now, last_at, seconds, False, WITHIN_MARGIN)
    else:
        limits = state.get("limits") or {}
        delta = _dt.timedelta(seconds=seconds)
        for key in SHIFT_KEYS:
            value = clock.parse(limits.get(key))
            if value is not None and value > last_at:
                limits[key] = clock.iso(value + delta)
        record["shifted_seconds"] = round(float(record.get("shifted_seconds") or 0.0) + seconds, 1)
        event = _event(now, last_at, seconds, True, SHIFTED)
    record.setdefault("events", []).append(event)
    return event


def event_line(event: dict[str, Any]) -> str:
    """記録した 1 件を知らせる 1 行。"""
    minutes = float(event.get("seconds") or 0.0) / 60
    if event.get("reason") == LEGACY:
        return "止まっていた時間: 計測しない（この状態ファイルは止まっていた時間を記録しない版でリファクタリング計画を取り込んだため、締め切りをずらさずに続けた）"
    if event.get("shifted"):
        return f"止まっていた時間: {minutes:.1f} 分（締め切りを {minutes:.1f} 分ずらした）"
    return f"止まっていた時間: {minutes:.1f} 分（余裕以下のため締め切りをずらさなかった）"


def report_line(state: dict[str, Any]) -> Optional[str]:
    """報告の 1 行。`pause.events[]` が無ければ `None`。"""
    record = state.get("pause") or {}
    events = record.get("events") or []
    if not events:
        return None
    if record.get("legacy"):
        return event_line({"reason": LEGACY})
    shifted = [e for e in events if e.get("shifted")]
    total = float(record.get("seconds") or 0.0) / 60
    moved = float(record.get("shifted_seconds") or 0.0) / 60
    if shifted:
        return f"止まっていた時間: {total:.1f} 分（{len(shifted)} 回。締め切りを {moved:.1f} 分ずらした）"
    return f"止まっていた時間: {total:.1f} 分（余裕以下のため締め切りをずらさなかった）"


def shifted_seconds(state: dict[str, Any]) -> float:
    """ずらした秒の和。所要から引く（決定 8）。"""
    return float((state.get("pause") or {}).get("shifted_seconds") or 0.0)


def resume(state_file: pathlib.Path, state: dict[str, Any]) -> Optional[dict[str, Any]]:
    """再開の入口（`init` の再開と `catch-up`）。最後の動きの時刻を読んでずらし、記録した 1 件を知らせて返す。

    **状態を書く前に呼ぶ。** 書いた後だと状態ファイルの更新時刻が最後の動きの時刻になる。保存は呼ぶ側が行う。
    """
    event = catch_up(state, clock.now(), last_activity(pathlib.Path(state_file).parent))
    if event is not None:
        info(f"↻ {event_line(event)}")
    return event
