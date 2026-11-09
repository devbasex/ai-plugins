"""閉じる条件 2（リリース後テスト）を満たさない課題の結果の項目（`sprint-close.py`）。"""

from __future__ import annotations

UNVERIFIED = "閉じているが閉じる条件 2 を満たさない: "


def unaccepted_item(base, state, why, kept):
    """条件 2 を満たさない課題の結果の項目。kept があればその理由で開いたまま残す。"""
    if kept:
        return {**base, "result": "kept_open", "reason": kept}
    if state == "CLOSED":
        return {**base, "result": "closed_unverified", "reason": UNVERIFIED + why}
    return {**base, "result": "kept_open", "reason": why}
