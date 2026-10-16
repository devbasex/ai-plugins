"""起動の失敗で止める 1 か所（#1483 E7・I8）。

着手前・項目の範囲テスト・テスト整備ラウンド・最終ゲートの手順は、テストのコマンドの結果を
`test_strategy.outcome` で判別し、起動の失敗（`launch_failed`）ならここを呼ぶ。記録を状態ファイルの
`launch_failure` へ書き、報告の 1 行を出し、中断（`ABORT` = 4）で止まる。項目の状態は変えず、
取り消しも次の項目の適用も行わない。
"""

from __future__ import annotations

import pathlib
from typing import Any, NoReturn, Optional

import statefile
import test_strategy as ts

from . import ABORT, clock, info

PHASES = {
    "baseline": "着手前のテスト",
    "verify": "項目の範囲テスト",
    "tests": "テスト整備ラウンド",
    "whole": "危険フラグの全体テスト",
    "gate": "最終ゲート",
    "prepush": "公開前の静的解析",
}


def line(record: dict[str, Any]) -> str:
    """報告の 1 行。"""
    phase = PHASES.get(str(record.get("phase")), str(record.get("phase")))
    return (
        f"⛔ 起動の失敗（{phase}）: {record.get('command')} — {record.get('reason')}。"
        f"ログ: {record.get('log') or '—'}。項目は取り消さずに止めました"
    )


def stop(
    path: Optional[pathlib.Path],
    state: dict[str, Any],
    phase: str,
    command: Any,
    result: ts.Outcome,
    log: Optional[pathlib.Path] = None,
) -> NoReturn:
    """起動の失敗を記録して止まる。`path` が `None`（状態ファイルがまだ無い）なら書かずに止まる。"""
    record = {
        "phase": phase,
        "command": command if isinstance(command, str) else " ".join(str(w) for w in command or []),
        "code": result.code,
        "reason": result.reason,
        "log": str(log) if log is not None else None,
        "at": clock.iso(clock.now()),
    }
    state["launch_failure"] = record
    if path is not None:
        statefile.save(path, state)
    info(line(record))
    raise SystemExit(ABORT)
