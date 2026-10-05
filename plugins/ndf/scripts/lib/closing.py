"""本文の課題を閉じる語（`Closes #n` など）の読み取り。`pr-steps.py`（閉じる語の検査）と `release-steps.py`
（版に含む PR から課題へたどる。宛先が既定ブランチでない PR は GitHub の `closingIssuesReferences` が空になる）が使う。"""

from __future__ import annotations

import re

CLOSING = re.compile(
    r"\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*"
    r"(https?://github\.com/[\w.-]+/[\w.-]+/issues/\d+|[\w.-]+/[\w.-]+#\d+|#\d+)",
    re.I,
)


def closing_words(text):
    """閉じる語の一致の字面（`Closes #12` など）。"""
    return [m.group(0) for m in CLOSING.finditer(text or "")]


def closing_issues(text):
    """閉じる語が指す課題の字面（`#12`・`owner/repo#12`・課題の URL）。"""
    return [m.group(2) for m in CLOSING.finditer(text or "")]


def closing_numbers(text) -> list[int]:
    """閉じる語が指す課題の番号を、重ねずに現れた順で返す。"""
    out: list[int] = []
    for ref in closing_issues(text):
        n = int(re.search(r"(\d+)$", ref).group(1))
        if n not in out:
            out.append(n)
    return out
