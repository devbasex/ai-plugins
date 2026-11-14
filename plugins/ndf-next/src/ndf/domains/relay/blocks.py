"""最後の応答から、次の区間を指すコードブロックを読む。

Markdown の解析は `ndf.adapters.markdown` だけが行う。ここはコードブロックの行の印から、囲みの 1 行目の
情報の文字列（言語の名前の位置）が `fence` のブロックを拾う。`fence` の名前は guard の表の `relay-mark` の行の
`params.fence` にある（コードは名前を文字で持たない）。
"""

from __future__ import annotations

from ndf.adapters import markdown


def _info(line: str) -> str:
    """囲みの 1 行目の情報の文字列の最初の語（囲みでなければ空）。"""
    stripped = line.strip()
    marker = stripped[:3]
    if marker not in ("```", "~~~"):
        return ""
    words = stripped.lstrip(marker[0]).split()
    return words[0] if words else ""


def fenced(text: str, fence: str) -> list[str]:
    """情報の文字列が `fence` のコードブロックの中身を、出てくる順に返す。閉じない囲みは文書の終わりまで。"""
    lines = text.splitlines()
    flags = markdown.code_lines(text)
    found: list[str] = []
    i = 0
    while i < len(lines):
        if not flags[i] or _info(lines[i]) != fence:
            i += 1
            continue
        j, closed = _block_end(lines, flags, i)
        body = lines[i + 1 : j - 1] if closed else lines[i + 1 : j]
        found.append("\n".join(body).strip())
        i = j
    return found


def _block_end(lines: list[str], flags: list[bool], start: int) -> tuple[int, bool]:
    """囲みの次の行の位置と、閉じる囲みで終わったか。"""
    j = start + 1
    while j < len(lines) and flags[j]:
        j += 1
        if _is_close(lines[j - 1]):
            return j, True
    return j, False


def _is_close(line: str) -> bool:
    stripped = line.strip()
    return stripped[:3] in ("```", "~~~") and _info(line) == ""


def last_block(text: str, fence: str) -> str | None:
    """最後の `fence` のブロック。無ければ None。"""
    blocks = fenced(text, fence)
    return blocks[-1] if blocks else None
