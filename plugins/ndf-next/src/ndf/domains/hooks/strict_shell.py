"""`strict` の guard が読むシェルの文の分割（区切りで分けたコマンドの語の並び）。

`shlex` で字句を読み、標準ライブラリだけで書く（環境が無いときのエントリポイントも `strict` から呼ぶ）。
"""

from __future__ import annotations

import shlex
from typing import Iterator, List, Union

# シェルの字句のうち、コマンドを区切るもの
_SEPARATORS = frozenset({";", "&&", "||", "|", "&", "|&", ";;", "(", ")"})
# 前後のコマンドを別のシェル（サブシェル）で走らせる区切り（パイプライン・裏で走らせる `&`）
PIPES = frozenset({"|", "|&"})
BACKGROUND = "&"
# 区切りでない演算子の字句（リダイレクト）。続けて書かれた演算子を分けるときに区切りと並べて長い順に当てる
_REDIRECTIONS = frozenset({"<", ">", "<<", ">>", "<<<", "<&", ">&", "&>", "&>>", ">|", "<>"})
_OPERATORS = sorted(_SEPARATORS | _REDIRECTIONS, key=len, reverse=True)


def parts(text: str) -> Iterator[Union[List[str], str]]:
    """コマンドの語の並びと区切りの字句（`(`・`)`・`|` など、str）を現れた順に。

    引用の中の改行は次の行とつなぐ。ヒアドキュメントの本文は読まない。閉じない引用は ValueError。
    """
    buffer = ""
    heredocs: List[str] = []
    for line in text.split("\n"):
        if heredocs:
            if line.strip() == heredocs[0]:
                heredocs.pop(0)
            continue
        buffer = f"{buffer}\n{line}" if buffer else line
        if buffer.endswith("\\"):
            buffer = buffer[:-1]
            continue
        try:
            tokens = _tokens(buffer)
        except ValueError:  # noqa: S112 利用者の入力: 引用が次の行へ続くので、次の行とつないで読み直す
            continue
        buffer = ""
        heredocs = _heredoc_delimiters(tokens)
        yield from _split(tokens)
    if buffer:
        raise ValueError("閉じない引用")


def _tokens(line: str) -> List[str]:
    lex = shlex.shlex(line, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = "#"
    return [piece for token in lex for piece in _operators(token)]


def _operators(token: str) -> List[str]:
    """`shlex` が 1 つにまとめた続きの演算子（`);`・`&&(` など）を、演算子ごとに分ける。演算子でない語はそのまま。"""
    if not token or token in _OPERATORS or not set(token) <= set("();<>|&"):
        return [token]
    pieces: List[str] = []
    rest = token
    while rest:
        head = next((op for op in _OPERATORS if rest.startswith(op)), rest[0])
        pieces.append(head)
        rest = rest[len(head) :]
    return pieces


def _heredoc_delimiters(tokens: List[str]) -> List[str]:
    """行に現れたヒアドキュメント（`<<` / `<<-`）の終わりの語。本文はコマンドではない。"""
    return [tokens[i + 1].lstrip("-") for i, token in enumerate(tokens[:-1]) if token == "<<"]


def _split(tokens: List[str]) -> Iterator[Union[List[str], str]]:
    words: List[str] = []
    for token in tokens:
        if token in _SEPARATORS:
            if words:
                yield words
            words = []
            yield token
        else:
            words.append(token)
    if words:
        yield words
