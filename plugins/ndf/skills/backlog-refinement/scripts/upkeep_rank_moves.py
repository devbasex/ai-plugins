"""upkeep_rank_moves.py: 前倒し・後ろ倒しの決定を置き場所へ反映し、マイルストーンをまたぐ依存を拾う（#1429）。GitHub を呼ばない。"""

from __future__ import annotations

from upkeep_rank_config import APPROVED, AUTO


def apply_moves(place: dict, forward: list[dict], backward: list[dict]) -> dict:
    """前倒し（自動・承認済み）と後ろ倒し（承認済み）を反映した課題の置き場所。"""
    moved = dict(place)
    for f in forward:
        if f["decision"] in (AUTO, APPROVED):
            for n in [f["number"], *f["group"]]:
                moved[n] = f["to"]
    for b in backward:
        if b["decision"] == APPROVED:
            moved[b["number"]] = b["to"]
    return moved


def cross_milestone_deps(board, moved: dict) -> list[dict]:
    """前のマイルストーンの課題が後ろのマイルストーンの課題に依存している辺。"""
    pos = {t: i for i, t in enumerate(board.order)}
    return [
        {"number": a, "milestone": moved[a], "depends_on": b, "depends_on_milestone": moved[b]}
        for a, b in sorted(board.edges)
        if moved.get(a) in pos and moved.get(b) in pos and pos[moved[a]] < pos[moved[b]]
    ]
