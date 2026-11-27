"""単位のファイル・実装計画・上限の読み手と、実装計画の表の書き手（issues/issue-3702-design-detail.md の「単位のファイルの読み書き」、設計の決定 3・7・11）。

単位のファイルは `issues/issue-<番号の並び>-plan.units.json` で、組の実装計画は同じ番号の並びの `-plan.md` である。読み元は
worktree かコミット（`Tree`）で、判定は `units_decide.judge` に渡す。ファイルは書き換えない（表は全文を返し、書くのは呼び手）。
読めないもの・形の違うものは `UnitsFileError`（停止理由の種類は `invalid`）にする。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import contracts
from ndf.adapters.git import END_OF_OPTIONS, Git
from ndf.contracts import limits
from ndf.core.plan import inputs_decide, review_units_decide
from ndf.core.plan import units_decide as ud

SCHEMA = "workflow/review-units.schema.json"
LIMITS_SCHEMA = "schemas/ndf-settings/project.schema.json#/$defs/ReviewUnits"
LIMITS_KEY = "workflow.review_units"
# 単位のファイルと設計書の名前の形（名前の番号の並びが課題。詳細設計書は設計書に数えない）
_UNITS_PATH = re.compile(r"^issues/issue-(\d+(?:-\d+)*)-plan\.units\.json$")
_DESIGN_PATH = re.compile(r"^issues/issue-(\d+(?:-\d+)*)-design\.md$")
UNITS_SUFFIX = "-plan.units.json"
PLAN_SUFFIX = "-plan.md"
# 独立した単位を持たない扱い（設計書の「設計の結果」の表の値）
EXCLUDED_HANDLINGS = frozenset({inputs_decide.MERGE_HANDLING, "閉じる"})

_SYMLINK_MODE = "120000"
_PARALLEL_TITLE = "並列と直列"
_UNITS_HEAD = (
    review_units_decide.NAME_COLUMN,
    review_units_decide.TASKS_COLUMN,
    review_units_decide.CRITERIA_COLUMN,
    "見込みの差分の行数",
    review_units_decide.ORDER_COLUMN,
    "先に要る単位",
    "マージの順",
    "実際の行数",
)
_PARALLEL_HEAD = (
    review_units_decide.ORDER_COLUMN,
    "この実行順の単位",
    "並列に流れる単位（依存が無く、触るファイルが重ならない）",
    "直列に残す依存と理由",
)
_NONE = review_units_decide.NO_CRITERIA
_JOIN = "、"


class UnitsFileError(contracts.ContractError):
    """単位のファイル・上限の設定を読めないか、形が違う。呼び手は `invalid` で止める。"""


class Tree(NamedTuple):
    """読み元。`commit` が無ければ worktree（`root` はリポジトリの根）、あればそのコミットの中身を読む。パスは根からの相対。"""

    root: Path
    commit: Optional[str] = None

    def names(self) -> list[str]:
        """`issues/` の直下の項目のパス（ファイルのほか、ディレクトリとリンクも含む）。"""
        if self.commit is not None:
            return sorted(self._entries("issues/"))
        found = self.root / "issues"
        return sorted(f"issues/{p.name}" for p in found.iterdir()) if found.is_dir() else []

    def read(self, path: str) -> Optional[str]:
        """ファイルの本文。無ければ None。ディレクトリ・シンボリックリンク・UTF-8 でない中身は `UnitsFileError`。"""
        data = self._committed(path) if self.commit is not None else self._working(path)
        if data is None:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnitsFileError(f"{path}: UTF-8 で読めない（{exc.start} バイト目）") from None

    def _working(self, path: str) -> Optional[bytes]:
        full = self.root / path
        # 途中のディレクトリのリンクと `..` も、解決した先が書いたとおりのパスと違うことで拾う
        if full.is_symlink() or (full.exists() and full.resolve() != self.root.resolve() / path):
            raise UnitsFileError(f"{path}: シンボリックリンクか、根の中を直に指さないパスは読まない")
        if full.is_dir():
            raise UnitsFileError(f"{path}: ディレクトリは読まない")
        return full.read_bytes() if full.is_file() else None

    def _committed(self, path: str) -> Optional[bytes]:
        entries = self._entries(path)
        if path not in entries:
            return None
        mode, kind = entries[path]
        if mode == _SYMLINK_MODE:
            raise UnitsFileError(f"{path}: シンボリックリンクは読まない")
        if kind != "blob":
            raise UnitsFileError(f"{path}: ディレクトリは読まない")
        return Git(self.root).blobs(f"{self.commit}:{path}")[0]

    def _entries(self, spec: str) -> dict[str, tuple[str, str]]:
        """コミットの `git ls-tree` の {パス: (モード, 種類)}。"""
        out = Git(self.root).run_bytes("ls-tree", "-z", END_OF_OPTIONS, str(self.commit), "--", spec)
        found: dict[str, tuple[str, str]] = {}
        for record in out.decode("utf-8", "replace").split("\0"):
            if record:
                meta, _, path = record.partition("\t")
                mode, kind, _ = meta.split(" ", 2)
                found[path] = (mode, kind)
        return found


class Collected(NamedTuple):
    """スプリントの課題の振り分け。`omitted` は実装計画を省いた課題、`excluded` は扱いが「マージする」「閉じる」の課題、
    `missing` はどちらでもないのに単位のファイルが無い課題。"""

    files: tuple[ud.UnitFile, ...]
    omitted: tuple[int, ...]
    excluded: tuple[int, ...]
    missing: tuple[int, ...]


class TaskGap(NamedTuple):
    """実装計画と単位のファイルのタスクの食い違い。`duplicated` は実装計画で 2 回以上現れる番号。"""

    plan: str
    duplicated: tuple[str, ...]
    only_plan: tuple[str, ...]
    only_units: tuple[str, ...]


class Report(NamedTuple):
    """確かめの結果。`task_gaps`・`stale_tables`（表を書き直すと変わる実装計画）は `docs` を指定したときだけ入る。"""

    judgement: ud.Judgement
    missing: tuple[int, ...]
    task_gaps: tuple[TaskGap, ...]
    stale_tables: tuple[str, ...]


# --- 読み手 -------------------------------------------------------------------------------------


def _numbers_in(pattern: re.Pattern[str], path: str) -> frozenset[int]:
    found = pattern.match(path)
    return frozenset(int(n) for n in found.group(1).split("-")) if found else frozenset()


def find(tree: Tree, issues: Sequence[int]) -> dict[int, str]:
    """課題の番号 → ファイル名の番号の並びにその課題を持つ単位のファイル。無い課題は入らない。2 つあれば `UnitsFileError`。"""
    wanted = set(issues)
    out: dict[int, str] = {}
    for path in tree.names():
        for issue in sorted(_numbers_in(_UNITS_PATH, path) & wanted):
            if issue in out:
                raise UnitsFileError(f"#{issue} の単位のファイルが 2 つある: {out[issue]}、{path}")
            out[issue] = path
    return out


def load(tree: Tree, path: str) -> ud.UnitFile:
    """スキーマとパスを検査して読む。名前が単位のファイルの形なら、名前の番号と中の `issues` の一致も確かめる。"""
    text = tree.read(path)
    if text is None:
        raise UnitsFileError(f"{path}: 単位のファイルが無い")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UnitsFileError(f"{path}: {exc.lineno} 行目が JSON として読めない（{exc.msg}）") from None
    errs = contracts.schema_errors(doc, SCHEMA)
    if errs:
        raise UnitsFileError(f"{path}: 形が違う: {'; '.join(_with_task_id(e, doc) for e in errs)}")
    named = _numbers_in(_UNITS_PATH, path)
    if named and named != frozenset(doc["issues"]):
        raise UnitsFileError(f"{path}: issues がファイル名の番号と違う: {doc['issues']}")
    return ud.parse(doc, path)


def _with_task_id(error: str, doc: Any) -> str:
    """`tasks/<添字>` を指すスキーマの誤りに、タスクの `id` を添える。"""
    found = re.match(r"tasks/(\d+)\b", error)
    if not found:
        return error
    task = doc["tasks"][int(found.group(1))]
    return f"タスク {task['id']}（{error}）" if isinstance(task, dict) and "id" in task else error


def limits_of(project: Mapping[str, Any]) -> ud.Limits:
    """`.ndf/project.json` の `workflow.review_units` を、上限の設定の既定より先に当てる。目安が上限以上なら誤り。"""
    workflow = project["workflow"] if "workflow" in project else None
    declared = workflow["review_units"] if isinstance(workflow, Mapping) and "review_units" in workflow else {}
    errs = contracts.schema_errors(declared, LIMITS_SCHEMA)
    if errs:
        raise UnitsFileError(f"{LIMITS_KEY} の形が違う: {'; '.join(errs)}")
    defaults = ud.Limits(int(limits.value("review-units.target-lines", "count")), int(limits.value("review-units.max-lines", "count")))
    # 設定のキー `<欄>_lines` は、`Limits` の欄の名前に `_lines` を足したもの
    got = defaults._replace(**{key.removesuffix("_lines"): int(value) for key, value in declared.items()})
    if got.target >= got.max:
        raise UnitsFileError(f"{LIMITS_KEY} の target_lines（{got.target}）は max_lines（{got.max}）より小さくする")
    return got


def collect(tree: Tree, issues: Sequence[int], omitted: Sequence[int]) -> Collected:
    """課題ごとの扱いを当て、読むファイルと欠落を返す。扱いは省略の指定より先に当てる。"""
    wanted = tuple(sorted(set(issues)))
    excluded = tuple(n for n in wanted if n in _excluded_by_design(tree, wanted))
    skipped = tuple(n for n in wanted if n in set(omitted) and n not in excluded)
    rest = [n for n in wanted if n not in excluded and n not in skipped]
    found = find(tree, rest)
    files = tuple(load(tree, path) for path in dict.fromkeys(found[n] for n in rest if n in found))
    return Collected(files, skipped, excluded, tuple(n for n in rest if n not in found))


def _excluded_by_design(tree: Tree, issues: Sequence[int]) -> set[int]:
    """設計書の「設計の結果」の扱いが「マージする」か「閉じる」の課題。設計書が無いか、扱いが割れる課題は含めない。"""
    handlings: dict[int, set[str]] = {}
    for path in tree.names():
        named = _numbers_in(_DESIGN_PATH, path) & set(issues)
        text = tree.read(path) if named else None
        if text is None:
            continue
        table = inputs_decide.design_handlings(text)
        for issue in named & table.keys():
            handlings.setdefault(issue, set()).add(table[issue][0])
    return {issue for issue, found in handlings.items() if found <= EXCLUDED_HANDLINGS}


def check(tree: Tree, issues: Sequence[int], omitted: Sequence[int], limits: ud.Limits, criteria: Mapping[int, int], docs: bool) -> Report:
    """`judge` の結果に、単位のファイルが無い課題を足す。`docs` なら実装計画のタスクと 2 つの表も突き合わせる。

    実装計画を省いた課題は、課題全体の 1 単位（`units_decide.single`）として判定に加える。
    """
    found = collect(tree, issues, omitted)
    judgement = ud.judge([*found.files, *map(ud.single, found.omitted)], limits, criteria)
    gaps: list[TaskGap] = []
    stale: list[str] = []
    for unit_file in found.files if docs else ():
        plan = unit_file.path.removesuffix(UNITS_SUFFIX) + PLAN_SUFFIX
        text = tree.read(plan)
        # 実装計画が無ければ、単位のファイルのタスクがすべて片側だけになる
        gap = _task_gap(plan, "" if text is None else text, unit_file)
        if gap is not None:
            gaps.append(gap)
        if text is not None and render_tables(text, judgement.views, unit_file.path) != _newlines(text):
            stale.append(plan)
    return Report(judgement, found.missing, tuple(gaps), tuple(stale))


def _task_gap(plan: str, text: str, unit_file: ud.UnitFile) -> Optional[TaskGap]:
    """「タスク分解」の `### Task N` の番号と、単位のファイルのタスクの `id` の食い違い。無ければ None。"""
    numbers = review_units_decide.task_numbers(text)
    counts = Counter(numbers)
    ours = {int(t.id[1:]) for t in unit_file.tasks}
    duplicated, only_plan, only_units = (
        tuple(f"T{n}" for n in sorted(found))
        for found in ([n for n, count in counts.items() if count > 1], counts.keys() - ours, ours - counts.keys())
    )
    return TaskGap(plan, duplicated, only_plan, only_units) if duplicated or only_plan or only_units else None


def differs(a: ud.UnitFile, b: ud.UnitFile) -> bool:
    """パスと実際の行数を除いて、並びと中身が違うか。"""

    def bare(f: ud.UnitFile) -> ud.UnitFile:
        return f._replace(path="", units=tuple(u._replace(actual_lines=None) for u in f.units))

    return bare(a) != bare(b)


# --- 実装計画の表 -------------------------------------------------------------------------------


def render_tables(plan: str, views: Sequence[ud.View], file: Optional[str] = None) -> str:
    """実装計画の「レビューの単位」と「並列と直列」の表を書き直した全文（改行は LF）。

    `views` は全ファイルの単位で、`file` の単位だけを行にする（省けばすべて）。別のファイルの単位は依存と並列の相手として
    名前に課題の番号を添える。セクションの最初の表だけを置き換え、前後の文とほかの表は残す。表が無ければセクションの終わりに
    足し、セクションが無ければ「タスク分解」の後（「並列と直列」は「レビューの単位」の後）に足す。
    """
    mine = [v for v in views if file is None or v.file == file]
    text = _put_table(
        _newlines(plan),
        review_units_decide.UNITS_HEADING,
        _markdown_table(_UNITS_HEAD, _unit_cells(mine, views)),
        review_units_decide.TASKS_HEADING,
    )
    return _put_table(
        text, _PARALLEL_TITLE, _markdown_table(_PARALLEL_HEAD, _parallel_rows(mine, views)), review_units_decide.UNITS_HEADING
    )


def _newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _markdown_table(head: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = [head, ["---"] * len(head), *rows]
    return [inputs_decide.pipe_row([inputs_decide.cell(c) for c in row]) for row in lines]


def _namer(of: ud.View, views: Sequence[ud.View]):
    """`of` のファイルから見た単位の表記。別のファイルの単位は、そのファイルの最小の課題の番号を添える。"""
    issue = {(v.file, v.name): min(v.issues, default=0) for v in views}

    def name(key: tuple[str, str], note: str = "") -> str:
        notes = ([] if key[0] == of.file else [f"#{issue[key]}"]) + ([note] if note else [])
        return f"{key[1]}（{'。'.join(notes)}）" if notes else key[1]

    return name


def _unit_cells(mine: Sequence[ud.View], views: Sequence[ud.View]) -> list[list[str]]:
    rows = []
    for v in mine:
        name = _namer(v, views)
        criteria = sorted(v.criteria, key=lambda c: tuple(map(int, c.split(":"))))
        rows.append(
            [
                v.name,
                "Task " + "・".join(t.removeprefix("T") for t in v.tasks),
                "・".join(criteria) or _NONE,
                _NONE if v.lines is None else str(v.lines),
                str(v.order),
                _JOIN.join(name((f, n), reason) for f, n, reason in v.after) or _NONE,
                str(v.merge),
                "" if v.actual_lines is None else str(v.actual_lines),
            ]
        )
    return rows


def _closures(views: Sequence[ud.View]) -> dict[tuple[str, str], set[tuple[str, str]]]:
    """単位ごとの、間接を含む先に要る単位。"""
    direct = {(v.file, v.name): [(f, n) for f, n, _ in v.after] for v in views}
    out: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for key in direct:
        seen: set[tuple[str, str]] = set()
        stack = list(direct[key])
        while stack:
            found = stack.pop()
            if found not in seen:
                seen.add(found)
                stack += direct[found]
        out[key] = seen
    return out


def _parallel_rows(mine: Sequence[ud.View], views: Sequence[ud.View]) -> list[list[str]]:
    """実行順ごとの 1 行。並列に流れる単位は、どちらからも依存で届かない単位である。"""
    closure = _closures(views)
    rows = []
    for order in sorted({v.order for v in mine}):
        group = [v for v in mine if v.order == order]
        parallel, serial = [], []
        for v in group:
            name, key = _namer(v, views), (v.file, v.name)
            others = [name(k) for k in ((o.file, o.name) for o in views) if k != key and k not in closure[key] and key not in closure[k]]
            parallel.append(f"{v.name}: {_JOIN.join(others) or _NONE}")
            serial += [f"{name((f, n))} → {v.name}: {reason}" for f, n, reason in v.after]
        rows.append([str(order), _JOIN.join(v.name for v in group), "\n".join(parallel), "\n".join(serial) or _NONE])
    return rows


def _put_table(text: str, title: str, table: list[str], after: str) -> str:
    """`title` のセクションの最初の表を `table` にする。"""
    lines = text.split("\n")
    slot = review_units_decide.table_slot(text, title)
    if slot is None:
        previous = review_units_decide.table_slot(text, after)
        return _add_block(lines, len(lines) if previous is None else previous.end, [f"## {title}", "", *table])
    if slot.table is None:
        return _add_block(lines, slot.end, table)
    lines[slot.table.start : slot.table.stop] = table
    return "\n".join(lines)


def _add_block(lines: list[str], at: int, block: list[str]) -> str:
    """`at` の行の手前へ、前後を 1 行の空行で区切って `block` を足す。末尾は改行 1 つにする。"""
    before, rest = lines[:at], lines[at:]
    while before and not before[-1].strip():
        before.pop()
    return "\n".join([*before, *([""] if before else []), *block, "", *rest]).rstrip("\n") + "\n"
