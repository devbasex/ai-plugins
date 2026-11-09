"""用語集の検査が文書の中の語を探す正規表現（`glossary.py` から分けた。#2733 の決定 12）。"""

from __future__ import annotations

import re

ASCII_WORD = re.compile(r"^[A-Za-z0-9_\-]+$")
KATAKANA_WORD = re.compile(r"^[ァ-ヺー]+$")  # カタカナだけの語（長音符を含む）


def _word_part(w: str, katakana: bool) -> str:
    if ASCII_WORD.match(w):
        return rf"(?<![A-Za-z0-9_]){re.escape(w)}(?![A-Za-z0-9_])"
    if katakana and KATAKANA_WORD.match(w):
        return rf"(?<![ァ-ヺー]){re.escape(w)}(?![ァ-ヺー])"
    return re.escape(w)


def word_pattern(words, katakana: bool = False) -> re.Pattern | None:
    """語の並びの正規表現（長い語から当てる）。ASCII の語は語の境界で当てる。`katakana` なら、カタカナだけの語も前後が
    カタカナのときに当てない（廃止語のラインがガイドライン・ステータスラインの内側に当たらないため）。"""
    words = sorted({w for w in words if w}, key=len, reverse=True)
    if not words:
        return None
    return re.compile("|".join(_word_part(w, katakana) for w in words))
