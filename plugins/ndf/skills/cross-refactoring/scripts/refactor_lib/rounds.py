"""採り直しの巡をまたぐ記録の読み出し（#1743 決定 10）。

`readopt` は採るたびに前の巡の全体テストの記録を `whole_test_history[]` へ移す。最終ゲート・報告・実行の行は、
ここで全巡の記録を合わせて読む。どのモジュールも import しない（読み出すだけの葉のモジュール）。
"""

from __future__ import annotations

from typing import Any


def whole_records(state: dict[str, Any]) -> list[dict[str, Any]]:
    """検証の中の全体テストの記録。前の巡（`whole_test_history`）から今の巡の順。"""
    records = [entry.get("record") or {} for entry in state.get("whole_test_history") or []]
    return [*records, state.get("whole_test") or {}]


def deferred_union(state: dict[str, Any]) -> dict[str, list[str]]:
    """CI へ寄せた危険フラグを、前の巡（`whole_test_history`）と今の巡（`whole_test`）から巡の順に重複なく合わせる（#1743）。

    採り直しが前の巡の記録を履歴へ移しても、最終ゲートの取り消しと報告は 1 巡目に寄せた項目を読む。寄せていなければ空。
    """
    flags: list[str] = []
    items: list[str] = []
    for record in whole_records(state):
        deferred = record.get("deferred") or {}
        flags.extend(f for f in deferred.get("flags") or [] if f not in flags)
        items.extend(i for i in deferred.get("items") or [] if i not in items)
    return {"flags": sorted(flags), "items": items} if (flags or items) else {}
