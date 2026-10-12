"""queue へ入れる前の重なりの検査と、資源のタグの決め方（#1142 の決定 29・30）。

副作用を持たない関数だけを置く。宣言の値（共有の一覧・資源の枠）は引数で受け、ファイルを読まない。

    plans = {"a.json": ["lib/"], "b.json": ["lib/x.py"], "c.json": ["docs/"]}
    overlaps(plans, shared)   # [{"a": "a.json", "b": "b.json", "paths": ["lib/", "lib/x.py"]}]
    groups(plans, shared)     # [["a.json", "b.json"], ["c.json"]]（組の中は同時に流さない）
    after = {"c.json": ["a.json"]}                   # プラン → 同じステージの前提のプラン
    needs(plans, groups(plans, shared, after), after)  # [[], ["a.json"]]（組ごとの、待つ前提のプラン）
    tags(step, resources)     # ["graphql"]（ステップが使う資源のタグ）

- 重なりの組: 同じステージの 2 本のプランで、`触るファイル` が包含で重なるか、同じ共有の一覧に当たるもの
- 包含はパスの要素の単位で見る（`a/b` は `a/b/c` を含み、`a/bc` を含まない）
- 重なりは推移的でないため、和集合の森で組をまとめる（A と B、B と C が重なれば A と C も同じ組）
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Iterable, Mapping


def _parts(path: str) -> tuple[str, ...]:
    return PurePosixPath(str(path)).parts


def contains(a: str, b: str) -> bool:
    """`a` が `b` を含む（同じパスか、`a` が `b` の上のディレクトリ）。"""
    pa, pb = _parts(a), _parts(b)
    return pb[: len(pa)] == pa


def touches(a: str, b: str) -> bool:
    """2 つのパスが包含で重なる（どちらかがもう片方を含む）。"""
    return contains(a, b) or contains(b, a)


def shared_hits(files: Iterable[str], shared: Iterable[Mapping]) -> list[str]:
    """`触るファイル` が当たる共有の一覧のパス。`touched_by`（無ければ一覧そのもの）のどれかと包含で重なれば当たる。"""
    files = list(files)
    return [s["path"] for s in shared if any(touches(f, t) for f in files for t in (s.get("touched_by") or [s["path"]]))]


def overlap_of(a_files: Iterable[str], b_files: Iterable[str], shared: Iterable[Mapping] = ()) -> list[str] | None:
    """2 本のプランの重なり `[a の側のパス, b の側のパス]`。同じ共有の一覧に当たるときは両方がその一覧。無ければ None。"""
    a_files, b_files, shared = list(a_files), list(b_files), list(shared)
    for fa in a_files:
        for fb in b_files:
            if touches(fa, fb):
                return [fa, fb]
    hits = set(shared_hits(b_files, shared))
    for path in shared_hits(a_files, shared):
        if path in hits:
            return [path, path]
    return None


def overlaps(plans: Mapping[str, list[str]], shared: Iterable[Mapping] = ()) -> list[dict]:
    """重なりの組の一覧 `[{"a", "b", "paths"}]`（`plans` は プラン → `触るファイル`。入れた順）。"""
    shared, names, found = list(shared), list(plans), []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            paths = overlap_of(plans[a], plans[b], shared)
            if paths:
                found.append({"a": a, "b": b, "paths": paths})
    return found


def _union(plans: Iterable[str], pairs: Iterable[tuple[str, str]]) -> list[list[str]]:
    """和集合の森で `pairs` をつないだ組（`plans` の順。組の中も `plans` の順）。"""
    plans = list(plans)
    root = {p: p for p in plans}

    def find(p: str) -> str:
        while root[p] != p:
            root[p] = root[root[p]]
            p = root[p]
        return p

    for a, b in pairs:
        root[find(b)] = find(a)
    grouped: dict[str, list[str]] = {}
    for p in plans:
        grouped.setdefault(find(p), []).append(p)
    return list(grouped.values())


def _edges(plans: list[str], after: Mapping[str, Iterable[str]]) -> list[tuple[str, str]]:
    """待ちの辺 (後ろのプラン, 前提のプラン)。前提のプランが `plans` で前にあるときだけ張る。"""
    pos = {p: i for i, p in enumerate(plans)}
    return [(p, a) for p in plans for a in after.get(p) or () if a in pos and pos[a] < pos[p]]


def needs(plans: Iterable[str], grouped: list[list[str]], after: Mapping[str, Iterable[str]] | None = None) -> list[list[str]]:
    """組ごとの、待つ前提のプラン（他の組にいるもの。重ねずに）。`plans` は入れた順、`after` は プラン → 同じステージの前提のプラン。"""
    edges = _edges(list(plans), after or {})
    out = []
    for g in grouped:
        mine = set(g)
        out.append(list(dict.fromkeys(a for p, a in edges if p in mine and a not in mine)))
    return out


def groups(
    plans: Mapping[str, list[str]], shared: Iterable[Mapping] = (), after: Mapping[str, Iterable[str]] | None = None
) -> list[list[str]]:
    """同時に流さないプランの組（入れた順）。どれとも重ならないプランは 1 本だけの組になる。

    `after`（プラン → 同じステージの前提のプラン）を渡すと、待ちが循環する組（組 G1 の員が G2 の員を、G2 の員が
    G1 の員を前提にする）をまとめて 1 つの組にする（待ち合って進まないため。入れた順に流す）。"""
    names = list(plans)
    pairs = [(o["a"], o["b"]) for o in overlaps(plans, shared)]
    edges = _edges(names, after or {})
    while True:
        grouped = _union(names, pairs)
        at = {p: i for i, g in enumerate(grouped) for p in g}
        cycle = _cycle({i: {at[a] for p, a in edges if at[p] == i and at[a] != i} for i in range(len(grouped))})
        if not cycle:
            return grouped
        pairs += [(grouped[cycle[0]][0], grouped[i][0]) for i in cycle[1:]]


def _cycle(wait: Mapping[int, set[int]]) -> list[int]:
    """待ちの有向グラフ（組の番号 → 待つ組の番号）の循環 1 つの組の番号。無ければ空。"""
    state: dict[int, int] = {}
    stack: list[int] = []

    def visit(n: int) -> list[int]:
        state[n] = 1
        stack.append(n)
        for m in sorted(wait.get(n, ())):
            if state.get(m) == 1:
                return stack[stack.index(m) :]
            if m not in state:
                found = visit(m)
                if found:
                    return found
        stack.pop()
        state[n] = 2
        return []

    for n in sorted(wait):
        if n not in state:
            found = visit(n)
            if found:
                return found
    return []


def tags(step: Mapping, resources: Mapping[str, Mapping]) -> list[str]:
    """ステップが持つ資源のタグ（名前の順）。型が `types` にあるか、run のステップの `cmd` が `commands` のどれかを含めば持つ。"""
    cmd = str(step.get("cmd") or "") if step.get("type") == "run" else ""
    return sorted(
        tag
        for tag, res in resources.items()
        if step.get("type") in (res.get("types") or []) or any(c and c in cmd for c in (res.get("commands") or []))
    )
