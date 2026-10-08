"""本文の課題を閉じる closing keywords（GitHub が課題を自動で閉じるキーワード `Closes #n` など）と、課題の参照の読み取り。
`pr-steps.py`（closing keywords の検査）と `release-steps.py`（版に含む PR から課題へたどる。宛先が既定ブランチでない PR は
GitHub の `closingIssuesReferences` が空になる）、`sprint-close.py`（参照だけの課題）が使う。"""

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


def _reference_pattern(repo):
    """記録のリポジトリの課題の参照（`#<番号>` と課題の URL）の正規表現。

    `#<番号>` は直前が英数字・`.`・`_`・`/`・`-` でないものだけを読み、`x/y#3`（他のリポジトリ）と
    `o/r#9`（`<記録のリポジトリ>#<番号>`）を外す。日本語の直後（`課題#5`）は読む。"""
    url = r"https?://github\.com/" + re.escape(repo) + r"/issues/(\d+)" if repo else r"(?!)"
    return re.compile(r"(?:(?<![A-Za-z0-9._/-])#(\d+)|" + url + r")(?![A-Za-z0-9_])", re.I)


def referenced_numbers(text, repo) -> list[int]:
    """記録のリポジトリ `repo` の課題を指す参照の番号を、重ねずに現れた順で返す（closing keywords かは問わない）。

    `#<番号>` が Pull Request の番号でも区別しない（呼ぶ側が状態を読んで外す）。"""
    out: list[int] = []
    for m in _reference_pattern(repo).finditer(text or ""):
        n = int(m.group(1) or m.group(2))
        if n not in out:
            out.append(n)
    return out
