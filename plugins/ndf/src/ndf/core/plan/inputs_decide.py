"""プランを組む前の入力の確かめ（純粋な関数）。止める理由を文で返し、無ければ None か空を返す。

- 課題の本文の `## 受け入れ条件` のセクションが無い・空なら止める（#2246）
- `## 受け入れ条件の確認` の表で区分の無い条件があれば止める（#2357）
- 統合した課題の受け入れ条件が統合先の本文から落ちていれば止める（#2274）
- 承認済みの課題の設計の結果を読めない・PR の単位が 2 つ以上なら止める（#2596・#2539）。実装を外したまま進めない
- 設計の結果の触るファイルのセルが、パスを挙げずに別の設計文書へのリンクで委ねていれば止める（#2453）。リンク先は
  たどらない（分割した文書のセクションに書式の定めが無い）。触るファイルは先頭の表のセルへ直に挙げる
- テストの検証は、サブディレクトリからの相対のパスで書いた名前も実在として扱う（#2553・#1811）
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import NamedTuple, Optional, TypeVar

from markdown_it import MarkdownIt
from markdown_it.common.html_re import attr_name, attribute, open_tag
from markdown_it.rules_block import StateBlock, html_block, paragraph
from markdown_it.rules_inline import StateInline, backtick, html_inline
from markdown_it.token import Token

HEADING = "## 受け入れ条件"
CHECKS_HEADING = "## 受け入れ条件の確認"
CHECKS_HEAD = ("条件", "区分")
KINDS = ("マージ前", "リリース後")
_BOX = re.compile(r"- \[[ xX]\](?=[ \t\r]|$)\s*(.*)")
# HTML の文を前から区切る。コメントは閉じ（無ければ末尾）まで 1 つにして中のタグを読まず、タグは改行を含めて読む
_COMMENT_OR_TAG = re.compile(rf"<!(?=--).*?(?:-->|\Z)|({open_tag})", re.DOTALL)
# 文の末尾で閉じていないタグ（属性の間か、引用符で始めた属性値の途中）。1 つめの組は書きかけの属性の `=`、2 つめは
# 閉じていない属性値
_UNFINISHED_TAG = re.compile(rf"""<[A-Za-z][A-Za-z0-9\-]*{attribute}*(?:\s+{attr_name}\s*(=)\s*("[^"]*|'[^']*)?|\s*/?)\Z""")


def _recording(rule: Callable[[StateInline, bool], bool]) -> Callable[[StateInline, bool], bool]:
    """インラインの規則が読み取った範囲を `env["spans"]` に残す規則。"""

    def run(state: StateInline, silent: bool) -> bool:
        start = state.pos
        matched = rule(state, silent)
        # 画像の代替テキストは別の文字列として解析されるので、位置を残さない
        if matched and not silent and state.src == state.env["src"]:
            state.env["spans"].append((rule is html_inline, start, state.pos))
        return matched

    return run


# コード表記と本物の HTML コメントの位置を読む（`<!--` を含む段落と見出しだけに使う）
_INLINE = MarkdownIt("commonmark")
_INLINE.inline.ruler.at("backticks", _recording(backtick))
_INLINE.inline.ruler.at("html_inline", _recording(html_inline))


def _comment_spans(content: str) -> list[tuple[bool, int, int]]:
    """インラインの文の (本物のコメントなどの HTML か, 開始, 終了) の並び。偽はコード表記。"""
    env: dict = {"src": content, "spans": []}
    _INLINE.inline.parse(content, _INLINE, env, [])
    return env["spans"]


def _inline_marks(lines: Sequence[str] | Mapping[int, str], tokens: Iterable[Token]) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """`<!--` の位置を (段落と見出しの中の本物のコメントの開き, コメントを開かないもの) で返す。

    コメントを開かないのは、段落と見出しのコード表記の中と、HTML のブロックのタグの属性値の中。
    位置は (行の添字, 行末までの文字数)。行頭の字下げや引用の記号を除いた行でも、元の行でも同じ値になる。
    結果はトークンに残し、同じトークンの解析と位置の変換は 1 回だけにする。
    """
    comments: set[tuple[int, int]] = set()
    literals: set[tuple[int, int]] = set()
    for token in (t for t in tokens if t.type in ("inline", "html_block") and "<!--" in t.content):
        if "marks" not in token.meta:
            if token.type == "inline":
                token.meta["marks"] = _token_marks(lines, token)
            else:
                token.meta["marks"] = set(), _literals(token.content, token.map[0], _tag_spans(token.content))
        comments |= token.meta["marks"][0]
        literals |= token.meta["marks"][1]
    return comments, literals


def _tag_spans(text: str) -> list[tuple[int, int]]:
    """HTML の文 `text` のタグの (開始, 終了)。この中の `<!--`（属性値の中）はコメントを開かない。"""
    return [tag.span() for tag in _COMMENT_OR_TAG.finditer(text) if tag[1]]


def _markdown_spans(text: str) -> list[tuple[int, int]]:
    """Markdown の文 `text` の、中の `<!--` がコメントを開かない (開始, 終了)。

    コード表記・タグ・エスケープを、同じインライン解析の結果から読む。
    """
    spans = [(a, b) for is_html, a, b in _comment_spans(text) if not (is_html and text.startswith("<!", a))]
    escaped = (m.start() for m in re.finditer("<!--", text))
    return spans + [(at, at + 4) for at in escaped if (at - len(text[:at].rstrip("\\"))) % 2]


def _literals(text: str, first: int, spans: Iterable[tuple[int, int]]) -> set[tuple[int, int]]:
    """`spans` の中の `<!--` の位置（`_inline_marks` と同じ形）。`text` の先頭の行の添字は `first`。"""
    ends = [m.start() for m in re.finditer("\n", text)] + [len(text)]
    found = (mark.start() for begin, stop in spans for mark in re.compile("<!--").finditer(text, begin, stop))
    return {(first + k, ends[k] - at) for at in found for k in (bisect_left(ends, at),)}


def _token_marks(lines: Sequence[str] | Mapping[int, str], token: Token) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """インラインのトークン 1 つの `_inline_marks`。"""
    # 解析する文の各行は、元の行から字下げと引用などの記号を除いた後ろの部分
    places: list[tuple[int, int]] = []
    for i, piece in enumerate(token.content.split("\n"), token.map[0]):
        body = piece.lstrip()
        rest = len(lines[i]) - lines[i].rfind(body) + (len(piece) - len(body))
        places.extend((i, rest - col) for col in range(len(piece) + 1))
    comments: set[tuple[int, int]] = set()
    literals: set[tuple[int, int]] = set()
    for is_html, begin, stop in _comment_spans(token.content):
        if is_html:
            if token.content.startswith("<!--", begin):
                comments.add(places[begin])
        else:
            literals.update(places[m.start()] for m in re.compile("<!--").finditer(token.content, begin, stop))
    return comments, literals


def _scan_line(
    i: int,
    line: str,
    inside: bool,
    in_html: bool,
    marks: tuple[set[tuple[int, int]], set[tuple[int, int]]],
) -> tuple[str, bool, bool, Optional[int]]:
    """1 行の (HTML コメントを除いた文, 行の最初の文字がコメントの中か, 行の終わりがコメントの中か, 閉じた位置)。

    閉じた位置は、行の中で最後に閉じたコメントの直後の添字（閉じたコメントが無ければ None）。

    `<!--` がコメントを開くのは、行の最初の文字にあるか、HTML のブロックの中にあるか、インライン解析がコメントと読む
    ときだけで、ほかはただの文字とする。コード表記の中と、HTML のブロックのタグの属性値の中はコメントを開かない。
    `marks` は `_inline_marks` の結果。
    """
    comments, literals = marks
    kept, pos, head, closed = "", 0, inside, None
    while pos < len(line):
        if inside:
            end = line.find("-->", pos)
            inside = end < 0
            pos = len(line) if inside else end + 3
            closed = closed if inside else pos
            continue
        found = line.find("<!--", pos)
        if found < 0:
            kept += line[pos:]
            break
        at_head = not (kept + line[pos:found]).strip()
        place = (i, len(line) - found)
        if place not in literals and (at_head or in_html or place in comments):
            kept += line[pos:found]
            head = head or at_head
            # `<!-->` も閉じるように、開きの `<!` の直後から閉じを探す
            inside, pos = True, found + 2
            continue
        kept += line[pos : found + 4]
        pos = found + 4
    return kept, head, inside, closed


def _unfinished_tags(line: str, carried: Iterable[str] = ()) -> set[str]:
    """`line` の末尾で閉じていないタグの候補すべての、続きの行の判定に足りる短い書き出し。候補が無ければ空。

    どの `<` が本物のタグの開きかは続きの行まで読むまで決まらない（コード表記の中の書き方の例もある）ので、
    すべての開始位置の候補を残す。`carried` は前の行までの書き出しで、`書き出し + "\\n" + line` の先頭からだけ判定し、
    `line` の中の `<` は書き出しの数に関わらず 1 回ずつ判定する。読み終えた行を読み直さない。
    """
    starts = [(f"{head}\n{line}", 0) for head in carried] + [(line, mark.start()) for mark in re.finditer("<", line)]
    found = (_UNFINISHED_TAG.match(text, at) for text, at in starts)
    return {"<a b" + (tag[1] or "") + (tag[2] or "")[:1] for tag in found if tag}


def _breaks_block(state: StateBlock, line: int, end: int) -> bool:
    """`line` が、前の行から続く段落を markdown-it-py の段落の規則で切るか。

    HTML のブロックの規則は除く（属性値の中の行頭の `<!--` は、ブロックを始めない）。
    """
    if state.isEmpty(line):
        return True
    if state.sCount[line] - state.blkIndent > 3 or state.sCount[line] < 0:
        return False
    parent, state.parentType = state.parentType, "paragraph"
    try:
        return any(rule(state, line, end, True) for rule in state.md.block.ruler.getRules("paragraph") if rule is not _HTML_BLOCK)
    finally:
        state.parentType = parent


def _holding_comment(rule: Callable[[StateBlock, int, int, bool], bool]) -> Callable[[StateBlock, int, int, bool], bool]:
    """ブロックを開いたままのコメントかタグで終えるとき、閉じる行までを 1 つの HTML のブロックとして読む規則。

    コメントの中の囲みの記号を、閉じた後の行まで続く囲みにしない。属性値の中の行頭の `<!--` を、次のブロックの
    コメントの開きにしない。開いたままかは、`_split_comments` と同じく `_scan_line` で決める。
    コメントを閉じた後ろは、閉じた行の残りを解析する。次の行を足すのは、タグが閉じていないまま行が終わるときだけで、
    markdown-it-py がブロックの切れ目と読む行（空行・囲み・リストなど。HTML のブロックは除く）は越えない。
    読み足した行は元のブロックの外なので Markdown として読み、元の HTML のブロックの中で閉じたときは HTML として読む。
    """

    def run(state: StateBlock, start: int, end: int, silent: bool) -> bool:
        if not rule(state, start, end, silent):
            return False
        if silent:
            return True
        in_html = rule is html_block
        token = state.tokens[-1] if in_html else state.tokens[-2]
        if "<!--" not in token.content:
            return True
        rows = {n: state.src[state.bMarks[n] + state.tShift[n] : state.eMarks[n]] for n in range(*token.map)}
        marks = _inline_marks(rows, [token])
        inside, closed = False, None
        for n, text in rows.items():
            _, _, inside, closed = _scan_line(n, text, inside, in_html, marks)

        def full(n: int) -> str:
            return state.src[state.bMarks[n] : state.eMarks[n]]

        first = line = state.line
        literals: set[tuple[int, int]] = set()
        # 行をまたぐタグが届く最後の行
        reach = first - 1

        def read_after(at: int, tail: str, spans_of: Callable[[str], list[tuple[int, int]]]) -> None:
            nonlocal reach
            parts, heads, stop = [tail], _unfinished_tags(tail), at + 1
            while heads and stop < end and not _breaks_block(state, stop, end):
                parts.append(full(stop))
                heads = _unfinished_tags(parts[-1], heads)
                stop += 1
            text = "\n".join(parts)
            if "<!--" not in text:
                return
            spans = spans_of(text)
            found = _literals(text, at, spans)
            literals.update(found)
            chain = at
            breaks = [m.start() for m in re.finditer("\n", text)]
            for begin, last in sorted((at + bisect_left(breaks, a), at + bisect_left(breaks, b)) for a, b in spans):
                if begin <= chain < last:
                    chain = last
            if any(at < n <= chain for n, _ in found):
                reach = max(reach, chain)

        if in_html and not inside and closed is not None:
            read_after(first - 1, rows[first - 1][closed:], _tag_spans)
        while line < end and (inside or line <= reach):
            text = full(line)
            if inside and "-->" in text:
                read_after(line, text.partition("-->")[2], _markdown_spans)
            inside = _scan_line(line, text, inside, True, (set(), literals))[2]
            line += 1
        if line > first:
            held = state.push("html_block", "", 0)
            held.map = [first, line]
            held.content = state.getLines(first, line, state.blkIndent, True)
            held.meta["marks"] = set(), {mark for mark in literals if first <= mark[0] < line}
            state.line = line
        return True

    return run


# コードブロックと HTML のブロックの範囲を読む（リスト項目の中の字下げは、行頭からの字下げの数では判定できない）。
# ブロックの範囲だけを読むので、インラインは解析しない
_MARKDOWN = MarkdownIt("commonmark").disable(["inline", "text_join"])
_HTML_BLOCK = _holding_comment(html_block)
_MARKDOWN.block.ruler.at("html_block", _HTML_BLOCK, {"alt": ["paragraph", "reference", "blockquote"]})
_MARKDOWN.block.ruler.at("paragraph", _holding_comment(paragraph))
# セクションの終わり（`#` か `##` の見出し）
SECTION_END = re.compile(r"^#{1,2} ")
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+\.md)(?:#[^)]*)?\)")
_T = TypeVar("_T")


class DesignResult(NamedTuple):
    """課題 1 つの設計の結果。`units` は PR の単位の名前（触るファイルのセルの先頭の括弧）。"""

    issue: int
    files: tuple[str, ...]
    units: tuple[str, ...] = ()
    delegated: tuple[str, ...] = ()


def files_cell(cell: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """触るファイルのセルから、バッククォートで囲んだパスと、触るファイルを委ねたリンク先の文書（`.md`）を返す。

    リンクの外のバッククォートだけをパスとして拾う。委ねた文書が 1 つでもあれば、`design_gaps` が止める（#2453）。
    """
    links = tuple(dict.fromkeys(_LINK.findall(cell)))
    files = tuple(dict.fromkeys(_CODE.findall(_LINK.sub("", cell))))
    return files, links


def body_lines(body: str) -> list[str]:
    """本文を行へ分ける（CRLF は LF とみなす）。"""
    return body.replace("\r\n", "\n").split("\n")


def physical_lines(text: str) -> list[str]:
    """`body_lines` の行のうち、最後の改行の後ろを行に数えないもの（閉じていないコードブロックの空行にしない）。"""
    lines = body_lines(text)
    if lines[-1] == "":
        lines.pop()
    return lines


def heading_at(lines: Sequence[str], heading: str) -> Optional[int]:
    """囲みと HTML コメントの外で、行全体（行末の空白を除く）が `heading` と一致する最初の行の添字。無ければ None。"""
    found = _after_heading(lines, heading)
    return None if found is None else found[0]


def _after_heading(lines: Sequence[str], heading: str) -> Optional[tuple[int, list[tuple[int, str]], set[int]]]:
    """`heading_at` の添字、それより後ろの囲みと HTML コメントの外の行、コメントの行の添字（添字は `lines` のもの）。無ければ None。

    見出しの検索とセクションの読み出しが、1 回の解析を共有する。
    """
    fenced, commented = _hidden_lines(lines)
    visible = [(i, line) for i, line in enumerate(lines) if i not in fenced and i not in commented]
    return next(((i, visible[k + 1 :], commented) for k, (i, line) in enumerate(visible) if line.rstrip() == heading), None)


def section_lines(body: str, heading: str) -> Optional[list[str]]:
    """見出しのセクションの行（囲みと HTML コメントの外）。セクションが無ければ None。見出しは行全体が一致するものだけ。"""
    found = _after_heading(body_lines(body), heading)
    if found is None:
        return None
    out = []
    for _, line in found[1]:
        if SECTION_END.match(line):
            break
        out.append(line)
    return out


def _block_tokens(lines: Sequence[str]) -> list[Token]:
    """`lines` のブロックのトークンのうち、行の添字の範囲（`map`）を持つもの。"""
    # 行の中に残った CR は改行と数えられるので、添字がずれないように空白にする
    text = "\n".join(line.replace("\r", " ") for line in lines)
    return [t for t in _MARKDOWN.parse(text) if t.map]


def _span(tokens: Iterable[Token], *kinds: str) -> set[int]:
    """`kinds` の種類のトークンが占める行の添字。"""
    return {i for t in tokens if t.type in kinds for i in range(*t.map)}


def _split_comments(lines: Sequence[str], tokens: Sequence[Token]) -> list[tuple[str, bool, bool]]:
    """行ごとの (HTML コメントを除いた文, 行の最初の文字がコメントの中か, 読み始めの行の添字)。

    3 つめはその行の添字で、行をまたぐコメントの中の行は、コメントを開いた行の添字を閉じる行まで引き継ぐ。
    コードブロックの行はコメントを探さない。ほかの行は `_scan_line` で読む。
    """
    code = _span(tokens, "fence", "code_block")
    html = _span(tokens, "html_block")
    marks = _inline_marks(lines, tokens)
    out: list[tuple[str, bool, int]] = []
    inside, start = False, 0
    for i, line in enumerate(lines):
        if not inside:
            start = i
            if i in code:
                out.append((line, False, start))
                continue
        kept, head, inside, _ = _scan_line(i, line, inside, i in html, marks)
        out.append((kept, head, start))
    return out


def _analyze(lines: Sequence[str], *, terminated: bool = False) -> tuple[list[Token], list[tuple[str, bool, int]]]:
    """`lines` のブロックのトークンと、`_split_comments` の結果（解析は 1 回）。

    `terminated` が真なら最後の行も改行で終えて解析する（末尾の空行が、閉じていないコードブロックの本文に入る）。
    """
    # 行の中に残った CR は改行と数えられるので、添字がずれないように空白にする
    work = [line.replace("\r", " ") for line in lines]
    tokens = _block_tokens([*work, ""] if terminated else work)
    return tokens, _split_comments(work, tokens)


def _hidden_lines(lines: Sequence[str]) -> tuple[set[int], set[int]]:
    """読まない行の添字を (囲み, HTML コメント) で返す（記入例を本物の見出し・条件・表として読まない）。"""
    tokens, split = _analyze(lines)
    return _span(tokens, "fence"), {i for i, (_, head, _) in enumerate(split) if head}


def uncommented(lines: Sequence[str]) -> list[tuple[str, bool]]:
    """HTML コメントを除いた行を (文, コードブロックの中身か) で返す。コメントだけの行（中の空行を含む）は返さない。

    コードの行は、リストと囲みに属する字下げだけを除いたもので、コード本体の相対的な字下げと空行は残る。
    囲みの開閉の行はコードの中身にしない。コードの範囲は、コメントを除いた行で決める。コメントだけの行は、段落の中で
    始まったコメントのものは閉じる行まで詰め（段落が続くまま）、ほかは空行に置き換える（コメントが区切っていた段落と
    コードをつなげない）。
    """
    tokens, split = _analyze(lines, terminated=True)
    paragraph_lines = _span(tokens, "inline")
    kept = [not head or bool(text.strip()) for text, head, _ in split]
    rows = [(i, text if kept[i] else "") for i, (text, _, start) in enumerate(split) if kept[i] or start not in paragraph_lines]
    texts = [text for _, text in rows]
    if texts != list(lines):
        tokens = _block_tokens([*texts, ""])
    code: dict[int, str] = {}
    for token in (t for t in tokens if t.type in ("fence", "code_block")):
        start = token.map[0] + (token.type == "fence")
        content = token.content.removesuffix("\n").split("\n") if token.content else []
        code.update(enumerate(content, start))
    return [(code.get(n, text), n in code) for n, (i, text) in enumerate(rows) if kept[i]]


def outside_fences(lines: Iterable[str]) -> Iterator[tuple[int, str]]:
    """囲み（` ``` ` か `~~~` のコードブロック）と HTML コメントの外の行を `(添字 0 始まり, 行)` で返す。
    囲みの開閉の行と、最初の文字がコメントの中にある行は返さない。

    囲みの範囲は CommonMark の解析（markdown-it-py）で決める。リスト項目の中の囲みは、項目の本文の位置から字下げを数える。
    """
    found = list(lines)
    fenced, commented = _hidden_lines(found)
    for i, line in enumerate(found):
        if i not in fenced and i not in commented:
            yield i, line


def criteria(body: str) -> list[str]:
    """受け入れ条件のセクションのチェックボックスの先頭の行の文（上から）。セクションが無ければ空。"""
    return [block.split("\n", 1)[0].strip() for block in criteria_blocks(body)]


def criteria_blocks(body: str) -> list[str]:
    """受け入れ条件のセクションのチェックボックスを、字下げした継続の行を含めて返す（上から。番号の数え方は `criteria` と同じ）。

    先頭の行はチェックボックスの後ろの文、続く行は書かれたままで、どの行も改行で終える。空行では終わらず、字下げの無い行で終わる。
    字下げしたコードブロックの行も条件の続きに含める。コードブロックと HTML コメントの中の行は、条件の始まりにもセクションの終わりにもしない。
    空白と空行の整え方は、呼び手が決める。
    """
    lines = physical_lines(body)
    found = _after_heading(lines, HEADING)
    if found is None:
        return []
    at, visible, commented = found
    outside = {i for i, _ in visible}
    blocks: list[list[str]] = []
    inside = False
    for i, line in enumerate(lines[at + 1 :], at + 1):
        if i in outside:
            if SECTION_END.match(line):
                break
            box = _BOX.match(line)
            if box:
                blocks.append([box.group(1)])
                inside = True
                continue
        if inside:
            # コメントの行は、字下げが無くても条件を終わらせない（条件の版を作る側が除く）
            inside = i in commented or not line.strip() or line.startswith((" ", "\t"))
            if inside:
                blocks[-1].append(line)
    return ["".join(f"{line}\n" for line in block) for block in blocks]


def section_lack(body: str) -> Optional[str]:
    """受け入れ条件のセクションが無い・空なら理由。あれば None。"""
    if section_lines(body, HEADING) is None:
        return f"本文に {HEADING} のセクションが無い"
    if not criteria(body):
        return f"{HEADING} のセクションにチェックボックスの条件が無い"
    return None


# 表のセルの区切り（`\|` は区切りでなくセルの中の `|`）と、セルの中の改行の書き方
_CELL_SEP = re.compile(r"(?<!\\)\|")
_CELL_BREAK = "<br>"
_RULE_CELL = re.compile(r"^:?-+:?$")


def cell(value: str) -> str:
    """値を表の 1 つのセルへ書ける形にする（`|` は `\\|`、改行は `<br>`）。`cells` が元の値へ戻す。"""
    return value.replace("\r\n", "\n").replace("|", "\\|").replace("\n", _CELL_BREAK)


def split_row(line: str) -> list[str]:
    """Markdown の表の 1 行の列。エスケープしたパイプ（`\\|`）は列を区切らず、列の中では `|` に戻す。"""
    inner = line.strip().removeprefix("|")
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [c.strip().replace("\\|", "|") for c in _CELL_SEP.split(inner)]


def cells(line: str) -> list[str]:
    """表の 1 行のセル（`cell` で書いた `\\|` と `<br>` は元の `|` と改行へ戻す）。"""
    return [c.replace(_CELL_BREAK, "\n") for c in split_row(line)]


def section_span(lines: Sequence[str], heading: str) -> Optional[tuple[int, int]]:
    """見出しのセクションの本文の行の範囲（見出しの次の行から、囲みと HTML コメントの外の次の `#`・`##` の見出しの前まで）。無ければ None。"""
    found = _after_heading(lines, heading)
    if found is None:
        return None
    at, visible, _ = found
    end = next((i for i, line in visible if SECTION_END.match(line)), len(lines))
    return at + 1, end


def checks_table_spans(lines: Sequence[str]) -> list[tuple[int, int]]:
    """`## 受け入れ条件の確認` のセクションの、囲みの外の表の行の範囲（終わりは含まない）を上から。セクション・表が無ければ空。"""
    return _checks_spans(lines)[0]


def _checks_spans(lines: Sequence[str]) -> tuple[list[tuple[int, int]], set[int]]:
    """`checks_table_spans` の範囲と、そのセクションの囲みと HTML コメントの外の行の添字（解析は 1 回）。"""
    found = _after_heading(lines, CHECKS_HEADING)
    if found is None:
        return [], set()
    at, visible, _ = found
    end = next((i for i, line in visible if SECTION_END.match(line)), len(lines))
    spans: list[tuple[int, int]] = []
    for i, line in visible:
        if i >= end:
            break
        if not line.strip().startswith("|") or (spans and i < spans[-1][1]):
            continue
        last = next((j for j in range(i, end) if not _table_line(lines[j], j > i)), end)
        spans.append((i, last))
    return spans, {i for i, _ in visible if at < i < end}


def _table_line(line: str, after_row: bool) -> bool:
    """表の続きの行か。`|` で始まる行のほか、表の行の直後の区切りの行（`--- | ---` のように先頭の `|` を省いた形）も含める。

    区切りの行で表を分けると、見出しとデータ行が別の表になり、データ行を先頭の表の列の順で読み違える（#3886 の r7-1）。"""
    stripped = line.strip()
    return stripped.startswith("|") or (after_row and "|" in stripped and _is_rule(stripped))


def checks_table_span(lines: Sequence[str]) -> Optional[tuple[int, int]]:
    """`## 受け入れ条件の確認` のセクションの、囲みの外の最初の表の行の範囲（終わりは含まない）。セクション・表が無ければ None。

    書き換える（`acceptance_decide.recorded_body`）のはこの表だけ。読む（`check_rows`）ときもこの表の番号を後続の表より
    優先するので、書き換えた行が後続の表の古い行で上書きされない（#3886 の r4-1）。"""
    spans = checks_table_spans(lines)
    return spans[0] if spans else None


def _is_rule(line: str) -> bool:
    """表の見出しの下の区切りの行（`| :--- | ---: |` のようにすべてのセルが `-` と `:` だけ）か。"""
    found = cells(line)
    return bool(found) and all(_RULE_CELL.match(c) for c in found)


def check_rows(body: str, head: Sequence[str], valid: Callable[[list[str]], bool] = lambda row: True) -> list[tuple[int, list[str]]]:
    """`## 受け入れ条件の確認` の表のデータ行を、(条件の番号, `head` の列の順に並べたセル) で上から返す。

    `head` の先頭は番号の列。最初の表（`checks_table_span`）の最初の行を見出し、次の行を区切りとして読む。最初の表が無いか、
    見出しに `head` の列が 1 つでも無ければ空。後続の表は、見出し（最初の行の次が区切りの行）があれば、`head` の列が
    そろうときだけその見出しの列で読み、そろわない表は別の表として読み飛ばす（#3886 の r6-2）。見出しの無い表だけを最初の
    表の見出しの列の順で読む。後続の表からは、それより前の表で `valid` な記録の得られた番号の行を除いて足す（重複は先頭の表を
    優先し、後続の表にしかない記録や、前の表の行が無効な番号の記録は引き継ぐ。#3886 の r4-1・r5-1・r6-1）。番号が読めない
    行・列が足りない行は飛ばす。`valid` は `head` の順に並べたセルを受け取り、記録として有効かを返す。
    """
    out: list[tuple[int, list[str]]] = []
    seen: set[int] = set()
    for k, (rows, cols) in enumerate(_check_tables(body_lines(body), head)):
        table_rows = []
        for row in rows:
            found = cells(row)
            num = re.match(r"^(\d+)", found[cols[0]]) if len(found) > max(cols) else None
            if num and (k == 0 or int(num.group(1)) not in seen):
                table_rows.append((int(num.group(1)), [found[i] for i in cols]))
        out.extend(table_rows)
        seen.update(n for n, row in table_rows if valid(row))
    return out


def _check_tables(lines: Sequence[str], head: Sequence[str]) -> list[tuple[list[str], list[int]]]:
    """`check_rows` が読む表を、(データ行, `head` の順の列の添字) で上から返す。先頭は常に最初の表。

    最初の表が無いか、2 行に満たないか、見出しに `head` の列が 1 つでも無ければ空。後続の表は、見出しがあって `head` の列が
    そろわなければ除き、見出しが無ければ最初の表の列の順で読む。"""
    spans, visible = _checks_spans(lines)
    # 表の範囲に挟まったコメントの中の行は、記録として読まない
    tables = [[lines[i] for i in range(a, b) if i in visible] for a, b in spans]
    if not tables or len(tables[0]) < 2:
        return []
    first_head = cells(tables[0][0])
    if any(h not in first_head for h in head):
        return []
    base = [first_head.index(h) for h in head]
    out: list[tuple[list[str], list[int]]] = []
    for k, rows in enumerate(tables):
        # 見出しは、次の行が区切りで列数も同じときだけ。見出しの無い表のデータ行の直後の区切り（列数が違う）を見出しと
        # 取り違えると、表全体を読み飛ばす（#3886 の r8-1）
        headed = k == 0 or (len(rows) >= 2 and _is_rule(rows[1]) and len(cells(rows[1])) == len(cells(rows[0])))
        found_head = cells(rows[0])
        if headed and any(h not in found_head for h in head):
            continue
        cols = [found_head.index(h) for h in head] if headed else base
        out.append((rows[2:] if headed else rows, cols))
    return out


def check_values(body: str, head: Sequence[str], parse: Callable[[list[str]], Optional[_T]]) -> dict[int, _T]:
    """`check_rows` の行を `parse`（`head` の順のセルを受け取り、無効なら None）で解釈した {条件の番号: 値}。

    無効な行は読まない。同じ番号の行が続けば後の行が勝つ。
    """
    out: dict[int, _T] = {}
    for num, row in check_rows(body, head, lambda row: parse(row) is not None):
        value = parse(row)
        if value is not None:
            out[num] = value
    return out


def check_kinds(body: str) -> dict[int, str]:
    """`## 受け入れ条件の確認` の表の {条件の番号: 区分}。表が無ければ空。区分の語が違う行は読まない。"""
    return check_values(body, CHECKS_HEAD, lambda row: row[1] if row[1] in KINDS else None)


def kinds_lack(body: str) -> list[int]:
    """区分の無い受け入れ条件の番号（1 から）。確認の表が無ければ、すべての条件が区分を欠く。"""
    kinds = check_kinds(body)
    return [n for n in range(1, len(criteria(body)) + 1) if n not in kinds]


def absorbed_missing(target_body: str, absorbed: Mapping[int, str]) -> dict[int, list[str]]:
    """統合した課題の受け入れ条件のうち、統合先の本文の受け入れ条件に無い文を、課題ごとに返す（#2274）。"""
    present = set(criteria(target_body))
    out = {}
    for issue, body in absorbed.items():
        lost = [c for c in criteria(body) if c not in present]
        if lost:
            out[issue] = lost
    return out


def design_gaps(approved: Sequence[int], results: Mapping[int, Optional[DesignResult]]) -> list[str]:
    """承認済みの課題の設計の結果から、実装のプランを組めない理由を並べる（#2596・#2539・#2453）。"""
    reasons = []
    for issue in approved:
        found = results[issue] if issue in results else None
        if found is None:
            reasons.append(f"#{issue} の設計の結果を読めない（実装を外したまま進めない）")
        elif found.delegated:
            reasons.append(f"#{issue} の触るファイルを {'、'.join(found.delegated)} へ委ねている（設計の結果の表のセルへ直に挙げる）")
        elif len(set(found.units)) > 1:
            reasons.append(f"#{issue} の設計が PR を {len(set(found.units))} つに分けている（課題ごとに 1 本のプランで組めない）")
    return reasons


def merged_files(results: Sequence[DesignResult]) -> tuple[str, ...]:
    """主題で束ねた課題の、触るファイルの和（並びは最初に現れた順。#2056）。"""
    return tuple(dict.fromkeys(f for r in results for f in r.files))


def path_present(name: str, files: Sequence[str], subdir: str = "") -> bool:
    """テストの名前 `name` が実在のファイルを指すか。リポジトリの根からの相対・`subdir` からの相対・末尾の一致のどれか。"""
    name = name.strip().removeprefix("./")
    prefix = subdir.strip("/") + "/" if subdir.strip("/") else ""
    return any(f in (name, prefix + name) or f.endswith("/" + name) for f in files)
