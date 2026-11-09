"""glossary_write.py: 用語集の取り込み（glossary.py merge）の書き出し。"""

from __future__ import annotations

from step_result import EXIT_UNREADABLE, StepError


def write_merge_outputs(writes):
    """(path, text) の組を順に書き出す。書けなければ読めない（EXIT_UNREADABLE）として投げる。"""
    try:
        for path, text in writes:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    except OSError as e:
        raise StepError(f"用語集を書けない: {e}", EXIT_UNREADABLE)
