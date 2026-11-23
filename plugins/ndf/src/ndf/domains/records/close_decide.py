"""工程の終わりの判断（純粋な関数。N8a・#2293・#1893・#2067）。

- スプリントを閉じる条件 2: リリース後テストの記録があれば、フラグを問わず見る。不合格の行を持つ課題は閉じない
  （フラグの付け忘れで不合格の課題を閉じない）。不合格が無く保留（not_run）の行を持つ課題は、閉じるかを人が決める（#3855）
- クリーンアップの工程の記録: 課題の本文の進行のセクションで、工程「クリーンアップ」の行が未チェックならチェックを付ける。記録済みなら
  打ち直さない
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Optional

from ndf.progress.records import ISSUE_SECTION, SECTION_END

# クリーンアップの工程の名前（進行のセクションの行）
CLEANUP_STAGE = "クリーンアップ"
# 課題を閉じてよいかの判定（閉じてよい / 閉じない / 人が決める）
CLOSE = "close"
KEEP = "keep"
ASK = "ask"


def _rows_with(issue: int, verification: Optional[Iterable[Mapping[str, Any]]], outcome: str) -> list[Mapping[str, Any]]:
    if verification is None:
        return []
    return [r for r in verification if r.get("number") == issue and r.get("result") == outcome]


def blocking_rows(issue: int, verification: Optional[Iterable[Mapping[str, Any]]]) -> list[Mapping[str, Any]]:
    """課題を閉じない、リリース後テストの行（その課題の failed。記録が無ければ空）。"""
    return _rows_with(issue, verification, "failed")


def waiting_rows(issue: int, verification: Optional[Iterable[Mapping[str, Any]]]) -> list[Mapping[str, Any]]:
    """閉じるかを人が決める、リリース後テストの行（その課題の not_run。記録が無ければ空）。"""
    return _rows_with(issue, verification, "not_run")


def verdict(issue: int, verification: Optional[Iterable[Mapping[str, Any]]], require: bool) -> tuple[str, str]:
    """課題を閉じてよいか（`CLOSE` / `KEEP` / `ASK`）と理由。`require` は記録が要る（記録が無ければ閉じない）。

    記録があれば条件 2 を必ず見る。failed があれば not_run の有無に依らず閉じない（悪い方を優先する）。
    """
    if verification is None:
        return (KEEP, "リリース後テストの記録が無い") if require else (CLOSE, "リリース後テストの記録が無い（条件 2 を見ない）")
    rows = list(verification)
    bad = blocking_rows(issue, rows)
    if bad:
        return KEEP, f"リリース後テストに不合格の行が {len(bad)} 件ある"
    waiting = waiting_rows(issue, rows)
    if waiting:
        return ASK, f"保留（not_run）の行が {len(waiting)} 件ある。閉じるかを人が決める"
    return CLOSE, "リリース後テストに不合格と保留の行が無い"


def _stage_line(stage: str) -> re.Pattern[str]:
    return re.compile(rf"^- \[( |x)\] {re.escape(stage)}(?:[^\S\n].*)?$", re.MULTILINE)


def mark_stage(body: str, stage: str, stamp: str) -> Optional[str]:
    """進行のセクションの工程の行にチェックを付けた本文。記録済み・セクションか行が無ければ None（書き換えない）。"""
    start = body.find(ISSUE_SECTION)
    if start < 0:
        return None
    end = body.find(SECTION_END, start)
    stop = end if end >= 0 else len(body)
    m = _stage_line(stage).search(body, start, stop)
    if m is None or m.group(1) == "x":
        return None
    return body[: m.start()] + f"- [x] {stage} — {stamp}" + body[m.end() :]
