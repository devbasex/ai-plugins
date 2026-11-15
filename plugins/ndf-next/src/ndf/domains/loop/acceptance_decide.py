"""課題の本文の受け入れ条件と、その確認の区分の記録の読み書き（#2144・#1682。GitHub を呼ばない）。

受け入れ条件の番号は、`## 受け入れ条件` の節のコードブロックの外にある、行頭（字下げなし）のチェックボックスの行
（`- [ ]` / `- [x]`）を上から数えた順（1 から）である。

確認の区分の記録は `## 受け入れ条件の確認` の節の表（`| 条件 | 区分 | 手段 |`）で、区分は `マージ前`（マージ前に
確かめた。手段にテスト名か実行したコマンドを書く）か `リリース後`（リリースの後でしか確かめられない）である。
手段の無い `マージ前` の行は確かめていないので読まない。

実装のプランは worker の後に照合（`unrecorded`）を打ち、区分の記録の無い条件が残れば完了で返さない（#1682）。
要求の手動確認（マージ前）を行えなかった worker は手段を書けないので、その条件は記録の無いまま残る。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import NamedTuple, Optional

HEADING = "## 受け入れ条件"
CHECKS_HEADING = "## 受け入れ条件の確認"
PRE_MERGE = "マージ前"
POST_RELEASE = "リリース後"
KINDS = (PRE_MERGE, POST_RELEASE)
CHECKS_HEAD = ("条件", "区分", "手段")
NO_MEANS = "—"
_BOX = re.compile(r"- \[([ xX])\](?=[ \t]|$)")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_NUMBER = re.compile(r"^(\d+)")


class AcceptanceError(Exception):
    """記録の引数の誤り（受け入れ条件に無い番号・両方の区分に出る番号・本文に受け入れ条件が無い）。"""


class Criterion(NamedTuple):
    number: int
    checked: bool
    text: str


class Check(NamedTuple):
    """1 つの受け入れ条件の確認の区分。means はマージ前に確かめた手段（リリース後なら空）。"""

    kind: str
    means: str


def _lines(body: str) -> list[str]:
    return body.replace("\r\n", "\n").split("\n")


def _section(lines: list[str], heading: str) -> Optional[tuple[int, int]]:
    """見出しがちょうど `heading` の節の本文の行の範囲（見出しの次の行から、次の同じか上の階の見出しの前まで）。"""
    level = heading.index(" ")
    inside, start, fenced = False, 0, False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
        if fenced or not line.startswith("#"):
            continue
        marks = len(line) - len(line.lstrip("#"))
        if inside and marks <= level:
            return start, i
        if line.rstrip() == heading:
            inside, start = True, i + 1
    return (start, len(lines)) if inside else None


def _unfenced(lines: list[str], bounds: tuple[int, int]) -> list[str]:
    out, fenced = [], False
    for line in lines[bounds[0] : bounds[1]]:
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            out.append(line)
    return out


def criteria(body: str) -> list[Criterion]:
    """受け入れ条件の番号・印・チェックボックスの後の文。節が無ければ空。"""
    lines = _lines(body or "")
    bounds = _section(lines, HEADING)
    if bounds is None:
        return []
    out: list[Criterion] = []
    for line in _unfenced(lines, bounds):
        m = _BOX.match(line)
        if m:
            out.append(Criterion(len(out) + 1, m.group(1) != " ", line[m.end() :].strip()))
    return out


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _parse_row(cells: list[str], cols: tuple[int, int, int]) -> Optional[tuple[int, Check]]:
    if len(cells) <= max(cols):
        return None
    num, kind, means = _NUMBER.match(cells[cols[0]]), cells[cols[1]], cells[cols[2]].strip("`").strip()
    if not num or kind not in KINDS:
        return None
    if kind == PRE_MERGE and (not means or means == NO_MEANS):
        return None
    return int(num.group(1)), Check(kind, "" if kind == POST_RELEASE else cells[cols[2]])


def read_checks(body: str) -> dict[int, Check]:
    """`## 受け入れ条件の確認` の表を {条件の番号: Check} で返す。節・表・列が無ければ空。"""
    lines = _lines(body or "")
    bounds = _section(lines, CHECKS_HEADING)
    rows = [line for line in _unfenced(lines, bounds) if line.strip().startswith("|")] if bounds else []
    if len(rows) < 2:
        return {}
    head = _cells(rows[0])
    if any(h not in head for h in CHECKS_HEAD):
        return {}
    cols = (head.index(CHECKS_HEAD[0]), head.index(CHECKS_HEAD[1]), head.index(CHECKS_HEAD[2]))
    out: dict[int, Check] = {}
    for row in rows[2:]:
        parsed = _parse_row(_cells(row), cols)
        if parsed is not None:
            out[parsed[0]] = parsed[1]
    return out


def unrecorded(body: str) -> list[Criterion]:
    """確認の区分の記録が無い受け入れ条件（手段の無いマージ前の行も記録が無いとみなす）。"""
    checks = read_checks(body)
    return [c for c in criteria(body) if c.number not in checks]


def merged(body: str, pre_merge: Mapping[int, str], post_release: Iterable[int]) -> dict[int, Check]:
    """本文の今の記録へ渡した区分を重ねた記録。受け入れ条件に無い番号・両方に出る番号は `AcceptanceError`。"""
    numbers = {c.number for c in criteria(body)}
    if not numbers:
        raise AcceptanceError("本文に受け入れ条件（`## 受け入れ条件` のチェックボックス）が無い")
    post = set(post_release)
    both = sorted(post & set(pre_merge))
    if both:
        raise AcceptanceError(f"マージ前とリリース後の両方に渡した番号: {both}")
    unknown = sorted((post | set(pre_merge)) - numbers)
    if unknown:
        raise AcceptanceError(f"受け入れ条件に無い番号: {unknown}（本文の条件は 1〜{max(numbers)}）")
    checks = read_checks(body)
    checks.update({n: Check(PRE_MERGE, m) for n, m in pre_merge.items()})
    checks.update({n: Check(POST_RELEASE, "") for n in post})
    return checks


def checks_table(checks: Mapping[int, Check]) -> str:
    """`## 受け入れ条件の確認` の節の中身（番号の順の表）。"""
    head = "| " + " | ".join(CHECKS_HEAD) + " |"
    rule = "| " + " | ".join(":---" for _ in CHECKS_HEAD) + " |"
    rows = [f"| {n} | {c.kind} | {c.means if c.kind == PRE_MERGE else NO_MEANS} |" for n, c in sorted(checks.items())]
    return "\n".join([head, rule, *rows])


def recorded_body(body: str, pre_merge: Mapping[int, str], post_release: Iterable[int]) -> str:
    """記録を書いた本文。節が無ければ末尾へ足し、あれば節の中身だけを置き換える。節の外は変えない。"""
    table = checks_table(merged(body, pre_merge, post_release))
    lines = _lines(body)
    bounds = _section(lines, CHECKS_HEADING)
    if bounds is None:
        return body.rstrip("\n") + f"\n\n{CHECKS_HEADING}\n\n{table}\n"
    start, end = bounds
    tail = lines[end:]
    return "\n".join([*lines[:start], "", table, *([""] if tail else []), *tail])
