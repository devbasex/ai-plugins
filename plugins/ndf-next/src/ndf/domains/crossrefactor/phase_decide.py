"""工程の起点の記録と、担当が結果を出さずに終わったときの続け方（#3313）。

コードを書く工程（テストの追加・実装）は、担当を起動する前に起点（`base_sha`）を工程の記録へ書く。担当が結果を出さずに
終わったときは、起点から今の head までを取り消して担当を振り替えるか、振り替える担当が無ければその項目を見送って続ける。
起点の無い記録（起点を書く前の版が書いた記録）でも止まらず、範囲を確定できない変更は採らずに項目を見送る。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

BASE_KEY = "base_sha"
REASSIGN = "reassign"
SKIP = "skip_item"


def start_phase(phases: Mapping[str, Any], phase: str, head: str, started_at: str) -> dict[str, Any]:
    """担当を起動する前の工程の記録。起点を今の head にして、前の試行の記録を置き換える。"""
    out = dict(phases)
    out[phase] = {BASE_KEY: head, "started_at": started_at}
    return out


@dataclass(frozen=True)
class NoResult:
    """結果を出さずに終わった担当の後の続け方。`kind` は `reassign`（取り消して振り替える）か `skip_item`（項目を見送る）。

    `undo` は取り消す範囲 (起点, head)。範囲を確定できなければ None で、何も取り消さない。
    """

    kind: str
    undo: tuple[str, str] | None
    reason: str


def on_no_result(record: Mapping[str, Any] | None, head: str, *, spare: bool) -> NoResult:
    """工程の記録と今の head から続け方を決める。`spare` は振り替えられる担当が残っているか。"""
    base = record[BASE_KEY] if record is not None and BASE_KEY in record else None
    if not base:
        return NoResult(SKIP, None, "工程の起点が記録されておらず範囲を確定できないため、変更を採らずに項目を見送る")
    undo = (base, head) if base != head else None
    if spare:
        return NoResult(REASSIGN, undo, "担当が結果を出さずに終わったため、起点まで取り消して担当を振り替える")
    return NoResult(SKIP, undo, "担当が結果を出さずに終わり、振り替える担当が残っていないため、起点まで取り消して項目を見送る")
