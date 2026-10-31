"""本文の課題を閉じる closing keywords（GitHub が課題を自動で閉じるキーワード `Closes #n` など）と、閉じる予定の参照
（`最終工程の後に閉じる: #n`。GitHub は閉じず、`sprint-close.py` がスプリント課題として集める）と、課題の参照の読み取り。
`pr-steps.py`（closing keywords の検査と、既定ブランチ宛の本文の置き換え）と `supervise_lib/pr.py`（同じ置き換え）、
`release-steps.py`（版に含む PR から課題へたどる。宛先が既定ブランチでない PR は GitHub の `closingIssuesReferences` が
空になる）、`sprint-close.py`（参照だけの課題）が使う。閉じる指定の形はこのモジュールだけが持ち、
`closing-issues.sh` は同じ形を読む（#2228）。"""

from __future__ import annotations

import re

_TARGET = r"(https?://github\.com/[\w.-]+/[\w.-]+/issues/\d+|[\w.-]+/[\w.-]+#\d+|#\d+)"
_KEYWORD = r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*"
DEFERRED = "最終工程の後に閉じる"  # 閉じる予定の参照の語。GitHub の closing keywords を含まない（#2228 の I9）
_DEFERRED_LEAD = DEFERRED + r"\s*:\s*"

CLOSING = re.compile(_KEYWORD + _TARGET, re.I)
_ANY_CLOSING = re.compile(r"(?:" + _KEYWORD + r"|" + _DEFERRED_LEAD + r")" + _TARGET, re.I)


def defer_closing(text):
    """closing keywords を閉じる予定の参照へ置き換える。(置き換えた本文, 置き換えた指し先の字面の並び) を返す。

    指し先の字面（`#12`・`owner/repo#12`・課題の URL）は変えない。宛先が既定ブランチの Pull Request の本文だけに使う。"""
    targets: list[str] = []

    def repl(m):
        targets.append(m.group(1))
        return f"{DEFERRED}: {m.group(1)}"

    return CLOSING.sub(repl, text or ""), targets


def defer_for_base(root, base, text):
    """宛先 base の Pull Request の本文を、マージで GitHub が課題を閉じるとき（lib/repo.py の merge_closes_issues）だけ
    defer_closing に通す。(本文, 置き換えた指し先の字面の並び) を返す。`pr-steps.py` と `supervise_lib/pr.py` が使う。"""
    import repo  # git を読む層。本文だけを扱う関数の読み手に git の依存を持ち込まない

    return defer_closing(text) if repo.merge_closes_issues(root, base) else (text or "", [])


def closing_words(text):
    """closing keywords の一致の字面（`Closes #12` など）。"""
    return [m.group(0) for m in CLOSING.finditer(text or "")]


def closing_issues(text):
    """closing keywords と閉じる予定の参照が指す課題の字面（`#12`・`owner/repo#12`・課題の URL）を現れた順で返す。"""
    return [m.group(1) for m in _ANY_CLOSING.finditer(text or "")]


def closing_numbers(text) -> list[int]:
    """closing keywords と閉じる予定の参照が指す課題の番号を、重ねずに現れた順で返す。"""
    out: list[int] = []
    for ref in closing_issues(text):
        n = int(re.search(r"(\d+)$", ref).group(1))
        if n not in out:
            out.append(n)
    return out


_REF = re.compile(r"^(?:https?://github\.com/([\w.-]+/[\w.-]+)/issues/|([\w.-]+/[\w.-]+)#|#)(\d+)$", re.I)


def issue_ref(ref, repo):
    """課題の字面（`#12`・`owner/repo#12`・課題の URL）を (owner/name, 番号) にする。`#12` は `repo` の課題。読めなければ None。"""
    m = _REF.match(str(ref or "").strip())
    if not m:
        return None
    return (m.group(1) or m.group(2) or repo, int(m.group(3)))


def closing_refs(text, repo) -> list[tuple[str, int]]:
    """closing keywords と閉じる予定の参照が指す課題を (owner/name, 番号) で、重ねずに現れた順で返す。`#12` は `repo` の課題。"""
    out: list[tuple[str, int]] = []
    for ref in closing_issues(text):
        got = issue_ref(ref, repo)
        if got is not None and got not in out:
            out.append(got)
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
