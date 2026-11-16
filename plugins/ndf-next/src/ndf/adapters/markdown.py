"""Markdown の行の種類とコードのコメントを扱うアダプター。

Markdown の解析は markdown-it-py、コードの字句は pygments だけが行う。正規表現で ``` の囲みや `#` を数えない。

- `block_kinds` は行ごとに、その行を含むブロックの種類（`BLOCK_KINDS`）を返す
- `code_lines` はコードブロック（囲みとインデント）の中の行かどうかを返す。引用や箇条の中のコードも含む
- `atx_headings` は文書の最上位の ATX の見出し（`## 題`）を返す。コードブロックと引用の中の `#` は見出しにしない
- `mask_comments` はコードの行のコメントを同じ幅の空白に置き換える
"""

from __future__ import annotations

from functools import lru_cache
from typing import NamedTuple

from markdown_it import MarkdownIt
from markdown_it.token import Token
from pygments.lexers import get_lexer_by_name
from pygments.token import Comment

# 行の種類。入れ子は外側を優先する（引用の中の表は quote）
BLOCK_KINDS = ("code", "heading", "quote", "table", "other")

# 開きのトークンの種類 → 行の種類
_KIND_OF_TOKEN = {
    "fence": "code",
    "code_block": "code",
    "heading_open": "heading",
    "blockquote_open": "quote",
    "table_open": "table",
}

# コメントを空白にする拡張子 → pygments の字句解析器の名前
COMMENT_LEXERS = {".py": "python", ".sh": "bash", ".js": "javascript", ".ts": "typescript"}


class Heading(NamedTuple):
    """ATX の見出しの 1 つ。`line` は 0 始まりの行の番号。"""

    line: int
    level: int
    title: str


@lru_cache(maxsize=1)
def _parser() -> MarkdownIt:
    return MarkdownIt("commonmark").enable("table")


@lru_cache(maxsize=32)
def _tokens(text: str) -> tuple[Token, ...]:
    return tuple(_parser().parse(text))


def _line_count(text: str) -> int:
    """markdown-it と同じ数え方の行の数（改行は `\\r\\n`・`\\r`・`\\n`。末尾の改行の後は行にしない）。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return 0
    return normalized.count("\n") + (0 if normalized.endswith("\n") else 1)


def block_kinds(text: str) -> list[str]:
    """行ごとに、その行を含むブロックの種類を返す。戻り値の長さは `text` の行の数。"""
    kinds: list[str | None] = [None] * _line_count(text)
    for token in _tokens(text):
        kind = _KIND_OF_TOKEN.get(token.type)
        if kind is None or token.map is None:
            continue
        start, end = token.map
        # トークンは外側から順に並ぶため、先に決めた種類（外側）を残す
        for i in range(start, min(end, len(kinds))):
            kinds[i] = kinds[i] or kind
    return [k or "other" for k in kinds]


def code_lines(text: str) -> list[bool]:
    """行ごとに、コードブロック（囲み・インデント）の中かを返す。閉じない囲みは文書の終わりまで続く。"""
    flags = [False] * _line_count(text)
    for token in _tokens(text):
        if token.type in ("fence", "code_block") and token.map is not None:
            start, end = token.map
            flags[start:end] = [True] * (min(end, len(flags)) - start)
    return flags


def atx_headings(text: str) -> list[Heading]:
    """文書の最上位の ATX の見出しを、出てくる順に返す（Setext の見出しと引用の中の見出しは返さない）。"""
    tokens = _tokens(text)
    found: list[Heading] = []
    for i, token in enumerate(tokens):
        if token.type != "heading_open" or token.level != 0 or not token.markup.startswith("#"):
            continue
        if token.map is None:
            continue
        found.append(Heading(token.map[0], int(token.tag[1:]), tokens[i + 1].content))
    return found


def mask_comments(lines: list[str], suffix: str) -> list[str]:
    """コードの行のコメントを同じ幅の空白に置き換える。

    戻り値の行の数と各行の長さは入力と同じ。pygments の `Comment` の字だけが空白になり、文字列の中の `#` は
    残る。`COMMENT_LEXERS` に無い拡張子は入力をそのまま返す。
    """
    name = COMMENT_LEXERS.get(suffix)
    if name is None:
        return list(lines)
    text = "\n".join(lines)
    lexer = get_lexer_by_name(name, stripnl=False, ensurenl=False)
    chars = list(text)
    # 行末のコメントを改行までと読む字句解析器があるため、末尾に改行を足して読む（位置は変わらない）
    for start, kind, value in lexer.get_tokens_unprocessed(text + "\n"):
        if kind not in Comment:
            continue
        for i in range(start, min(start + len(value), len(chars))):
            if chars[i] != "\n":
                chars[i] = " "
    masked = "".join(chars)
    out: list[str] = []
    pos = 0
    for line in lines:
        out.append(masked[pos : pos + len(line)])
        pos += len(line) + 1
    return out
