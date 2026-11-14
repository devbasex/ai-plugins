"""`strict` の guard が読むシェルの文の分割（区切りで分けたコマンドの語の並び）。

`shlex` で字句を読み、標準ライブラリだけで書く（環境が無いときのエントリポイントも `strict` から呼ぶ）。
"""

from __future__ import annotations

import shlex
from typing import Iterator, List

# シェルの字句のうち、コマンドを区切るもの
_SEPARATORS = frozenset({";", "&&", "||", "|", "&", "|&", ";;", "(", ")"})


def segments(text: str) -> Iterator[List[str]]:
    """区切りで分けたコマンドの語の並び。引用の中の改行は次の行とつなぐ。閉じない引用は ValueError。"""
    buffer = ""
    for line in text.split("\n"):
        buffer = f"{buffer}\n{line}" if buffer else line
        if buffer.endswith("\\"):
            buffer = buffer[:-1]
            continue
        try:
            tokens = _tokens(buffer)
        except ValueError:  # noqa: S112 利用者の入力: 引用が次の行へ続くので、次の行とつないで読み直す
            continue
        buffer = ""
        yield from _split(tokens)
    if buffer:
        raise ValueError("閉じない引用")


def _tokens(line: str) -> List[str]:
    lex = shlex.shlex(line, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = "#"
    return list(lex)


def _split(tokens: List[str]) -> Iterator[List[str]]:
    words: List[str] = []
    for token in tokens:
        if token in _SEPARATORS:
            if words:
                yield words
            words = []
        else:
            words.append(token)
    if words:
        yield words
