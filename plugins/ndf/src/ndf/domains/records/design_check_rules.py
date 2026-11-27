"""設計の検査の判定（`ndf records design check` と MVV 判定の表の確かめ。#2912 の決定 1・7）。

詳細設計書は名前を `design_check_decide` としたが、Markdown の解析（`ndf.adapters.markdown`）を読むため、判断のモジュールの
規則（`decide-pure`。名前が `_decide` で終わるモジュールは `ndf.adapters` を読まない）に合わせて名前を変えた。中身は副作用の無い判定である。

文書の本文から、レッドラインの番号を示さずに判断を人に任せる行（番号の無い委ね方）と、「人へ残す判断」の表の行を判定する。
ファイル・git・GitHub を読まない。委ねる語・番号・見る範囲の定数はこの 1 か所にだけ置き、MVV 判定（`mvv_gate`）も
`redline_rows` を呼んで同じ定数を使う。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import NamedTuple, Optional

from ndf.adapters import markdown

# 見る範囲のセクション（見出しの題がこれで始まるセクションとその下位）
SECTIONS = ("決定の記録", "未確認のまま残ること", "人へ残す判断")
TABLE_SECTION = "人へ残す判断"
# 「人へ残す判断」の表の欄の見出し（判断・番号・理由の順）
COLUMNS = ("判断", "当たるレッドライン", "なぜ当たるか")
RULE = "unnamed_redline"

# 委ねる語（設計書の「入出力のコントラクト」の式。10 系の design-survey.py の DELEGATE を元に、済んだ記録と否定を除く）
_SUBJECT = r"(?:利用者|(?<![一-龠々])人)"
_DENIAL = r"(?:必要|こと|まで|の)(?:では|で|は|が|も)?(?:ない|無い|なく|なし|ありません|しない|せず)(?!か)"
_DONE = r"(?:た|ました|んだ|びました|定した|定しました|択した|択しました)(?!まま)"
_NEGATION = r"(?:択|定)?(?:し|せ|び|ば)?(?:ず|ない|なく|ません)(?!か)"
DELEGATE = re.compile(
    "|".join(
        (
            _SUBJECT + r"が(?:後で|あとで|後から|別途|改めて)?"
            r"(?:決める|決めます|選ぶ|選びます|選択する|選択します|確かめる|確かめます|確認する|確認します|判断する|判断します|承認する|承認します)"
            rf"(?!{_DENIAL})",
            _SUBJECT + rf"(?:へ|に)委ね(?:られ)?(?:る(?!{_DENIAL})|ます|ている|ています|ておく|ておきます|たまま)",
            _SUBJECT + rf"の(?:判断|決定|確認|選択|回答|承認)を待(?:つ(?!{_DENIAL})|ちます|っている|っています)",
            r"承認ゲート(?:\s*\d+\s*)?で" + _SUBJECT + rf"が(?:決め|選|確かめ)(?!{_DONE}|{_NEGATION})(?![ぁ-ん]*{_DENIAL})",
        )
    )
)
REDLINE_NO = re.compile(r"(?<![A-Za-z0-9])[CPR][0-9]+(?![0-9])")
# 番号の直後の打ち消し（インラインコードの閉じの `` ` `` を挟んでもよい）
NEGATED = re.compile(r"`?\s*(?:に当たらない|には当たらない|に該当しない)")

_INLINE_CODE = re.compile(r"(`+)(.+?)\1")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_SYMBOLS = re.compile(r"[\s`*_]")
_BLANK_SYMBOLS = re.compile(r"[\s`*_\-—]")


class DecisionRow(NamedTuple):
    """「人へ残す判断」の表の 1 行。`line` は 1 始まりの行番号。"""

    line: int
    decision: str
    numbers: tuple[str, ...]
    reason: str


class Finding(NamedTuple):
    """指摘の 1 件。`line` は 1 始まりの行番号、`text` は行の文。"""

    path: str
    line: int
    text: str


def blank(cell: str) -> bool:
    """空白と記号 `` ` `` `*` `_` `-` `—` を除くと何も残らないか（実質空）。"""
    return not _BLANK_SYMBOLS.sub("", cell)


def _normal(text: str) -> str:
    return _SYMBOLS.sub("", text)


def numbers_in(text: str, *, negatable: bool) -> tuple[str, ...]:
    """文のレッドラインの番号を出てくる順に重複なく返す。`negatable` なら直後に打ち消しが続く番号を除く。"""
    seen: dict[str, None] = {}
    for m in REDLINE_NO.finditer(text):
        if negatable and NEGATED.match(text, m.end()):
            continue
        seen.setdefault(m.group(0), None)
    return tuple(seen)


def delegates(line: str) -> bool:
    """インラインコードを除いた行が委ねる語に当たるか。"""
    return DELEGATE.search(_INLINE_CODE.sub("", line)) is not None


def _ranges(text: str, titles: Sequence[str]) -> list[tuple[int, int]]:
    """題が `titles` のどれかで始まる見出しのセクションの行の範囲（0 始まり、終わりを含まない）。"""
    heads = markdown.atx_headings(text)
    end_of_text = len(text.splitlines())
    out = []
    for i, h in enumerate(heads):
        if not h.title.strip().startswith(tuple(titles)):
            continue
        end = next((n.line for n in heads[i + 1 :] if n.level <= h.level), end_of_text)
        out.append((h.line, end))
    return out


def _within(line: int, ranges: Sequence[tuple[int, int]]) -> bool:
    return any(start <= line < end for start, end in ranges)


def decision_rows(text: str) -> list[DecisionRow]:
    """「人へ残す判断」で始まるセクションの表の行を、判断・番号・理由の欄に分けて返す。欠けた欄は空の文にする。"""
    ranges = _ranges(text, (TABLE_SECTION,))
    rows: list[DecisionRow] = []
    for table in markdown.gfm_tables(text):
        if not _within(table.line, ranges):
            continue
        header = [_normal(c) for c in table.header]
        index = [header.index(c) if c in header else None for c in COLUMNS]
        for row in table.rows:
            decision, number, reason = (row.cells[i] if i is not None and i < len(row.cells) else "" for i in index)
            rows.append(DecisionRow(row.line + 1, decision, numbers_in(number, negatable=False), reason))
    return rows


def redline_rows(text: str) -> list[DecisionRow]:
    """`decision_rows` のうち、番号の欄にレッドラインの番号を 1 つ以上持つ行（MVV 判定の表の確かめが使う）。"""
    return [r for r in decision_rows(text) if r.numbers]


def redline_numbers(text: str) -> tuple[str, ...]:
    """`redline_rows` の行が持つレッドラインの番号を、出てくる順に重複なく返す。"""
    return tuple(dict.fromkeys(n for r in redline_rows(text) for n in r.numbers))


def _anchors(rows: Sequence[DecisionRow]) -> list[DecisionRow]:
    """対応付けの相手にできる行（判断と理由が実質空でなく、番号を持つ）。"""
    return [r for r in rows if r.numbers and not blank(r.decision) and not blank(r.reason)]


def named_in_table(line: str, rows: Sequence[DecisionRow]) -> bool:
    """委ねる行が、表のどれか 1 行の「判断」の文と、その行と同じ打ち消されていない番号を両方含むか。"""
    numbers = set(numbers_in(line, negatable=True))
    plain = _normal(line)
    return any(numbers & set(r.numbers) and _normal(r.decision) in plain for r in _anchors(rows))


def _unfilled(row: DecisionRow) -> bool:
    return blank(row.decision) or not row.numbers or blank(row.reason)


def findings(text: str, path: str, lines: Optional[frozenset[int]] = None) -> list[Finding]:
    """見る範囲のセクションで、番号の無い委ね方の行と、欄を欠く「人へ残す判断」の表の行を返す。

    `lines` があれば、その行番号（1 始まり）の行だけを返す。コードブロックの中の行は数えない。同じ行の指摘は 1 件にする。
    """
    all_lines = text.splitlines()
    ranges = _ranges(text, SECTIONS)
    code = markdown.code_lines(text)
    rows = decision_rows(text)
    table_rows = {r.line: r for r in rows}
    out: list[Finding] = []
    for i, line in enumerate(all_lines):
        number = i + 1
        if lines is not None and number not in lines:
            continue
        if not _within(i, ranges) or (i < len(code) and code[i]):
            continue
        row = table_rows.get(number)
        if (row is not None and _unfilled(row)) or (delegates(line) and not named_in_table(line, rows)):
            out.append(Finding(path, number, line.strip()))
    return out


def _pairs(text: str) -> set[tuple[str, str]]:
    return {(_normal(r.decision), n) for r in _anchors(decision_rows(text)) for n in r.numbers}


def anchors_lost(before: str, after: str) -> bool:
    """分岐点の本文 `before` の表で対応付けに使えた（判断, 番号）の組が、worktree の本文 `after` の表から失われたか。"""
    return not _pairs(before) <= _pairs(after)


def _hunks(patch: str) -> Optional[list[tuple[int, int, int, int]]]:
    """`git diff -U0` の hunk の見出しの（古い始まり, 古い件数, 新しい始まり, 新しい件数）。読めない見出しがあれば None。"""
    out = []
    for line in patch.splitlines():
        if not line.startswith("@@"):
            continue
        m = _HUNK.match(line)
        if m is None:
            return None
        a, b, c, d = m.groups()
        out.append((int(a), 1 if b is None else int(b), int(c), 1 if d is None else int(d)))
    return out


def added_lines(patch: str) -> Optional[frozenset[int]]:
    """1 ファイル分の `git diff -U0` から、足した・変えた行の新しい側の行番号。読めない hunk の見出しがあれば None。"""
    hunks = _hunks(patch)
    if hunks is None:
        return None
    return frozenset(n for _, _, start, count in hunks for n in range(start, start + count))


def heading_touched(before: str, after: str, patch: str) -> bool:
    """差分が見出しの行を足した・変えた・消したか。読めない hunk の見出しがあれば真。"""
    hunks = _hunks(patch)
    if hunks is None:
        return True
    new = {h.line + 1 for h in markdown.atx_headings(after)}
    old = {h.line + 1 for h in markdown.atx_headings(before)}
    return any(any(n in new for n in range(c, c + d)) or any(n in old for n in range(a, a + b)) for a, b, c, d in hunks)
