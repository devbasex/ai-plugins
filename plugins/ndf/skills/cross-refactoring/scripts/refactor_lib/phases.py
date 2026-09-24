"""フェーズの所要の記録（#933 の決定 8）。

**進行側の時計で測る。** 開始は `start-phase` が CLI の起動の直前に、終わりは各
取り込み（`merge-*`）と `verify` が書く。担当の申告（`elapsed_seconds`）は使わない
（#917 では申告と監視の実測が食い違った）。
"""
from __future__ import annotations

from typing import Any, Optional

import statefile

from . import clock


def finish_phase(state: dict[str, Any], name: str, started: Optional[str] = None) -> None:
    """フェーズの終わりを書き、所要を秒で残す。**書き済みなら書き換えない。**

    開始の記録が無い（`start-phase` を通らずに取り込んだ）ときは `started` を、それも
    無ければ実行の開始を起点にする。所要が読めないより、長めに数える方が見積りを
    楽観側へ倒さない。
    """
    record = state.setdefault("phases", {}).setdefault(name, {})
    if record.get("ended_at"):
        return
    now = statefile.now()
    start = record.get("started_at") or started or state.get("started_at")
    record.setdefault("started_at", start)
    record["ended_at"] = now
    seconds = clock.seconds_between(start, now)
    record["seconds"] = None if seconds is None else round(max(seconds, 0.0), 1)


def add_phase_seconds(state: dict[str, Any], name: str, seconds: float) -> None:
    """繰り返すフェーズ（検証・修正）の所要を足し込む。終わりの時刻は最後の 1 回で上書きする。"""
    record = state.setdefault("phases", {}).setdefault(name, {})
    now = statefile.now()
    record.setdefault("started_at", now)
    record["ended_at"] = now
    record["seconds"] = round(float(record.get("seconds") or 0.0) + max(seconds, 0.0), 1)


def elapsed_minutes(state: dict[str, Any]) -> float:
    """実行の開始からの経過（分）。"""
    seconds = clock.seconds_between(state.get("started_at"), clock.now())
    return 0.0 if seconds is None else max(seconds, 0.0) / 60.0
