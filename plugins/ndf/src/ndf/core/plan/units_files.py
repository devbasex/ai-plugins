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
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple, Optional

from ndf import contracts
from ndf.adapters.git import END_OF_OPTIONS, Git
from ndf.contracts import limits
from ndf.core.production_merge import PROJECT_FILE
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
# 承認の記録の保存先の、レビューした HEAD のファイル（`ndf cross-review run` の `--reviewed-out` の出力先）
REVIEWED_HEAD = "reviewed-head"
# 独立した単位を持たない扱い（設計書の「設計の結果」の表の値）
EXCLUDED_HANDLINGS = frozenset({inputs_decide.MERGE_HANDLING, "閉じる"})
# 設計書の無い課題（設計書を省いた課題）の扱い
IMPLEMENT_HANDLING = "実装する"

_SYMLINK_MODE = "120000"
_TASK_ID = re.compile(r"T[1-9][0-9]*")
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

    def listing(self) -> dict[str, Optional[tuple[str, str, str]]]:
        """`issues/` の直下の項目（ファイルのほか、ディレクトリとリンクも含む）。値はコミットなら (モード, 種類, id)、worktree なら None。"""
        if self.commit is not None:
            return dict(self._entries("issues/"))
        found = self.root / "issues"
        return {f"issues/{p.name}": None for p in found.iterdir()} if found.is_dir() else {}

    def names(self) -> list[str]:
        """`listing` のパスの並び。"""
        return sorted(self.listing())

    def read(self, path: str) -> Optional[str]:
        """ファイルの本文。無ければ None。

        リポジトリの根からの相対でないパス・ディレクトリ・シンボリックリンク・UTF-8 でない中身は `UnitsFileError`。
        """
        return self.read_all([path])[path]

    def read_all(
        self, paths: Sequence[str], listing: Optional[Mapping[str, Optional[tuple[str, str, str]]]] = None
    ) -> dict[str, Optional[str]]:
        """`read` をまとめて行う。コミットの読み取りは git の起動が件数に依らない。`listing` を渡すと項目を調べ直さない。"""
        for path in paths:
            # 絶対パスは `root / path` で根が落ちるので、開く前に断る
            if PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
                raise UnitsFileError(f"{path}: リポジトリの根からの相対パスでないものは読まない")
        if self.commit is not None:
            data = self._committed(paths, listing)
        else:
            data = {path: self._working(path) for path in paths}
        return {path: _decode(path, found) for path, found in data.items()}

    def _working(self, path: str) -> Optional[bytes]:
        full = self.root / path
        # 途中のディレクトリのリンクと `..` も、解決した先が書いたとおりのパスと違うことで拾う
        if full.is_symlink() or (full.exists() and full.resolve() != self.root.resolve() / path):
            raise UnitsFileError(f"{path}: シンボリックリンクか、根の中を直に指さないパスは読まない")
        if full.is_dir():
            raise UnitsFileError(f"{path}: ディレクトリは読まない")
        return full.read_bytes() if full.is_file() else None

    def _committed(
        self, paths: Sequence[str], listing: Optional[Mapping[str, Optional[tuple[str, str, str]]]]
    ) -> dict[str, Optional[bytes]]:
        if not paths:
            return {}
        entries = self._entries(*paths) if listing is None else listing
        ids: dict[str, str] = {}
        for path in paths:
            if path not in entries:
                continue
            mode, kind, oid = entries[path]
            if mode == _SYMLINK_MODE:
                raise UnitsFileError(f"{path}: シンボリックリンクは読まない")
            if kind != "blob":
                raise UnitsFileError(f"{path}: ディレクトリは読まない")
            ids[path] = oid
        blobs = dict(zip(ids, Git(self.root).blobs(*ids.values()))) if ids else {}
        return {path: blobs[path] if path in blobs else None for path in paths}

    def _entries(self, *specs: str) -> dict[str, tuple[str, str, str]]:
        """コミットの `git ls-tree` の {パス: (モード, 種類, id)}。"""
        out = Git(self.root).run_bytes("ls-tree", "-z", END_OF_OPTIONS, str(self.commit), "--", *specs)
        found: dict[str, tuple[str, str, str]] = {}
        for record in out.decode("utf-8", "replace").split("\0"):
            if record:
                meta, _, path = record.partition("\t")
                mode, kind, oid = meta.split(" ", 2)
                found[path] = (mode, kind, oid)
        return found


def _decode(path: str, data: Optional[bytes]) -> Optional[str]:
    if data is None:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnitsFileError(f"{path}: UTF-8 で読めない（{exc.start} バイト目）") from None


class Collected(NamedTuple):
    """スプリントの課題の振り分け。`omitted` は実装計画を省いた課題、`excluded` は扱いが「マージする」「閉じる」の課題、
    `missing` はどちらでもないのに単位のファイルが無い課題。`handlings` は課題ごとの（扱い, 統合先）。"""

    files: tuple[ud.UnitFile, ...]
    omitted: tuple[int, ...]
    excluded: tuple[int, ...]
    missing: tuple[int, ...]
    handlings: Mapping[int, tuple[str, Optional[int]]]


class TaskGap(NamedTuple):
    """実装計画と単位のファイルのタスクの食い違い。`duplicated` は実装計画で 2 回以上現れる番号。"""

    plan: str
    duplicated: tuple[str, ...]
    only_plan: tuple[str, ...]
    only_units: tuple[str, ...]


class Report(NamedTuple):
    """確かめの結果。`task_gaps`・`stale_tables`（表を書き直すと変わる実装計画）は `docs` を指定したときだけ入る。

    `files` は判定した単位のファイル（省いた課題の 1 単位を含む）である。`handlings` は課題から確かめたときだけ入る。
    """

    judgement: ud.Judgement
    missing: tuple[int, ...]
    task_gaps: tuple[TaskGap, ...]
    stale_tables: tuple[str, ...]
    files: tuple[ud.UnitFile, ...]
    handlings: Mapping[int, tuple[str, Optional[int]]]


# --- 読み手 -------------------------------------------------------------------------------------


def _numbers_in(pattern: re.Pattern[str], path: str) -> frozenset[int]:
    found = pattern.match(path)
    return frozenset(int(n) for n in found.group(1).split("-")) if found else frozenset()


def find(tree: Tree, issues: Sequence[int], names: Optional[Sequence[str]] = None) -> dict[int, str]:
    """課題の番号 → ファイル名の番号の並びにその課題を持つ単位のファイル。無い課題は入らない。2 つあれば `UnitsFileError`。

    `names` に `tree.names()` を渡すと、一覧を取り直さない。
    """
    wanted = set(issues)
    out: dict[int, str] = {}
    for path in tree.names() if names is None else names:
        for issue in sorted(_numbers_in(_UNITS_PATH, path) & wanted):
            if issue in out:
                raise UnitsFileError(f"#{issue} の単位のファイルが 2 つある: {out[issue]}、{path}")
            out[issue] = path
    return out


def load(tree: Tree, path: str) -> ud.UnitFile:
    """スキーマとパスを検査して読む。名前が単位のファイルの形なら、名前の番号と中の `issues` の一致も確かめる。"""
    return _load(path, tree.read(path))


def _load(path: str, text: Optional[str]) -> ud.UnitFile:
    if text is None:
        raise UnitsFileError(f"{path}: 単位のファイルが無い")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UnitsFileError(f"{path}: {exc.lineno} 行目が JSON として読めない（{exc.msg}）") from None
    # 読んだ中身を誤りの文に写さない（場所と規則の名前だけを出す）
    errs = contracts.broken_places(doc, SCHEMA)
    if errs:
        raise UnitsFileError(f"{path}: 形が違う: {'; '.join(_with_task_id(e, doc) for e in errs)}")
    named = _numbers_in(_UNITS_PATH, path)
    if named and named != frozenset(doc["issues"]):
        raise UnitsFileError(f"{path}: issues がファイル名の番号と違う: {doc['issues']}")
    return ud.parse(doc, path)


def _with_task_id(error: str, doc: Any) -> str:
    """`tasks/<添字>` を指すスキーマの誤りに、タスクの `id` を添える。形の違う `id` は添えない。"""
    found = re.match(r"tasks/(\d+)\b", error)
    if not found:
        return error
    task = doc["tasks"][int(found.group(1))]
    tid = task["id"] if isinstance(task, dict) and "id" in task else None
    return f"タスク {tid}（{error}）" if isinstance(tid, str) and _TASK_ID.fullmatch(tid) else error


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
    return _collect(tree, tree.listing(), issues, omitted)


def _collect(tree: Tree, listing: Mapping[str, Optional[tuple[str, str, str]]], issues: Sequence[int], omitted: Sequence[int]) -> Collected:
    wanted = tuple(sorted(set(issues)))
    handled, excluded = _handlings(tree, listing, wanted)
    skipped = tuple(n for n in wanted if n in set(omitted) and n not in excluded)
    rest = [n for n in wanted if n not in excluded and n not in skipped]
    found = find(tree, rest, sorted(listing))
    paths = list(dict.fromkeys(found[n] for n in rest if n in found))
    texts = tree.read_all(paths, listing)
    files = tuple(_load(path, texts[path]) for path in paths)
    return Collected(files, skipped, excluded, tuple(n for n in rest if n not in found), handled)


def _design_rows(
    tree: Tree, listing: Mapping[str, Optional[tuple[str, str, str]]], issues: Sequence[int]
) -> dict[int, list[tuple[str, Optional[int]]]]:
    """課題ごとの、名前の番号にその課題を持つ設計書の「設計の結果」の（扱い, 統合先）。設計書のパスの順に並ぶ。"""
    named = {path: _numbers_in(_DESIGN_PATH, path) & set(issues) for path in sorted(listing)}
    texts = tree.read_all([path for path, found in named.items() if found], listing)
    rows: dict[int, list[tuple[str, Optional[int]]]] = {}
    for path, text in texts.items():
        if text is None:
            continue
        table = inputs_decide.design_handlings(text)
        for issue in sorted(named[path] & table.keys()):
            rows.setdefault(issue, []).append(table[issue])
    return rows


def _handlings(
    tree: Tree, listing: Mapping[str, Optional[tuple[str, str, str]]], issues: Sequence[int]
) -> tuple[dict[int, tuple[str, Optional[int]]], tuple[int, ...]]:
    """課題ごとの（扱い, 統合先）と、独立した単位を持たない課題。

    扱いが割れる課題は独立した単位を持つ扱いを取り、設計書に行の無い課題は「実装する」である。
    """
    rows = _design_rows(tree, listing, issues)
    out: dict[int, tuple[str, Optional[int]]] = {}
    for issue in issues:
        found = rows[issue] if issue in rows else [(IMPLEMENT_HANDLING, None)]
        out[issue] = next((row for row in found if row[0] not in EXCLUDED_HANDLINGS), found[0])
    return out, tuple(n for n in issues if out[n][0] in EXCLUDED_HANDLINGS)


def project_of(tree: Tree) -> dict[str, Any]:
    """読み元の `.ndf/project.json`。無いか、オブジェクトでなければ空。JSON として読めなければ `UnitsFileError`。"""
    text = tree.read(PROJECT_FILE)
    if text is None:
        return {}
    try:
        found = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UnitsFileError(f"{PROJECT_FILE}: {exc.lineno} 行目が JSON として読めない（{exc.msg}）") from None
    return found if isinstance(found, dict) else {}


def issues_referred(files: Sequence[ud.UnitFile]) -> tuple[int, ...]:
    """ファイルの課題と、タスクの受け入れ条件が指すすべての課題。"""
    referred = {int(ref.partition(":")[0]) for f in files for t in f.tasks for ref in t.criteria}
    return tuple(sorted(referred.union(*(f.issues for f in files))))


def check(tree: Tree, issues: Sequence[int], omitted: Sequence[int], limits: ud.Limits, criteria: Mapping[int, int], docs: bool) -> Report:
    """`judge` の結果に、単位のファイルが無い課題を足す。`docs` なら実装計画のタスクと 2 つの表も突き合わせる。

    実装計画を省いた課題は、課題全体の 1 単位（`units_decide.single`）として判定に加える。
    """
    listing = tree.listing()
    found = _collect(tree, listing, issues, omitted)
    files = [*found.files, *map(ud.single, found.omitted)]
    return _report(tree, listing, files, ud.judge(files, limits, criteria), docs)._replace(missing=found.missing, handlings=found.handlings)


def check_files(tree: Tree, files: Sequence[ud.UnitFile], limits: ud.Limits, criteria: Mapping[int, int], docs: bool) -> Report:
    """直に指したファイルを確かめる。課題から探さないので、欠落は見ない。"""
    return _report(tree, tree.listing(), files, ud.judge(files, limits, criteria), docs)


def _report(
    tree: Tree,
    listing: Mapping[str, Optional[tuple[str, str, str]]],
    files: Sequence[ud.UnitFile],
    judgement: ud.Judgement,
    docs: bool,
) -> Report:
    """判定の結果に、`docs` なら実装計画との突き合わせを足す。`missing` は空で返す。"""
    gaps: list[TaskGap] = []
    stale: list[str] = []
    # 省いた課題の 1 単位はパスが空で、組の実装計画が無い
    plans = {paired_plan(f.path): f for f in (files if docs else ()) if f.path}
    for plan, text in tree.read_all(list(plans), listing).items():
        unit_file = plans[plan]
        # 実装計画が無ければ、単位のファイルのタスクがすべて片側だけになる
        gap = _task_gap(plan, "" if text is None else text, unit_file)
        if gap is not None:
            gaps.append(gap)
        if text is not None and render_tables(text, judgement.views, unit_file.path) != _newlines(text):
            stale.append(plan)
    return Report(judgement, (), tuple(gaps), tuple(stale), tuple(files), {})


def paired_plan(units_path: str) -> str:
    """単位のファイルと組の実装計画のパス。"""
    return units_path.removesuffix(UNITS_SUFFIX) + PLAN_SUFFIX


class Rewrite(NamedTuple):
    """実装計画 1 つの、読んだ本文（`before`）と表を書き直した全文（`after`）。"""

    before: str
    after: str


def rendered(tree: Tree, issues: Sequence[int], omitted: Sequence[int], limits: ud.Limits) -> dict[str, Rewrite]:
    """表を書き直すと中身が変わる実装計画の {パス: `Rewrite`}。実装計画の無いファイルは含めない（書くのは呼び手）。"""
    listing = tree.listing()
    found = _collect(tree, listing, issues, omitted)
    # 表に出る値は受け入れ条件の数に依らないので、数は渡さない（参照の違反は確かめが返す）
    views = ud.judge([*found.files, *map(ud.single, found.omitted)], limits, {}, propose=False).views
    files = {paired_plan(f.path): f.path for f in found.files}
    out: dict[str, Rewrite] = {}
    for plan, text in tree.read_all(list(files), listing).items():
        if text is None:
            continue
        new = render_tables(text, views, files[plan])
        if new != _newlines(text):
            out[plan] = Rewrite(text, new)
    return out


# --- 結果の項目 ---------------------------------------------------------------------------------


def unit_items(report: Report) -> list[dict[str, Any]]:
    """単位ごとの `kind: unit`。省いた課題の単位は `file` が空で `lines` が null である。"""
    issue_of = {(f.path, t.id): t.issue for f in report.files for t in f.tasks}
    items = []
    for v in report.judgement.views:
        item = {key: list(value) if isinstance(value, tuple) else value for key, value in v._asdict().items()}
        del item["actual_lines"]
        item["after"] = [{"file": f, "unit": n, "reason": reason} for f, n, reason in v.after]
        # 省いた課題の単位はパスがどれも空だが、合成のタスクの課題は単位の課題と同じである
        item["tasks"] = [{"id": t, "issue": issue_of[(v.file, t)] if v.file else v.issues[0]} for t in v.tasks]
        items.append({"kind": "unit", **item})
    return items


def findings(report: Report) -> list[ud.Finding]:
    """止める理由の並び。規則の番号のほか、欠落は `missing`、タスクの食い違いは `tasks`、表の食い違いは `tables`。"""
    found = [ud.Finding("missing", "", f"#{issue} の単位のファイルが無い") for issue in report.missing]
    for gap in report.task_gaps:
        sides = (
            ("重なる番号", gap.duplicated),
            ("実装計画だけにある", gap.only_plan),
            ("単位のファイルだけにある", gap.only_units),
        )
        reason = "、".join(f"{label} {'・'.join(ids)}" for label, ids in sides if ids)
        found.append(ud.Finding("tasks", "", f"{gap.plan}: タスクが一致しない（{reason}）"))
    found += [
        ud.Finding("tables", "", f"{plan}: 表が単位のファイルと違う（ndf plan units render で書き直す）") for plan in report.stale_tables
    ]
    return [*report.judgement.violations, *found]


def split_items(report: Report) -> list[dict[str, Any]]:
    """分け方の案ごとの `kind: split`。"""
    return [
        {
            "kind": "split",
            "file": p.file,
            "unit": p.unit,
            "units": [{"name": u.name, "tasks": list(u.tasks), "after": [_edge_doc(e) for e in u.after]} for u in p.units],
            "resplit": list(p.resplit),
            "remaining": [f._asdict() for f in p.remaining],
            "repoint": [{"file": f, "unit": u, "issue": issue} for f, u, issue in p.repoint],
        }
        for p in report.judgement.proposals
    ]


def _edge_doc(edge: ud.Edge) -> dict[str, Any]:
    """単位のファイルの `after` の形。"""
    return {key: value for key, value in edge._asdict().items() if value is not None}


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
    名前に課題の番号を添える。置き換えるのは、列の見出しが生成する表のものを持つ表（下位の見出しより手前）だけで、前後の文と
    ほかの表は残す。その表が無ければ、セクションのほかの表と下位の見出しより手前（どちらも無ければ終わり）に足し、セクションが
    無ければ「タスク分解」の後（「並列と直列」は「レビューの単位」の後）に足す。
    """
    mine = [v for v in views if file is None or v.file == file]
    text = _put_table(
        _newlines(plan),
        review_units_decide.UNITS_HEADING,
        review_units_decide.REQUIRED_COLUMNS,
        _markdown_table(_UNITS_HEAD, _unit_cells(mine, views)),
        review_units_decide.TASKS_HEADING,
    )
    return _put_table(
        text,
        _PARALLEL_TITLE,
        _PARALLEL_HEAD[:2],
        _markdown_table(_PARALLEL_HEAD, _parallel_rows(mine, views)),
        review_units_decide.UNITS_HEADING,
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
    """実行順ごとの 1 行。並列に流れる単位は、どちらからも依存で届かず、触るファイルが重ならない単位である。

    計画を省いた課題の単位（`units_decide.single`。パスが空）は触るファイルが分からず直列に流すので、並列の相手にしない。
    """
    closure = _closures(views)
    rows = []
    for order in sorted({v.order for v in mine}):
        group = [v for v in mine if v.order == order]
        parallel, serial = [], []
        for v in group:
            name, key = _namer(v, views), (v.file, v.name)
            apart = ((o.file, o.name) for o in views if v.file and o.file and not set(o.touches) & set(v.touches))
            others = [name(k) for k in apart if k != key and k not in closure[key] and key not in closure[k]]
            parallel.append(f"{v.name}: {_JOIN.join(others) or _NONE}")
            serial += [f"{name((f, n))} → {v.name}: {reason}" for f, n, reason in v.after]
        rows.append([str(order), _JOIN.join(v.name for v in group), "\n".join(parallel), "\n".join(serial) or _NONE])
    return rows


def _put_table(text: str, title: str, columns: Sequence[str], table: list[str], after: str) -> str:
    """`title` のセクションの、列の見出しに `columns` を持つ表を `table` にする。無ければ足す。"""
    lines = text.split("\n")
    slot = review_units_decide.table_slot(text, title, columns)
    if slot is None:
        previous = review_units_decide.table_slot(text, after, ())
        return _add_block(lines, len(lines) if previous is None else previous.end, [f"## {title}", "", *table])
    if slot.table is None:
        return _add_block(lines, slot.at, table)
    lines[slot.table.start : slot.table.stop] = table
    return "\n".join(lines)


def _add_block(lines: list[str], at: int, block: list[str]) -> str:
    """`at` の行の手前へ、前後を 1 行の空行で区切って `block` を足す。末尾は改行 1 つにする。"""
    before, rest = lines[:at], lines[at:]
    while before and not before[-1].strip():
        before.pop()
    return "\n".join([*before, *([""] if before else []), *block, "", *rest]).rstrip("\n") + "\n"
