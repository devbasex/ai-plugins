"""原因の鍵（#2343 の決定 6）。失敗が前回と同じ原因かを、種類と伏せた理由の文で見る。外の世界を触らない。

`make(kind, text)` は理由の文から日時・URL・パス・16 進の語・数字をこの順で伏せ、空白を 1 つにまとめて先頭 200 字を残し、
`"<種類>: <伏せた文>"` を返す。種類は例外の型の名前か `exit=<終了コード>`。伏せた文が空なら None（原因を見分けられない）。
run の番号や秒だけが違う失敗は同じ鍵になり、終了コードや文が違えば別の鍵になる。`brief(key)` は鍵の先頭 60 字。
"""

from __future__ import annotations

import re

KEEP = 200
BRIEF = 60
_RULES = (
    (re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"), "<時刻>"),
    (re.compile(r"https?://\S+"), "<URL>"),
    (re.compile(r"(?<![\w<])(?:~/|\.\./|\./|/)\S*|(?<![\w<])[\w.-]+(?:/[\w.-]+)+/?"), "<パス>"),
    (re.compile(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{7,64}\b"), "<sha>"),
    (re.compile(r"\d+(?:\.\d+)?"), "<数>"),
)
_SPACE = re.compile(r"\s+")


def make(kind: str, text: str | None) -> str | None:
    """種類と理由の文から原因の鍵を作る。伏せた文が空なら None。"""
    body = text or ""
    for pattern, mark in _RULES:
        body = pattern.sub(mark, body)
    body = _SPACE.sub(" ", body).strip()[:KEEP]
    return f"{kind}: {body}" if body else None


def of_exception(exc: BaseException) -> str | None:
    """例外の原因の鍵（種類は型の名前）。"""
    return make(type(exc).__name__, str(exc))


def brief(key: str | None) -> str:
    """鍵の要旨（先頭 60 字）。鍵が無ければ「原因を見分けられない」。"""
    return key[:BRIEF] if key else "原因を見分けられない"
