"""起動の部品へ差し込む、値を選ぶ処理の口（#2944）。

`claude_choice.LaunchChooser` を満たす。値の選択と基準の組への切り替えは `model_choice` のものをそのまま使い、
割り付けを受けた起動が返した実際のモデルの行（`observed`）だけをここで書く。一番上のエントリポイントがこのモジュールを
`claude_choice.set_launch_chooser` で差し込む。
"""

from __future__ import annotations

import logging
from datetime import timedelta

from ndf.adapters import model_choice
from ndf.adapters.model_choice import base_fallback, launch_choice

__all__ = ["base_fallback", "launch_choice", "observed"]

log = logging.getLogger(__name__)


def observed(choice: model_choice.Choice, model: str) -> None:
    """割り付けを受けた単位の、引き上げでも基準への切り替えでもない起動が返した実際のモデルを、割り付け ID ごとに最初の 1 回だけ足す。

    読むのは割り付けの期間の上限の日数で、掃除が消した月のファイルは無いとして読む。書けなければ警告を出して続ける。
    """
    if choice.assign_id is None or choice.arm is None or choice.raised:
        return
    now = model_choice.utc_now()
    row = {"kind": "observed", "at": now.isoformat(), "assign_id": choice.assign_id, "arm": dict(choice.arm)}
    try:
        with model_choice.ledger(now - timedelta(days=model_choice.LONGEST_WINDOW), choice.store) as led:
            if not any(r["kind"] == "observed" and r["assign_id"] == choice.assign_id for r in led.rows):
                led.append({**row, "model": model})
    except model_choice.STORE_ERRORS as exc:
        log.warning("実験の記録へ実際のモデルを書けない（割り付け %s）: %s", choice.assign_id, exc)
