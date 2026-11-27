"""単位の判定（純粋な関数。issues/issue-3702-design-detail.md の「単位の判定」、設計の決定 4・5・6）。

単位のファイル（`contracts/workflow/review-units.schema.json`）から、実行順・マージの順などの導く値と、規則 U1〜U6 の違反・W1 の警告を返す。
ファイル・git・環境変数を読まない。例外は投げず、判定できないことは `Finding` で返す。分け方の案（`split`）は後の単位で足す。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from graphlib import CycleError, TopologicalSorter
from typing import NamedTuple, Optional


class Task(NamedTuple):
    id: str
    title: str
    issue: int
    files: tuple[str, ...]
    lines: Optional[int]
    criteria: tuple[str, ...]
    after: tuple[str, ...]


class Edge(NamedTuple):
    """先に要る単位。`issue` は別のファイルの単位を指すときの、そのファイルに載る課題の番号。"""

    unit: str
    reason: str
    issue: Optional[int] = None


class UnitDef(NamedTuple):
    name: str
    tasks: tuple[str, ...]
    after: tuple[Edge, ...]
    actual_lines: Optional[int] = None


class UnitFile(NamedTuple):
    """単位のファイル 1 つ。`whole` は実装計画を省いた課題を 1 単位にしたもの（`single`）。"""

    path: str
    issues: tuple[int, ...]
    tasks: tuple[Task, ...]
    units: tuple[UnitDef, ...]
    whole: bool = False


class Limits(NamedTuple):
    """見込みの行数の目安（超えると W1）と上限（超えると U6）。"""

    target: int
    max: int


class View(NamedTuple):
    """単位 1 つの導いた値。`after` は解決した `(ファイルのパス, 単位の名前, 理由)` の並び。順の 0 は依存の輪で決まらないこと。"""

    file: str
    name: str
    order: int
    merge: int
    after: tuple[tuple[str, str, str], ...]
    issues: tuple[int, ...]
    tasks: tuple[str, ...]
    criteria: tuple[str, ...]
    lines: Optional[int]
    actual_lines: Optional[int]
    touches: tuple[str, ...]


class Finding(NamedTuple):
    rule: str
    unit: str
    reason: str


class Proposal(NamedTuple):
    unit: str
    units: tuple[UnitDef, ...]
    resplit: tuple[str, ...]
    remaining: tuple[Finding, ...]


class Judgement(NamedTuple):
    views: tuple[View, ...]
    violations: tuple[Finding, ...]
    warnings: tuple[Finding, ...]
    proposals: tuple[Proposal, ...]


_Key = tuple[int, str]  # (ファイルの位置, 単位の名前)


def parse(doc: Mapping, path: str) -> UnitFile:
    """スキーマを通った辞書を型にする。`issue` を省いたタスクはファイルの唯一の課題になる。"""
    issues = tuple(doc["issues"])
    tasks = tuple(
        Task(
            t["id"],
            t["title"],
            t["issue"] if "issue" in t else issues[0],
            tuple(t["files"]),
            t["lines"],
            tuple(t["criteria"]),
            tuple(t["after"]),
        )
        for t in doc["tasks"]
    )
    units = tuple(
        UnitDef(
            u["name"], tuple(u["tasks"]), tuple(Edge(e["unit"], e["reason"], e.get("issue")) for e in u["after"]), u.get("actual_lines")
        )
        for u in doc["units"]
    )
    return UnitFile(path, issues, tasks, units)


def single(issue: int) -> UnitFile:
    """実装計画を省いた課題全体を 1 つの単位にする。行数は無く、触るファイルは空。"""
    task = Task("T1", f"#{issue}", issue, (), None, (), ())
    return UnitFile("", (issue,), (task,), (UnitDef(f"issue-{issue}", ("T1",), ()),), whole=True)


def judge(files: Sequence[UnitFile], limits: Limits, criteria: Mapping[int, int]) -> Judgement:
    """全ファイルを 1 つのグラフにし、導く値・違反・警告を返す。`criteria` は対象の課題ごとの受け入れ条件の数。

    参照の解決に失敗しても（U1）、残りの規則は評価できる範囲で続ける。
    """
    bad: list[Finding] = []
    owner = _issue_owners(files, bad)
    homes: dict[_Key, UnitDef] = {}
    member: dict[tuple[int, str], list[_Key]] = {}
    for at, f in enumerate(files):
        _name_rules(f, bad)
        _task_rules(f, criteria, bad)
        _task_cycle(f, bad)
        for u in f.units:
            homes.setdefault((at, u.name), u)
            for tid in u.tasks:
                member.setdefault((at, tid), []).append((at, u.name))
    graph, edges = _unit_graph(files, homes, owner, bad)
    closure, order = _closures_and_orders(graph, bad)
    _u3(files, {k: v[0] for k, v in member.items() if len(v) == 1}, closure, bad)
    _u4(files, homes, closure, bad)
    views = _views(files, homes, edges, order)
    warn: list[Finding] = []
    for v in views:
        if v.lines is None:
            continue
        if v.lines > limits.max:
            bad.append(Finding("U6", v.name, f"{v.file}: 見込みの {v.lines} 行が上限の {limits.max} 行を超える"))
        elif v.lines > limits.target:
            warn.append(Finding("W1", v.name, f"{v.file}: 見込みの {v.lines} 行が目安の {limits.target} 行を超える"))
    return Judgement(views, tuple(bad), tuple(warn), ())


def _issue_owners(files: Sequence[UnitFile], bad: list[Finding]) -> dict[int, int]:
    """課題 → それを載せるファイルの位置。2 つのファイルに載る課題は U1。"""
    owner: dict[int, int] = {}
    for at, f in enumerate(files):
        for issue in f.issues:
            if issue in owner and owner[issue] != at:
                bad.append(Finding("U1", "", f"#{issue} が {files[owner[issue]].path} と {f.path} の 2 つに載る"))
            owner.setdefault(issue, at)
    return owner


def _name_rules(f: UnitFile, bad: list[Finding]) -> None:
    """タスクの番号と単位の名前の重なり・単位が指すタスクの欠落（U1）と、理由の無い依存（U5）。"""
    ids = [t.id for t in f.tasks]
    names = [u.name for u in f.units]
    bad += [Finding("U1", "", f"{f.path}: タスク {dup} が重なる") for dup in sorted({x for x in ids if ids.count(x) > 1})]
    bad += [Finding("U1", dup, f"{f.path}: 単位の名前が重なる") for dup in sorted({x for x in names if names.count(x) > 1})]
    bad += [Finding("U1", u.name, f"{f.path}: 単位のタスク {tid} が無い") for u in f.units for tid in u.tasks if tid not in ids]
    bad += [Finding("U5", u.name, f"{f.path}: 先に要る単位 {e.unit} の理由が空") for u in f.units for e in u.after if not e.reason.strip()]


def _task_rules(f: UnitFile, criteria: Mapping[int, int], bad: list[Finding]) -> None:
    """空の計画・タスクの無い単位・タスクの所属・課題・受け入れ条件（U2）。計画を省く課題は `single` で表すので、空は誤り。"""
    if not f.tasks:
        bad.append(Finding("U2", "", f"{f.path}: タスクが無い"))
    if not f.units:
        bad.append(Finding("U2", "", f"{f.path}: 単位が無い"))
    bad += [Finding("U2", u.name, f"{f.path}: 単位にタスクが無い") for u in f.units if not u.tasks]
    for t in f.tasks:
        count = sum(u.tasks.count(t.id) for u in f.units)
        if count != 1:
            bad.append(Finding("U2", "", f"{f.path}: タスク {t.id} が {count} 個の単位にある"))
        if t.issue not in f.issues:
            bad.append(Finding("U2", "", f"{f.path}: タスク {t.id} の #{t.issue} がファイルの課題に無い"))
        for ref in t.criteria:
            issue, _, number = ref.partition(":")
            limit = criteria.get(int(issue))
            if limit is None:
                bad.append(Finding("U2", "", f"{f.path}: タスク {t.id} の受け入れ条件 {ref} は対象外の課題を指す"))
            elif not 1 <= int(number) <= limit:
                bad.append(Finding("U2", "", f"{f.path}: タスク {t.id} の受け入れ条件 {ref} が #{issue} の本文に無い"))


def _task_cycle(f: UnitFile, bad: list[Finding]) -> None:
    """タスクの依存の指す先の欠落と輪（U1）。"""
    known = {t.id for t in f.tasks}
    bad += [Finding("U1", "", f"{f.path}: タスク {t.id} の先に要るタスク {a} が無い") for t in f.tasks for a in t.after if a not in known]
    graph = {t.id: {a for a in t.after if a in known} for t in f.tasks}
    try:
        TopologicalSorter(graph).prepare()
    except CycleError as e:
        bad.append(Finding("U1", "", f"{f.path}: タスクの依存が輪になる（{' → '.join(e.args[1])}）"))


def _unit_graph(files, homes, owner, bad):
    """単位のグラフ（キー → 先に要るキー）と、解決した直接の依存 `(キー, 理由)` を返す。`Edge.issue` は指す先のファイルを探すためだけに使う。"""
    graph: dict[_Key, set[_Key]] = {k: set() for k in homes}
    edges: dict[_Key, list[tuple[_Key, str]]] = {k: [] for k in homes}
    for key, u in homes.items():
        for e in u.after:
            where = key[0] if e.issue is None else owner.get(e.issue)
            target = (-1, e.unit) if where is None else (where, e.unit)
            if target in homes:
                graph[key].add(target)
                edges[key].append((target, e.reason))
            else:
                bad.append(Finding("U1", u.name, f"{files[key[0]].path}: 先に要る単位 {e.unit} が無い"))
    return graph, edges


def _closure(key: _Key, graph: Mapping[_Key, set[_Key]]) -> frozenset[_Key]:
    """間接を含む先に要る単位の集合（輪にかかる単位だけに使う）。"""
    seen: set[_Key] = set()
    stack = list(graph[key])
    while stack:
        k = stack.pop()
        if k not in seen:
            seen.add(k)
            stack.extend(graph[k])
    return frozenset(seen)


def _closures_and_orders(graph, bad) -> tuple[dict[_Key, frozenset[_Key]], dict[_Key, int]]:
    """閉包と実行順を依存の順に求める。閉包は直接の依存と、その求め済みの閉包の和にし、依存の辺を 1 回ずつ見る。

    実行順は「先に要る単位の実行順の最大 + 1」。輪にかかる単位（輪の上か、輪の後ろ）は並びに出てこないので、
    閉包をたどり直して求め、実行順を 0 にする。
    """
    closure: dict[_Key, frozenset[_Key]] = {}
    order: dict[_Key, int] = {}
    sorter = TopologicalSorter(graph)
    try:
        sorter.prepare()
    except CycleError:
        pass  # 輪で止まるまでは get_ready で並べられる。輪の単位は下で拾う
    while sorter.is_active():
        ready = sorter.get_ready()
        for k in ready:
            closure[k] = frozenset(graph[k]).union(*(closure[p] for p in graph[k]))
            order[k] = 1 + max((order[p] for p in graph[k]), default=0)
        sorter.done(*ready)
    tangled = [k for k in graph if k not in closure]
    for k in tangled:
        closure[k] = _closure(k, graph)
        order[k] = 0
    if tangled:
        cycle = sorted(k for k in tangled if k in closure[k])
        bad.append(Finding("U1", cycle[0][1], f"単位の依存が輪になる（{'・'.join(n for _, n in cycle)}）"))
    return closure, order


def _u3(files, unit_of, closure, bad) -> None:
    """別の単位のタスクへの依存を、単位の依存（間接を含む）が覆わない（U3）。所属が 1 つに決まらないタスクは U2 が見る。"""
    deps = [(at, f, t, a) for at, f in enumerate(files) for t in f.tasks for a in t.after]
    for at, f, t, a in deps:
        mine, theirs = unit_of.get((at, t.id)), unit_of.get((at, a))
        if mine and theirs and theirs != mine and theirs not in closure[mine]:
            bad.append(Finding("U3", mine[1], f"{f.path}: タスク {t.id} が先に要る {a}（単位 {theirs[1]}）を単位の依存が覆わない"))


def _u4(files, homes, closure, bad) -> None:
    """依存（間接を含む）で前後が決まらない 2 単位が同じファイルを触る（U4）。

    ファイルから触る単位を引く索引を作り、各単位について、入力順で後ろにあり同じファイルを触る単位だけを重複なく集める。
    索引をたどる量（ファイルごとの触る単位の数の 2 乗の和）が全組の数（単位の数の 2 乗）を超えるときは、
    多くのファイルを共有していて索引が組を何度も数えるので、入力順で後ろの単位すべてと組にする。
    どちらでも、依存で前後の決まる相手は、重なるファイルを求める前に外す。
    """
    keys = list(homes)
    position = {k: i for i, k in enumerate(keys)}
    touches = {k: frozenset(_touches(files[k[0]], homes[k])) for k in keys}
    by_path: dict[str, list[_Key]] = {}
    for k in keys:
        for p in touches[k]:
            by_path.setdefault(p, []).append(k)
    scan_all = sum(len(ks) ** 2 for ks in by_path.values()) > len(keys) ** 2
    for i, a in enumerate(keys):
        if scan_all:
            later = keys[i + 1 :]
        else:
            partners = {b for p in touches[a] for b in by_path[p] if position[b] > i}
            later = sorted(partners, key=position.__getitem__)
        for b in later:
            if a in closure[b] or b in closure[a]:
                continue
            common = touches[a] & touches[b]
            if not common:
                continue
            where = f"{files[a[0]].path} の {a[1]} と {files[b[0]].path} の {b[1]}"
            bad.append(Finding("U4", a[1], f"{where} が前後なしに {'・'.join(sorted(common))} を触る"))


def _unit_tasks(f: UnitFile, u: UnitDef) -> list[Task]:
    by_id = {t.id: t for t in f.tasks}
    return [by_id[tid] for tid in u.tasks if tid in by_id]


def _touches(f: UnitFile, u: UnitDef) -> tuple[str, ...]:
    return tuple(sorted({p for t in _unit_tasks(f, u) for p in t.files}))


def _views(files, homes, edges, order) -> tuple[View, ...]:
    """マージの順は実行順、同じ実行順は (単位の最小の課題の番号, ファイルとファイルの中の並び) の順に 1 から振る。"""

    def issues_of(k: _Key) -> tuple[int, ...]:
        return tuple(sorted({t.issue for t in _unit_tasks(files[k[0]], homes[k])})) or files[k[0]].issues

    position = {k: i for i, k in enumerate(homes)}
    ranked = sorted((k for k in homes if order[k]), key=lambda k: (order[k], min(issues_of(k)), position[k]))
    merge = dict.fromkeys(homes, 0)
    merge.update({k: i for i, k in enumerate(ranked, 1)})
    views = []
    for k, u in homes.items():
        f = files[k[0]]
        tasks = _unit_tasks(f, u)
        sizes = [t.lines for t in tasks if t.lines is not None]
        lines = None if f.whole or len(sizes) < len(tasks) else sum(sizes)
        criteria = tuple(dict.fromkeys(c for t in tasks for c in t.criteria))
        after = tuple((files[t[0]].path, t[1], reason) for t, reason in edges[k])
        views.append(
            View(f.path, u.name, order[k], merge[k], after, issues_of(k), u.tasks, criteria, lines, u.actual_lines, _touches(f, u))
        )
    return tuple(views)
