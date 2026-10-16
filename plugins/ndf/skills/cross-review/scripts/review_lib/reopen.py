"""ラウンドを足す（#1340 の決定 1・4・5・6）。

`final` が確定したレビューの状態ファイルの `final` を外し、履歴（`rounds`）を残したまま次の番号のラウンドから
収束ループを続ける。足すたびに `reopens` へ 1 件積む（I2）。ラウンドの上限・巻き直し・振動検知は、最後に
足した時点（`base_round`）より後のラウンドだけで数える（I3・I6）。

書き込みはしない。呼び出し側が `store._write_state` で保存する。
"""

from __future__ import annotations

from typing import Any


def reopen(st: dict[str, Any], now: str) -> dict[str, Any]:
    """`final` を外して足した記録を積み、積んだ記録を返す。`final` が確定していなければ `ValueError`。

    `ended_at` と `sweep` は足した記録へ移し、状態からは消す（決定 6）。残すと、足した後の実行が
    前の実行の最終スイープの結果で終わったように読まれる。
    """
    final = st.get("final")
    if final is None:
        raise ValueError("final が確定していない状態にはラウンドを足さない")
    rounds = st.get("rounds")
    if not isinstance(rounds, list):
        raise ValueError("rounds が list ではない")
    record = {
        "at": now,
        "from_final": final,
        "base_round": len(rounds),
        "ended_at": st.get("ended_at"),
        "sweep": st.get("sweep"),
    }
    st.setdefault("reopens", []).append(record)
    st["final"] = None
    st.pop("ended_at", None)
    st.pop("sweep", None)
    return record


def last_reopen(st: dict[str, Any]) -> dict[str, Any] | None:
    """最後に足した記録。足したことが無ければ `None`。"""
    reopens = st.get("reopens") or []
    return reopens[-1] if reopens and isinstance(reopens[-1], dict) else None


def base_round(st: dict[str, Any]) -> int:
    """最後に足した時点のラウンド数。足していなければ 0（`reopens` の無い今の形の状態ファイルも 0）。"""
    rec = last_reopen(st)
    return int(rec.get("base_round") or 0) if rec else 0


def rounds_since(st: dict[str, Any]) -> list[dict[str, Any]]:
    """最後に足した時点より後のラウンド。"""
    return list((st.get("rounds") or [])[base_round(st) :])


def sweep_closed(st: dict[str, Any]) -> bool:
    """最後に足した記録の最終スイープが、未解決 0 件で閉じたと検証できたか（I4）。"""
    rec = last_reopen(st)
    sweep = (rec or {}).get("sweep") or {}
    return sweep.get("verified") is True and int(sweep.get("remaining_open") or 0) == 0 and "remaining_open" in sweep
