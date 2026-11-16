"""用語集の検査が文書の中の語を探す正規表現（`glossary.py` から分けた。#2733 の決定 12）。"""

from __future__ import annotations

import re

ASCII_WORD = re.compile(r"^[A-Za-z0-9_\-]+$")
KATAKANA_WORD = re.compile(r"^[ァ-ヺー]+$")  # カタカナだけの語（長音符を含む）
# 漢字の範囲（CJK 統合漢字拡張 A・CJK 統合漢字・CJK 互換漢字・繰り返しの記号）。漢字の判定はここだけに置く（#3190）
KANJI = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3005"
_KANJI_CHAR = re.compile(f"[{KANJI}]")


def _word_part(w: str, katakana: bool) -> str:
    if ASCII_WORD.match(w):
        return rf"(?<![A-Za-z0-9_]){re.escape(w)}(?![A-Za-z0-9_])"
    if katakana and KATAKANA_WORD.match(w):
        return rf"(?<![ァ-ヺー]){re.escape(w)}(?![ァ-ヺー])"
    return re.escape(w)


def is_kanji_edged(w: str) -> bool:
    """語の先頭か末尾の 1 文字が漢字か。空の文字列は偽。"""
    return bool(w) and bool(_KANJI_CHAR.fullmatch(w[0]) or _KANJI_CHAR.fullmatch(w[-1]))


def standalone_part(w: str) -> str:
    """単独で照合する語 1 つの断片。先頭が漢字なら直前が漢字の出現に、末尾が漢字なら直後が漢字の出現に当てない
    （`段` が `段階`・`手段` に当たらないため）。"""
    head = f"(?<![{KANJI}])" if _KANJI_CHAR.fullmatch(w[0]) else ""
    tail = f"(?![{KANJI}])" if _KANJI_CHAR.fullmatch(w[-1]) else ""
    return f"{head}{re.escape(w)}{tail}"


def word_pattern(words, katakana: bool = False, standalone=()) -> re.Pattern | None:
    """語の並びの正規表現（長い語から当てる）。ASCII の語は語の境界で当てる。`katakana` なら、カタカナだけの語も前後が
    カタカナのときに当てない（廃止語のラインがガイドライン・ステータスラインの内側に当たらないため）。`standalone` の語は
    `standalone_part` で組む。同じ語が両方にあれば `words` の側で組む（当たる範囲が広い側）。"""
    plain = {w for w in words if w}
    parts = {w: _word_part(w, katakana) for w in plain}
    parts.update({w: standalone_part(w) for w in standalone if w and w not in plain})
    if not parts:
        return None
    return re.compile("|".join(parts[w] for w in sorted(parts, key=len, reverse=True)))
