"""止まっていた時間を測り、まだ来ていない締め切りをその分だけ後ろへずらす（#1491・#1743 の F3）。

想定最大時間は cross-refactoring が動いていた時間で数える。中断して再開したとき、止まっていた時間（再開の時刻 −
最後の動きの時刻）が余裕（`0.05·B`）を超えれば、上限の表のうち最後の動きの時刻より後に来る締め切りだけをずらす
（決定 4）。前に来る締め切りは開き直さない。リファクタリング計画の前の中断も同じく測り、提案と計画の枠の締め切りも
ずらす（#1944）。ずらした秒の和は、計画の経過時間（`phases.elapsed_minutes`）と上限の表の起点（`timeline.window_offset`）
から引く。

最後の動きの時刻は、作業ディレクトリの直下のファイルのうち最も新しい更新時刻である（決定 3）。状態ファイル・
CLI とテストのログ・結果ファイルのほかに、待つ側が 15 秒ごとに更新する心拍のファイル（`paths.alive_path`）を含む。

ファイルの更新時刻を読むのは `last_activity` だけで、`shift_for_pause` は時刻を引数で受ける純粋な処理である。
"""

from __future__ import annotations

import datetime as _dt
import pathlib
from typing import Any, Optional

from . import clock, info, timeline

# ずらす上限の表のキー（I4）。`propose_end_at`・`plan_end_at` は計画の後には過ぎているため含めない。
SHIFT_KEYS = ("add_tests_end_at", "implement_end_at", "fix_end_at", "final_end_at")
# 計画の前の中断でずらすキー（#1944 の I6）。提案と計画の枠の締め切りを足す。
PRE_PLAN_SHIFT_KEYS = ("propose_end_at", "plan_end_at", *SHIFT_KEYS)

SHIFTED = "shifted"
WITHIN_MARGIN = "within_margin"
LEGACY = "legacy"


def empty() -> dict[str, Any]:
    """`init` が新しい状態に置く空の記録（`merge-plan` は在れば引き継ぐ）。"""
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


def _after_final_gate(state: dict[str, Any]) -> bool:
    """最終ゲートに入った後か。`final_gate` は `init` が空の記録（`fix_rounds`・`checks`）で置くため、在るだけでは決めない。"""
    from .ledger import FINAL_PHASES  # ledger → gitfacts → … の読み込みを、時間の計算の側へ持ち込まない

    return state.get("phase") in FINAL_PHASES or bool((state.get("final_gate") or {}).get("status"))


def shift_for_pause(state: dict[str, Any], now: _dt.datetime, last_at: Optional[_dt.datetime]) -> Optional[dict[str, Any]]:
    """止まっていた時間を測り、状態の辞書を書き換えて、記録した 1 件を返す。記録しなければ `None`。

    - 最終ゲートに入った後（手順が `final`・`done` か、`final_gate` に `status` がある）は記録しない（I7）
    - 計画の前で、枠が収まらずに止めた後の打ち直し（`window_stopped_at` がある）は記録しない。起点を打ち直した
      時刻へ移す扱い（`resumed_at`）が止まっていた時間を含む
    - 計画の前で `pause` を持たない状態（この扱いより前の版で作った）は、空の記録を作って測る（#1944）
    - 計画の後で `pause` を持たない状態（止まっていた時間を記録しない版で計画した）はずらさず、`legacy` を 1 度だけ残す（I6）
    - 止まっていた時間が余裕（`0.05·B`）以下ならずらさず、`within_margin` を残す（I5）
    - それより長ければ、最後の動きの時刻より後に来る上限の表の値をずらし、`shifted` を残す（I4）
    """
    if _after_final_gate(state):
        return None
    planned = bool(state.get("plan"))
    if not planned and state.get("window_stopped_at"):
        return None
    record, event = _ensure_record(state, planned, now, last_at)
    if event is not None:
        return event
    if record.get("legacy") or last_at is None:
        return None
    seconds = max(0.0, (now - last_at).total_seconds())
    _add_seconds(record, "seconds", seconds)
    if seconds <= timeline.margin(int(state["budget_minutes"])):
        event = _event(now, last_at, seconds, False, WITHIN_MARGIN)
    else:
        keys = SHIFT_KEYS if planned else PRE_PLAN_SHIFT_KEYS
        _shift_limits(state.get("limits") or {}, keys, last_at, _dt.timedelta(seconds=seconds))
        _add_seconds(record, "shifted_seconds", seconds)
        event = _event(now, last_at, seconds, True, SHIFTED)
    record.setdefault("events", []).append(event)
    return event


def _ensure_record(
    state: dict[str, Any], planned: bool, now: _dt.datetime, last_at: Optional[_dt.datetime]
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """`pause` の記録と、記録が無かった計画の後の状態に残した `legacy` の 1 件（それ以外は `None`）。
    計画の前で記録が無ければ空の記録を作る。"""
    record = state.get("pause")
    if isinstance(record, dict):
        return record, None
    if not planned:
        record = state["pause"] = empty()
        return record, None
    record = state["pause"] = {**empty(), "legacy": True}
    event = _event(now, last_at, 0.0, False, LEGACY)
    record["events"].append(event)
    return record, event


def _add_seconds(record: dict[str, Any], key: str, seconds: float) -> None:
    record[key] = round(float(record.get(key) or 0.0) + seconds, 1)


def _shift_limits(limits: dict[str, Any], keys: tuple[str, ...], last_at: _dt.datetime, delta: _dt.timedelta) -> None:
    """上限の表のうち、最後の動きの時刻より後に来る締め切りを delta だけ後ろへずらす。"""
    for key in keys:
        value = clock.parse(limits.get(key))
        if value is not None and value > last_at:
            limits[key] = clock.iso(value + delta)


def event_line(event: dict[str, Any]) -> str:
    """記録した 1 件を知らせる 1 行。"""
    minutes = float(event.get("seconds") or 0.0) / 60
    if event.get("reason") == LEGACY:
        return "止まっていた時間: 計測しない（この状態ファイルは止まっていた時間を記録しない版でリファクタリング計画を取り込んだため、締め切りをずらさずに続けた）"
    if event.get("shifted"):
        return f"止まっていた時間: {minutes:.1f} 分（締め切りを {minutes:.1f} 分ずらした）"
    return f"止まっていた時間: {minutes:.1f} 分（余裕以下のため締め切りをずらさなかった）"


def pause_report_line(state: dict[str, Any]) -> Optional[str]:
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
    """ずらした秒の和。所要（決定 8）・計画の経過時間・上限の表の起点から引く（#1944）。"""
    return float((state.get("pause") or {}).get("shifted_seconds") or 0.0)


def resume_after_pause(state_file: pathlib.Path, state: dict[str, Any]) -> Optional[dict[str, Any]]:
    """再開の入口（`init` の再開と `catch-up`）。最後の動きの時刻を読んでずらし、記録した 1 件を知らせて返す。

    **状態を書く前に呼ぶ。** 書いた後だと状態ファイルの更新時刻が最後の動きの時刻になる。保存は呼ぶ側が行う。
    """
    event = shift_for_pause(state, clock.now(), last_activity(pathlib.Path(state_file).parent))
    if event is not None:
        info(f"↻ {event_line(event)}")
    return event
