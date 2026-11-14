"""ラウンドを始める前の、修正の記録の要否の判断（#2770 の形 1。純粋な関数）。

前のラウンドが修正必須の判定（`changes_required`）なら、次のラウンドの前に修正の記録が要る。記録が無くても、
修正担当の結果ファイルが残っていれば止めずに取り込む（駆動の外でコミットを積んだ後の打ち直しで、結果ファイルを
読み飛ばして止まらない）。記録も結果ファイルも無ければ、取り込む 1 行を示して止める。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple, Optional

CHANGES_REQUIRED = "changes_required"
# 答えの種類
OK = "ok"
INGEST = "ingest_pending"
STOP = "no_fix_record"


class FixGate(NamedTuple):
    """`kind` は `OK`・`INGEST`（`pending` を取り込む）・`STOP`（`reason` で止める）のどれか。"""

    kind: str
    pending: Optional[str]
    reason: Optional[str]


def require_fix_for_changes(round_no: int, verdict: Optional[str], fix: Optional[Mapping[str, Any]], pending: Optional[str]) -> FixGate:
    """`round_no` は前のラウンドの番号、`verdict` はその判定、`fix` は修正の記録、`pending` は残った結果ファイルのパス。"""
    if verdict != CHANGES_REQUIRED or fix:
        return FixGate(OK, None, None)
    if pending:
        return FixGate(INGEST, pending, None)
    return FixGate(STOP, None, f"round {round_no} は修正必須の判定だが、修正の記録も修正の結果ファイルも無い")
