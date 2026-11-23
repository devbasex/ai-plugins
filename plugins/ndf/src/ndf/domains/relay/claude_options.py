"""claude の CLI の引数からオプションを読む（`claude_args` と hook の `reply_judge` が共に使う）。

オプションが値を取るかの表（`contracts/data/claude-cli.json` の `flags`・`variadic`・`value`）は呼び手が読んで渡す。
このモジュールは標準ライブラリだけを読む（hook の閉包に `ndf.contracts` を入れない）。表に無いオプションは、次の語が
`-` で始まらなければ値ごと引き継ぐ。`--名前=値` の形は 1 語で読む。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Optional, Sequence

# オプションが値を取るかの表（`contracts/` からの相対パス）
TABLE = "data/claude-cli.json"

Table = Mapping[str, frozenset[str]]


@dataclass(frozen=True)
class Option:
    name: str
    words: tuple[str, ...]  # オプションの語と値（`--名前=値` は 1 語）
    value: Optional[str]


def table_of(data: Mapping[str, Any]) -> dict[str, frozenset[str]]:
    """`claude-cli.json` の中身から、値が一覧の項目だけを集合にした表。"""
    return {k: frozenset(v) for k, v in data.items() if isinstance(v, list)}


def take(table: Table, args: Sequence[str], i: int) -> tuple[Option, int]:
    """`args[i]` から 1 つのオプションを読み、次の位置を返す。"""
    word = args[i]
    name, eq, inline = word.partition("=")
    if eq and name.startswith("--"):
        return Option(name, (word,), inline), i + 1
    nxt = args[i + 1] if i + 1 < len(args) else None
    if word in table["flags"]:
        return Option(word, (word,), None), i + 1
    if word in table["variadic"]:
        j = i + 1
        while j < len(args) and not args[j].startswith("-"):
            j += 1
        return Option(word, tuple(args[i:j]), None), j
    if nxt is not None and (not nxt.startswith("-") or word in table["value"]):
        return Option(word, (word, nxt), nxt), i + 2
    return Option(word, (word,), None), i + 1


def is_option(word: str) -> bool:
    """`word` がオプションの語か（`-` で始まり、`-` 1 文字でない）。`--` の判定は呼び手が先にする。"""
    return word.startswith("-") and word != "-"


def options(table: Table, args: Sequence[str]) -> Iterator[tuple[str, Optional[str]]]:
    """オプションの名前と値（値が無ければ None）を前から順に返す。位置引数と `--` より後は返さない。

    値を取るかは表で決めるので、ほかのオプションの値になった語をオプションとして読まない。"""
    i = 0
    while i < len(args):
        word = args[i]
        if word == "--":
            return
        if not is_option(word):
            i += 1
            continue
        opt, i = take(table, args, i)
        yield opt.name, opt.value
