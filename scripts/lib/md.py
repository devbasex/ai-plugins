"""Markdown の構造の読み取りの包み（#1142 の決定 19・種類 3）。markdown-it-py を呼ぶのはこのモジュールだけである。

11.0.0 の入れ替え（#3304）で 10 系のプラグインから根へ移した。根の検査（`check-markdown-links.py`・
`check-doc-staleness.py`・`check-skill-shell-vars.py`）が使う囲み・見出し・節・リンクとアンカーの読み取りだけを持つ。

囲み（```` ``` ```` / `~~~`）・見出し・節・地の文・リンクとアンカーを、CommonMark の規則で読む。

- 行の番号は 0 始まりで、`token.map` と同じ半開区間 `[start, end)` を使う
- 書き込み（節の差し替えなど）はこのモジュールで行わない。読み取った行の区間を使い、呼び出し側が行を操作する
  （節の外のバイト列を変えないため）
- アンカーは GitHub の規則（小文字にし、文字・結合文字・数字・`_`・`-`・空白のほかを落とし、空白を `-` にする。
  同じアンカーには `-1`・`-2` を付ける）

使う側は `ndf_wrappers.require("md")` を先に呼ぶ。
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache
from typing import TYPE_CHECKING, NamedTuple
from urllib.parse import unquote

if TYPE_CHECKING:  # markdown_it は解析するときだけ読む（起動を縮める。#2309）
    from markdown_it import MarkdownIt
    from markdown_it.token import Token


class Heading(NamedTuple):
    level: int
    title: str  # インラインの記法を外した字面（`code` は中身だけ）
    line: int  # 見出しの行（0 始まり）
    anchor: str  # GitHub のアンカー（同じ文書で重なれば `-1` などが付く）


class Section(NamedTuple):
    heading: Heading
    start: int  # 見出しの次の行
    end: int  # 同じか浅い見出しの行、または文書の終わり（含まない）


class Link(NamedTuple):
    href: str
    text: str
    line: int


@lru_cache(maxsize=1)
def _md_parser() -> MarkdownIt:
    from markdown_it import MarkdownIt

    return MarkdownIt("commonmark").enable("table")


@lru_cache(maxsize=16)
def _tokens(text: str) -> tuple[Token, ...]:
    return tuple(_md_parser().parse(text))


def md_tokens(text: str) -> list[Token]:
    """markdown-it-py のブロックのトークンの並び（読むだけ。書き換えない）。"""
    return list(_tokens(text))


def _line_count(text: str) -> int:
    return len(text.splitlines())


def fenced_lines(text: str) -> list[bool]:
    """行ごとに、コードの囲み（開き・閉じの行を含む）かインデントのコードブロックの中なら真。"""
    mask = [False] * _line_count(text)
    for t in _tokens(text):
        if t.type in ("fence", "code_block") and t.map:
            for i in range(t.map[0], min(t.map[1], len(mask))):
                mask[i] = True
    return mask


def plain_text(inline: Token) -> str:
    """インラインのトークンから記法を外した字面を返す（リンクは文字列、`code` は中身）。"""
    out = []
    for c in inline.children or []:
        if c.type in ("text", "code_inline", "html_inline"):
            out.append(c.content)
        elif c.type in ("softbreak", "hardbreak"):
            out.append(" ")
        elif c.type == "image":
            out.append(plain_text(c))
    return "".join(out)


def heading_anchor(title: str) -> str:
    """GitHub の見出しのアンカー（重なりの接尾辞は付けない）。"""
    kept = []
    for ch in title.strip().lower():
        cat = unicodedata.category(ch)
        if ch in " -_" or cat[0] in "LMN" or cat == "Pc":
            kept.append(ch)
    return "".join(kept).replace(" ", "-")


def headings(text: str) -> list[Heading]:
    """ATX と Setext の見出し。コードの囲みの中の `#` は見出しにしない。"""
    out: list[Heading] = []
    seen: dict[str, int] = {}
    used: set[str] = set()
    toks = _tokens(text)
    for i, t in enumerate(toks):
        if t.type != "heading_open" or not t.map:
            continue
        title = plain_text(toks[i + 1]).strip()
        base = heading_anchor(title)
        anchor = base
        while anchor in used:
            seen[base] = seen.get(base, 0) + 1
            anchor = f"{base}-{seen[base]}"
        used.add(anchor)
        out.append(Heading(int(t.tag[1:]), title, t.map[0], anchor))
    return out


def md_sections(text: str) -> list[Section]:
    """見出しごとの節。節は、次の同じか浅い見出しの行の手前まで（子の節を含む）。"""
    hs = headings(text)
    total = _line_count(text)
    out = []
    for k, h in enumerate(hs):
        end = next((o.line for o in hs[k + 1 :] if o.level <= h.level), total)
        out.append(Section(h, h.line + 1, end))
    return out


def section_named(text: str, title: str, level: int | None = None) -> Section | None:
    """字面が `title` と一致する最初の節（`level` を渡せば深さも合わせる）。無ければ None。"""
    for s in md_sections(text):
        if s.heading.title == title.strip() and (level is None or s.heading.level == level):
            return s
    return None


def links(text: str) -> list[Link]:
    """インラインのリンク（`[文字列](先)` と参照の形。自動リンクを含む）。画像は含めない。

    `href` は markdown-it-py が符号化した `%xx` を戻した字面、`line` はリンクのある行。
    """
    out: list[Link] = []
    for t in _tokens(text):
        if t.type != "inline" or not t.map:
            continue
        line = t.map[0]
        kids = t.children or []
        for k, c in enumerate(kids):
            if c.type in ("softbreak", "hardbreak"):
                line += 1
            if c.type != "link_open":
                continue
            out.append(Link(unquote(str(c.attrs.get("href", ""))), _link_label(kids, k), line))
    return out


def _link_label(kids: list, start: int) -> str:
    """`start` の link_open から link_close までの文字列（text と code_inline の字面）。"""
    label = []
    for d in kids[start + 1 :]:
        if d.type == "link_close":
            break
        if d.type in ("text", "code_inline"):
            label.append(d.content)
    return "".join(label)
