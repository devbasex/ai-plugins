"""notes（`release-steps.py` の cmd_notes）が PR の欄から組む中身。通常の経路と承認資料（--approval）の経路と metrics へ同じ組で渡る。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NotesContent:
    notes: list  # 欄を読んだ PR（pr_notes の結果）
    bullets: list[str]  # 利用者向けの変化の箇条（置き換えられた箇条を除く）
    risks: list[str]  # 危険の箇条
    migration: list[str]  # 移行の手順の箇条
    skipped: list  # マージされていない PR の番号
    rep_items: list[dict]  # 置き換え・読めない置き換えの結果の項目
    gone: int  # 後の PR が置き換えて除いた箇条の数

    @classmethod
    def of(cls, notes: list, after: dict, skipped: list, rep_items: list[dict], gone: int) -> NotesContent:
        """PR の欄（`notes`）と、置き換えた後の箇条（`after`: PR 番号 → 箇条）から組む。"""
        return cls(
            notes,
            [f"- {i}" for x in notes for i in after[x.number]],
            [f"- {i}" for x in notes for i in x.risks],
            [f"- {i}" for x in notes for i in x.migration],
            skipped,
            rep_items,
            gone,
        )
