"""設計承認の依頼の id の規則（`gate1_requests`・`parallel_lane.request_gate1`・`parallel_ask`・`parallel_design_gate` が共有する）。

読み込むものを持たない。`parallel_lane` から読んでも、`gate1_requests` の読み書きを呼び手の到達範囲へ広げない。
"""

from __future__ import annotations

HEAD_LEN = 12  # 依頼の id に入れる SHA の桁


def gate1_id(lane: str, pr: int, stamp: str, suffix: str = "") -> str:
    """設計承認の依頼の id `<レーン>-g<設計 PR>-<先頭の SHA の 12 桁><接尾辞>`（同じ先頭の打ち直しは同じ id）。"""
    return f"{lane}-g{pr}-{stamp[:HEAD_LEN]}{suffix}"
