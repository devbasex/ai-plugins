"""コードの行のコメントを空白に置き換える（glossary.py の `mask_comments` の本体）。

文字列の内側の記号をコメントとみなさないための走査を、1 手ずつ進める形で持つ。使う側は `mask_comments` だけを呼ぶ。"""

from __future__ import annotations


def _consume_block_comment(line: str, chars: list[str], i: int) -> tuple[bool, int]:
    """`/* */` の続きを `chars` の上で空白にする。(まだブロックの中か, 次の位置) を返す。"""
    end = line.find("*/", i)
    stop = len(line) if end < 0 else end + 2
    chars[i:stop] = " " * (stop - i)
    return end < 0, stop


def _advance_in_quote(line: str, i: int, quote: str, sh: bool, escaped_eol: bool) -> tuple[int, str | None, bool]:
    """引用の中を 1 手進める。(次の位置, 開いている引用, 行末の `\\` で改行をエスケープしたか) を返す。"""
    if line[i] == "\\" and not (sh and quote == "'"):
        return i + 2, quote, i + 1 == len(line)
    if line.startswith(quote, i):
        return i + len(quote), None, escaped_eol
    return i + 1, quote, escaped_eol


def _is_line_comment_start(line: str, i: int, hash_style: bool, suffix: str) -> bool:
    """`i` から行末までのコメント（`#` か `//`）が始まるか。"""
    c = line[i]
    return (c == "#" and hash_style and (suffix == ".py" or i == 0 or line[i - 1] in " \t;|&(")) or (
        not hash_style and line.startswith("//", i)
    )


def _step_char(
    line: str, chars: list[str], i: int, state: tuple[bool, str | None, bool], rules: tuple[str, tuple[str, ...]]
) -> tuple[int, tuple[bool, str | None, bool]]:
    """走査を 1 手進める。state は (`/* */` の中か, 開いている引用, 行末の `\\` で改行をエスケープしたか)、
    rules は (拡張子, 引用を開く記号)。(次の位置, 次の state) を返す。行末までのコメントは空白にして行末を返す。"""
    block, quote, escaped_eol = state
    suffix, opens = rules
    hash_style, sh = suffix in (".py", ".sh"), suffix == ".sh"
    if block:
        block, i = _consume_block_comment(line, chars, i)
        return i, (block, quote, escaped_eol)
    if quote:
        i, quote, escaped_eol = _advance_in_quote(line, i, quote, sh, escaped_eol)
        return i, (block, quote, escaped_eol)
    if sh and line[i] == "\\":
        return i + 2, state
    opened = next((q for q in opens if line.startswith(q, i)), None)
    if opened:
        return i + len(opened), (block, opened, escaped_eol)
    if _is_line_comment_start(line, i, hash_style, suffix):
        chars[i:] = " " * (len(line) - i)
        return len(line), state
    if not hash_style and line.startswith("/*", i):
        chars[i : i + 2] = "  "
        return i + 2, (True, quote, escaped_eol)
    return i + 1, state


def mask_comments(lines: list[str], suffix: str) -> list[str]:
    """コードの行のコメントを空白に置き換える。`#` は .py / .sh（.sh は語の頭だけ）、`//` と `/* */` は .js / .ts。
    文字列の内側の記号はコメントとみなさない（`"--cart"` の識別子は残す）。行をまたぐ文字列（.py の三連引用符、
    .js / .ts のバッククォート、.sh の引用）は次の行へ持ち越す。.sh は引用の外の `\\` で次の 1 文字を飛ばし、
    `'` の内側の `\\` はエスケープとみなさない。.py / .js / .ts の通常の引用も、行末の `\\` で改行を
    エスケープしたときは次の行へ持ち越す。"""
    opens = {".py": ('"""', "'''", '"', "'"), ".sh": ('"', "'")}.get(suffix, ("`", '"', "'"))
    multiline = {'"', "'"} if suffix == ".sh" else {'"""', "'''", "`"}
    rules = (suffix, opens)
    out, block, quote = [], False, None
    for line in lines:
        chars, i, state = list(line), 0, (block, quote, False)
        while i < len(line):
            i, state = _step_char(line, chars, i, state, rules)
        block, quote, escaped_eol = state
        if quote not in multiline and not escaped_eol:
            quote = None
        out.append("".join(chars))
    return out
