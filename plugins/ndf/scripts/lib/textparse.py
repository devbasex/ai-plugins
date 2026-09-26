"""unified diff の解釈とコードのコメントの除去の包み（#1142 の決定 19・種類 11・12）。

unidiff と pygments を呼ぶのはこのモジュールだけである。

- `diff_added_lines(diff)`: `git diff` の出力から、ファイルごとに足した行の番号（新しい側・1 始まり）を返す。
  消したファイル（`+++ /dev/null`）は含めない。パスは `b/` を外した字面
- `blank_comments(lines, suffix)`: コードの行のコメントを同じ幅の空白に置き換える（行の数と桁の位置を保つ）。
  字句の判定は pygments の字句解析器で、`Token.Comment` の種類（行・ブロック・シバン）を消す。文字列の中の
  `#` や `//`、行をまたぐ文字列・docstring は残す。字句解析器の無い拡張子は、そのまま返す

使う側は `deps.require("textparse")` を先に呼ぶ。
"""
from __future__ import annotations

from typing import Iterable

from pygments.lexer import Lexer
from pygments.lexers import get_lexer_by_name, get_lexer_for_filename
from pygments.token import Comment
from pygments.util import ClassNotFound
from unidiff import PatchSet
from unidiff.errors import UnidiffParseError

# 拡張子 → pygments の字句解析器の名前（ほかの拡張子は pygments のファイル名の判定に任せる）
_LEXERS = {".py": "python", ".sh": "bash", ".bash": "bash", ".js": "javascript", ".mjs": "javascript",
           ".ts": "typescript", ".tsx": "tsx"}


class DiffParseError(ValueError):
    """unified diff として読めない。"""


def diff_added_lines(diff: str) -> dict[str, set[int]]:
    """unified diff の足した行の番号を、ファイルごとに返す（足した行の無いファイルは含めない）。"""
    try:
        patch = PatchSet(diff.splitlines(keepends=True))
    except UnidiffParseError as exc:
        raise DiffParseError(f"unified diff として読めない: {exc}") from None
    out: dict[str, set[int]] = {}
    for f in patch:
        if f.is_removed_file:
            continue
        path = f.target_file[2:] if f.target_file.startswith("b/") else f.target_file
        added = {ln.target_line_no for hunk in f for ln in hunk if ln.is_added and ln.target_line_no}
        if added:
            out.setdefault(path, set()).update(added)
    return out


def _lexer(suffix: str) -> Lexer | None:
    opts = {"stripnl": False, "stripall": False, "ensurenl": False}
    try:
        name = _LEXERS.get(suffix.lower())
        return get_lexer_by_name(name, **opts) if name else get_lexer_for_filename(f"x{suffix}", **opts)
    except ClassNotFound:
        return None


def blank_comments(lines: Iterable[str], suffix: str) -> list[str]:
    """行の並びのコメントを空白に置き換えた並びを返す（改行は保つ。`suffix` は `.py` などの拡張子）。"""
    lines = list(lines)
    lexer = _lexer(suffix)
    if lexer is None or not lines:
        return lines
    text = "\n".join(lines)
    chars = list(text)
    for index, kind, value in lexer.get_tokens_unprocessed(text):
        if kind in Comment:
            for k in range(index, index + len(value)):
                if chars[k] != "\n":
                    chars[k] = " "
    return "".join(chars).split("\n")
