"""工程の起点の記録と、担当が結果を出さずに終わったときの続け方（#3313）。

コードを書く工程（テストの追加・実装）は、担当を起動する前に起点（`base_sha`）を工程の記録へ書く。担当が結果を出さずに
終わったときは、その項目を見送って続ける（コードを書くのは修正担当だけで、振り替える担当は無い）。
起点の無い記録（起点を書く前の版が書いた記録）でも止まらず、範囲を確定できない変更は採らずに項目を見送る。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

BASE_KEY = "base_sha"


def start_phase(phases: Mapping[str, Any], phase: str, head: str, started_at: str) -> dict[str, Any]:
    """担当を起動する前の工程の記録。起点を今の head にして、前の試行の記録を置き換える。"""
    out = dict(phases)
    out[phase] = {BASE_KEY: head, "started_at": started_at}
    return out


def on_no_result(record: Mapping[str, Any] | None) -> str:
    """担当が結果を出さずに終わった後、項目を見送る理由。工程の記録に起点が在るかで変わる。"""
    base = record[BASE_KEY] if record is not None and BASE_KEY in record else None
    if not base:
        return "工程の起点が記録されておらず範囲を確定できないため、変更を採らずに項目を見送る"
    return "担当が結果を出さずに終わり、振り替える担当が残っていないため、起点まで取り消して項目を見送る"
