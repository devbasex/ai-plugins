"""課題の本文の受け入れ条件の節の読み取りと印付け（#1683）。GitHub を呼ばない。

受け入れ条件の番号は、`## 受け入れ条件` の節のコードブロックの外にある、行頭（字下げなし）の
チェックボックスの行（`- [ ]` / `- [x]`）を上から数えた順（1 から）である。行頭に書いた番号は読まない
（設計の決定 2）。取り消し線で差し替えた行（`- ~~…~~ → …`）はチェックボックスを持たないため数えない。

印付けは元の本文の文字の位置で行い、チェックボックスの `[ ]` の空白 1 文字だけを `x` に置き換える。
節の外・説明の行・改行の形（`\\r\\n`）は変えない（決定 3）。
"""

from __future__ import annotations

import re
from itertools import accumulate
from typing import NamedTuple

import gh_sections

HEADING = "## 受け入れ条件"
_BOX = re.compile(r"- \[([ xX])\](?=[ \t\r]|$)")


class Criterion(NamedTuple):
    number: int
    checked: bool


class _Box(NamedTuple):
    number: int
    checked: bool
    at: int  # 印の 1 文字（`[ ]` の空白）の位置


def _boxes(body: str) -> list[_Box]:
    bounds = gh_sections.section_bounds(body, HEADING)
    if bounds is None:
        return []
    start, end = bounds
    out: list[_Box] = []
    lines = body[start:end].split("\n")
    pos = list(accumulate((len(line) + 1 for line in lines), initial=start))  # 各行の始まりの位置（囲みの行も数える）
    for i, line in gh_sections.unfenced_lines(lines):
        b = _BOX.match(line)
        if b:
            out.append(_Box(len(out) + 1, b.group(1) != " ", pos[i] + b.start(1)))
    return out


def criteria(body: str) -> list[Criterion]:
    """受け入れ条件の番号と印の有無。節が無ければ空。"""
    return [Criterion(b.number, b.checked) for b in _boxes(body)]


def check_criteria(body: str, numbers) -> str:
    """`numbers` の受け入れ条件の `[ ]` を `[x]` にした本文。既に印のある行・範囲外の番号は変えない。"""
    want = set(numbers)
    chars = list(body)
    for b in _boxes(body):
        if b.number in want and not b.checked:
            chars[b.at] = "x"
    return "".join(chars)


def only_marks(old: str, new: str) -> bool:
    """`new` と `old` の違いが、受け入れ条件の印の空白を `x` にしたことだけか（書く前の確かめ）。"""
    if len(old) != len(new):
        return False
    allowed = {b.at for b in _boxes(old) if not b.checked}
    return all(i in allowed and new[i] == "x" for i, (o, n) in enumerate(zip(old, new)) if o != n)


def criteria_lines(body: str) -> list[tuple[Criterion, str]]:
    """受け入れ条件の番号・印と、チェックボックスの後の文（#1488 の未実施のリリース後テストの行に使う）。"""
    out = []
    for b in _boxes(body):
        start = body.rfind("\n", 0, b.at) + 1
        end = body.find("\n", b.at)
        line = body[start : len(body) if end < 0 else end].rstrip("\r")
        out.append((Criterion(b.number, b.checked), _BOX.sub("", line, count=1).strip()))
    return out


# --- リリース後テストの表（sprint-close.py の閉じる判定と release-steps.py の承認の材料が同じ規則で読む。#1885 の決定 8）


class IssueRows(NamedTuple):
    """1 課題のリリース後テストの行のまとめ。passed は合格の行にあり、合格でない行に無い受け入れ条件の番号。"""

    all_pass: bool
    passed: frozenset


_ISSUE_CELL = re.compile(r"^(?:([A-Za-z0-9._-]+/[A-Za-z0-9._-]+))?#(\d+)")
_CRITERION_CELL = re.compile(r"^(\d+)\.")


def verification_verdicts(block, record_repo):
    """表を課題でまとめ、{(repo, number): IssueRows} を返す。表が読めない・課題を指す行が無ければ None。

    表は lib/md.py（GFM）で読む。欠けたセルは空として読むため、`結果` の欠けた行は合格でない。
    `課題` の列の頭が課題の参照でない行（導入の確認の `—`・`PR 1869` など）は飛ばす（#1683 の I1）。
    `受け入れ条件` の列の頭の `<番号>.` を受け入れ条件の番号として読む。使う側は `deps.require("md")` を先に呼ぶ。"""
    import md

    found = md.tables(block)
    if not found:
        return None
    head = [c.strip() for c in found[0].header]
    if "課題" not in head or "結果" not in head:
        return None
    ci, ri = head.index("課題"), head.index("結果")
    ai = head.index("受け入れ条件") if "受け入れ条件" in head else None
    acc = {}
    for row in found[0].rows:
        cs = [c.strip() for c in row]  # md は行のセルを見出しの数へ揃える
        m = _ISSUE_CELL.match(cs[ci])
        if not m:
            continue
        key = (m.group(1) or record_repo, int(m.group(2)))
        ok = cs[ri].startswith("合格")
        all_pass, passed, failed = acc.get(key, (True, set(), set()))
        num = _CRITERION_CELL.match(cs[ai]) if ai is not None else None
        if num:
            (passed if ok else failed).add(int(num.group(1)))
        acc[key] = (all_pass and ok, passed, failed)
    if not acc:
        return None
    return {k: IssueRows(a, frozenset(p - f)) for k, (a, p, f) in acc.items()}


_VERSION = re.compile(r"v?(\d+\.\d+\.\d+)")


def version_verdicts(text, version, record_repo):
    """PR の本文とコメント（text）の `## リリース後テスト` のブロックのうち、`対象の版:` の基底が version の基底と
    一致する最後のブロックの表を読む（{(repo, number): IssueRows}）。無い・読めなければ空。"""
    import dist_record
    import md

    want = _VERSION.match(str(version))
    lines = text.splitlines()
    sections = [s for s in md.md_sections(text) if lines[s.heading.line].startswith("#")]
    blocks = [
        b
        for b in dist_record._verify_blocks(lines, sections)
        if want and (m := _VERSION.match(b["ver"] or "")) and m.group(1) == want.group(1)
    ]
    return (verification_verdicts("\n".join(blocks[-1]["lines"]), record_repo) or {}) if blocks else {}
