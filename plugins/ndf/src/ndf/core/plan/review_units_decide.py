"""実装計画の「レビューの単位」の表の読み手（純粋な関数。#4369 の決定 2・7・9）。

本文から単位の並びか、読めない理由を返す。ファイルも git も読まない。読み方は #4369 の設計書の「単位の表の読み方」と
「順序」にあり、決めた形でない表は行を飛ばさず `UnitsUnreadable` で止める。実装計画の見出しの語もここが持つ。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import NamedTuple, Optional

from ndf.core.plan import inputs_decide

UNITS_HEADING = "レビューの単位"
TASKS_HEADING = "タスク分解"
# 「リファクタリングの時機」の表（時機 | 対象 | 理由）。工程「リファクタリング」の差し込みもこの見出しと「しない」を読む
TIMING_HEADING = "リファクタリングの時機"
BEFORE_IMPL = "実装の前"
NO_TARGET = "しない"
NAME_COLUMN = "単位"
TASKS_COLUMN = "含むタスク"
CRITERIA_COLUMN = "満たす受け入れ条件"
ORDER_COLUMN = "実行順"
REQUIRED_COLUMNS = (NAME_COLUMN, TASKS_COLUMN, CRITERIA_COLUMN)
NO_CRITERIA = "無し"

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$")
_NOTE = re.compile(r"（[^（）]*）|\([^()]*\)")
_SEPARATOR = re.compile(r"[・、,]")
_TASK_ITEM = re.compile(r"(?P<prefix>Task |T)?(?P<first>[0-9]+)(?:〜(?:Task |T)?(?P<last>[0-9]+))?")
_CRITERIA_ITEM = re.compile(r"(?:(?P<issue>[0-9]+):)?(?P<first>[0-9]+)(?:〜(?P<last>[0-9]+))?")
_TASK_TITLE = re.compile(r"Task ([0-9]+)(?![0-9])")


class PlanDoc(NamedTuple):
    """実装計画 1 つ。`issues` はファイル名の番号。"""

    path: str
    issues: frozenset[int]
    text: str


class Unit(NamedTuple):
    """レビューの単位 1 つ。`issue` は代表の課題、`criteria` は (課題, 条件の番号) の並び。

    `supplemented` は単位の見出しを持たない課題を課題 1 つの 1 単位として補ったもので、`plan` は空になる。
    """

    name: str
    issue: int
    plan: str
    tasks: tuple[int, ...]
    criteria: tuple[tuple[int, int], ...]
    supplemented: bool


class UnitsUnreadable(Exception):
    """単位の表を読めない。`reasons` は `<パス>: <理由>` の並び。"""

    def __init__(self, reasons: Sequence[str]) -> None:
        super().__init__("\n".join(reasons))
        self.reasons = tuple(reasons)


class _Bad(Exception):
    """表の 1 か所が読めない理由。"""


class _Line(NamedTuple):
    """コードブロックの外の 1 行。`level` は見出しのレベル（見出しでなければ 0）、`title` は見出しの文。"""

    at: int
    text: str
    level: int
    title: str


class _Scope(NamedTuple):
    """表の 1 行を読むのに要る、実装計画とスプリントの値。"""

    plan: PlanDoc
    tasks: Sequence[int]
    criteria: Mapping[int, int]
    sprint: frozenset[int]


def _lines(text: str) -> list[_Line]:
    out = []
    for at, line in inputs_decide.outside_fences(inputs_decide.body_lines(text)):
        found = _HEADING.match(line)
        out.append(_Line(at, line, len(found.group(1)), found.group(2)) if found else _Line(at, line, 0, ""))
    return out


def _unit_headings(lines: Sequence[_Line]) -> list[int]:
    """単位の見出し（`##` 以下）の、`lines` の中の位置。"""
    return [i for i, line in enumerate(lines) if line.level >= 2 and UNITS_HEADING in line.title]


def _section(lines: Sequence[_Line], at: int) -> Sequence[_Line]:
    """`at` の見出しのセクション（次の同じか上のレベルの見出しの手前まで）。"""
    level = lines[at].level
    end = next((i for i in range(at + 1, len(lines)) if 0 < lines[i].level <= level), len(lines))
    return lines[at + 1 : end]


def _named_section(lines: Sequence[_Line], title: str) -> Sequence[_Line]:
    """見出しの文が `title` の最初のセクション。無ければ空。"""
    at = next((i for i, line in enumerate(lines) if line.level >= 2 and line.title == title), None)
    return () if at is None else _section(lines, at)


def _first_table(section: Sequence[_Line]) -> Optional[list[list[str]]]:
    """セクションの最初の表の (見出しの行, データ行...) のセル。区切りの行を持つ表が無ければ None。"""
    rows: list[_Line] = []
    for line in section:
        piped = line.text.lstrip().startswith("|")
        if rows and (not piped or line.at != rows[-1].at + 1):
            break
        if piped:
            rows.append(line)
    if len(rows) < 2 or not inputs_decide.is_rule(rows[1].text):
        return None
    return [inputs_decide.split_row(line.text) for line in (rows[0], *rows[2:])]


def _cell_elements(cell: str) -> list[str]:
    """括弧の注記を除き、区切りで分けたセルの要素。"""
    left = _NOTE.sub("", cell)
    while left != cell:
        cell, left = left, _NOTE.sub("", left)
    return [part.strip() for part in _SEPARATOR.split(left)]


def _span(found: re.Match[str]) -> range:
    """要素の番号か範囲。始めが終わりより大きければ空。"""
    first = int(found["first"])
    return range(first, int(found["last"] or found["first"]) + 1)


def _tasks_cell(cell: str, known: Sequence[int]) -> tuple[int, ...]:
    out: dict[int, None] = {}
    for i, element in enumerate(_cell_elements(cell)):
        found = _TASK_ITEM.fullmatch(element)
        if not found or (i == 0 and not found["prefix"]) or not _span(found):
            raise _Bad(f"{TASKS_COLUMN}の形が違う: {cell!r}")
        # 大きな範囲を展開しない。見出しに無い最初の番号で止める
        for number in _span(found):
            if number not in known:
                raise _Bad(f"{TASKS_COLUMN}の Task {number} の見出し（### Task {number}）が「{TASKS_HEADING}」に無い")
            out[number] = None
    return tuple(out)


def _criteria_item(element: str, issue: Optional[int], scope: _Scope) -> tuple[int, range]:
    """条件のセルの要素 1 つの (課題, 条件の番号)。`issue` は課題を省いた要素が受け継ぐ課題。"""
    found = _CRITERIA_ITEM.fullmatch(element)
    if not found or not _span(found):
        raise _Bad(f"{CRITERIA_COLUMN}の要素の形が違う: {element!r}")
    if found["issue"]:
        issue = int(found["issue"])
    if issue is None:
        raise _Bad(f"{CRITERIA_COLUMN}の最初の要素に課題が無い（課題が 1 つの実装計画だけ省ける）: {element!r}")
    if issue not in scope.sprint:
        raise _Bad(f"{CRITERIA_COLUMN}の課題 #{issue} はスプリントの課題に無い")
    numbers = _span(found)
    if numbers.start < 1 or numbers.stop - 1 > scope.criteria[issue]:
        raise _Bad(f"{CRITERIA_COLUMN}の {element!r} は #{issue} の受け入れ条件（{scope.criteria[issue]} 件）に無い")
    return issue, numbers


def _criteria_cell(cell: str, scope: _Scope) -> tuple[tuple[int, int], ...]:
    elements = _cell_elements(cell)
    if elements == [NO_CRITERIA]:
        return ()
    issue = min(scope.plan.issues) if len(scope.plan.issues) == 1 else None
    out: dict[tuple[int, int], None] = {}
    for element in elements:
        issue, numbers = _criteria_item(element, issue, scope)
        out.update(dict.fromkeys((issue, n) for n in numbers))
    return tuple(out)


def _order_cell(row: Mapping[str, str]) -> int:
    """「実行順」の値。列が無ければ 0（並びは表の順のまま）。"""
    if ORDER_COLUMN not in row:
        return 0
    value = row[ORDER_COLUMN]
    if not re.fullmatch("[0-9]+", value) or int(value) < 1:
        raise _Bad(f"{ORDER_COLUMN}が 1 以上の整数でない: {value!r}")
    return int(value)


def _row_unit(row: Mapping[str, str], scope: _Scope) -> tuple[int, Unit]:
    name = row[NAME_COLUMN]
    unit = Unit(
        name,
        min(scope.plan.issues),
        scope.plan.path,
        _tasks_cell(row[TASKS_COLUMN], scope.tasks),
        _criteria_cell(row[CRITERIA_COLUMN], scope),
        False,
    )
    return _order_cell(row), unit


def _unit_rows(lines: Sequence[_Line]) -> list[dict[str, str]]:
    """単位の表のデータ行を {列の名前: セル} にしたもの。足りないセルは空。"""
    headings = _unit_headings(lines)
    if len(headings) > 1:
        raise _Bad(f"「{UNITS_HEADING}」の見出しが 2 つ以上ある")
    table = _first_table(_section(lines, headings[0]))
    if table is None:
        raise _Bad(f"「{UNITS_HEADING}」のセクションに表が無い")
    head, *rows = table
    lack = [column for column in REQUIRED_COLUMNS if column not in head]
    if lack:
        raise _Bad(f"単位の表に列が無い: {'、'.join(lack)}")
    if not rows:
        raise _Bad("単位の表に行が無い")
    return [dict(zip(head, [*cells, *[""] * len(head)])) for cells in rows]


def _assignment_lacks(tasks: Sequence[int], found: Sequence[Unit]) -> list[str]:
    """どの単位にも無いタスクと、2 つ以上の単位にあるタスク。"""
    out = []
    for number in tasks:
        owners = [unit.name for unit in found if number in unit.tasks]
        if not owners:
            out.append(f"Task {number} がどの単位にも無い")
        elif len(owners) > 1:
            out.append(f"Task {number} が 2 つ以上の単位にある: {'、'.join(owners)}")
    return out


def _pre_refactoring(lines: Sequence[_Line]) -> bool:
    """「リファクタリングの時機」の表で、実装の前の行の対象が「しない」で始まらないか。表か行が無ければ偽。"""
    table = _first_table(_named_section(lines, TIMING_HEADING))
    rows = [] if table is None else table[1:]
    return any(len(row) > 1 and row[0].startswith(BEFORE_IMPL) and not row[1].startswith(NO_TARGET) for row in rows)


def _ordered(lines: Sequence[_Line], tasks: Sequence[int], rows: Sequence[tuple[int, Unit]], by_column: bool) -> list[Unit]:
    """実装計画の中の流す順。"""
    if by_column:
        return [unit for _, unit in sorted(rows, key=lambda row: row[0])]
    found = [unit for _, unit in rows]
    if tasks and _pre_refactoring(lines):
        found.sort(key=lambda unit: tasks[0] not in unit.tasks)
    return found


def _plan_units(plan: PlanDoc, criteria: Mapping[int, int], sprint: frozenset[int]) -> tuple[list[Unit], list[str]]:
    """実装計画 1 つの (流す順の単位, 読めない理由)。"""
    lines = _lines(plan.text)
    titles = (_TASK_TITLE.match(line.title) for line in _named_section(lines, TASKS_HEADING) if line.level == 3)
    tasks = list(dict.fromkeys(int(m.group(1)) for m in titles if m))
    scope = _Scope(plan, tasks, criteria, sprint)
    try:
        table = _unit_rows(lines)
    except _Bad as exc:
        return [], [str(exc)]
    rows: list[tuple[int, Unit]] = []
    reasons: list[str] = []
    names: set[str] = set()
    for number, row in enumerate(table, start=1):
        name = row[NAME_COLUMN]
        if not name or name in names:
            reasons.append(f"{number} 行目の単位の名前が空" if not name else f"単位の名前が重なる: {name}")
        names.add(name)
        try:
            rows.append(_row_unit(row, scope))
        except _Bad as exc:
            reasons.append(f"{number} 行目の{exc}")
    # 行が欠けた状態で割り当てを数えると、読めない行のタスクが「どの単位にも無い」と重ねて出る
    if len(rows) == len(table):
        reasons += _assignment_lacks(tasks, [unit for _, unit in rows])
    return _ordered(lines, tasks, rows, ORDER_COLUMN in table[0]), reasons


def has_units(text: str) -> bool:
    """コードブロックの外に単位の見出しがあるか。"""
    return bool(_unit_headings(_lines(text)))


def lineup(plans: Sequence[PlanDoc], criteria: Mapping[int, int], issues: Sequence[int]) -> tuple[Unit, ...]:
    """全実装計画の表を読み、表の無い課題を 1 単位として補い、流す順に並べる。

    `plans` はスプリントの課題の実装計画、`criteria` はスプリントの課題ごとの受け入れ条件の数。読めない所があれば、
    すべての実装計画を読み終えてから理由をまとめて `UnitsUnreadable` にする。
    """
    sprint = frozenset(issues)
    with_units = sorted((p for p in plans if has_units(p.text)), key=lambda p: (min(p.issues), p.path))
    covered = frozenset(number for plan in with_units for number in plan.issues)
    groups = [(number, [Unit(f"issue-{number}", number, "", (), (), True)]) for number in sprint - covered]
    reasons: list[str] = []
    for plan in with_units:
        found, lacks = _plan_units(plan, criteria, sprint)
        groups.append((min(plan.issues), found))
        reasons += [f"{plan.path}: {lack}" for lack in lacks]
    ordered = tuple(unit for _, group in sorted(groups, key=lambda g: g[0]) for unit in group)
    seen: dict[tuple[int, str], str] = {}
    for unit in ordered:
        first = seen.setdefault((unit.issue, unit.name), unit.plan)
        if first != unit.plan:
            reasons.append(f"{unit.plan}: 単位 {unit.name} の代表の課題と名前が {first} の単位と重なる（ブランチ名が重なる）")
    if reasons:
        raise UnitsUnreadable(reasons)
    return ordered


def acceptance_args(unit: Unit) -> list[str]:
    """単位の `ndf loop acceptance check` の引数の並び（#4369 の決定 9）。"""
    if unit.supplemented:
        return ["--issue", str(unit.issue)]
    if not unit.criteria:
        return ["--issue", str(unit.issue), "--no-criteria"]
    args = [arg for number in sorted({issue for issue, _ in unit.criteria}) for arg in ("--issue", str(number))]
    return [*args, *[arg for issue, number in unit.criteria for arg in ("--criteria", f"{issue}:{number}")]]
