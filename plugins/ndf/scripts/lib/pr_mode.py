"""Pull Request の宛て先の区別と、本文の末尾に書くモードの 1 行。

ミッションの課題の Pull Request はミッションのブランチ（`mission/<名前>`）宛てに出して集め、
実装レビューを通さない。develop への Pull Request はミッションで 1 本で、検査を通す。

本文の末尾には `モード: <mode> / 通した工程: <工程> → <工程>` の 1 行を書く。配布後の
不具合の起票数と突き合わせ、ミッション単位にした後の精度を測る材料にする。
"""
from __future__ import annotations

import re

MISSION_PREFIX = "mission/"
MODE_LINE_PREFIX = "モード: "
REVIEW_MARK = "<!-- I want to review in Japanese. -->"
STAGE_SEP = " → "
# 本文の最後に置く印。モードの 1 行はこれらより前に置く
TRAILERS = (REVIEW_MARK, "🤖 Generated with")


def pr_target(base: str) -> str:
    """宛て先の区分を返す。`mission`（課題の PR）か `develop`（ミッションの PR・単独の PR）。"""
    return "mission" if (base or "").startswith(MISSION_PREFIX) else "develop"


def needs_review(base: str) -> bool:
    """実装レビューを通す宛て先か。ミッションのブランチ宛ては通さない。"""
    return pr_target(base) != "mission"


def split_stages(text: str | None) -> list[str]:
    """`設計,実装` / `設計、実装` / `設計 → 実装` を工程の並びへ分ける。"""
    if not text:
        return []
    return [s.strip() for s in re.split(r"[,、]|→", text) if s.strip()]


def mode_line(mode: str, stages: list[str]) -> str:
    return f"{MODE_LINE_PREFIX}{mode} / 通した工程: {STAGE_SEP.join(stages) if stages else '無し'}"


def with_mode_line(body: str, mode: str | None, stages: list[str]) -> str:
    """本文の末尾にモードの 1 行を置く。既にあれば置き換える。

    末尾の印（レビューの印・生成の署名）はその後ろに残す。mode が空なら本文を変えない。
    """
    if not mode:
        return body
    line = mode_line(mode, stages)
    kept = [l for l in body.rstrip("\n").split("\n") if not l.startswith(MODE_LINE_PREFIX)]
    cut = len(kept)
    while cut > 0 and (not kept[cut - 1].strip() or kept[cut - 1].startswith(TRAILERS)):
        cut -= 1
    head = re.sub(r"\n{3,}", "\n\n", "\n".join(kept[:cut])).rstrip()
    tail = [l for l in kept[cut:] if l.strip()]
    parts = [p for p in (head, line, *tail) if p]
    return "\n\n".join(parts) + "\n"
