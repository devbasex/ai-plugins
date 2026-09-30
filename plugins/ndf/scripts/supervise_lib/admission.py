"""queue へ入れる前の重なりの検査と、資源のタグの決め方（#1142 の決定 29・30）。

副作用を持たない関数だけを置く。宣言の値（共有の一覧・資源の枠）は引数で受け、ファイルを読まない。

    plans = {"a.json": ["lib/"], "b.json": ["lib/x.py"], "c.json": ["docs/"]}
    overlaps(plans, shared)   # [{"a": "a.json", "b": "b.json", "paths": ["lib/", "lib/x.py"]}]
    groups(plans, shared)     # [["a.json", "b.json"], ["c.json"]]（組の中は同時に流さない）
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


def groups(plans: Mapping[str, list[str]], shared: Iterable[Mapping] = ()) -> list[list[str]]:
    """同時に流さないプランの組（入れた順）。どれとも重ならないプランは 1 本だけの組になる。"""
    root = {p: p for p in plans}

    def find(p: str) -> str:
        while root[p] != p:
            root[p] = root[root[p]]
            p = root[p]
        return p

    for o in overlaps(plans, shared):
        root[find(o["b"])] = find(o["a"])
    grouped: dict[str, list[str]] = {}
    for p in plans:
        grouped.setdefault(find(p), []).append(p)
    return list(grouped.values())


def others(plans: Mapping[str, list[str]], grouped: list[list[str]]) -> dict[str, list[str]]:
    """プランごとの、同時に流れる他の組の `触るファイル`（重ねずに入れた順）。同じ組のプランは同時に流れないので入れない。"""
    out: dict[str, list[str]] = {}
    for g in grouped:
        rest = [f for h in grouped if h is not g for p in h for f in plans[p]]
        for p in g:
            out[p] = list(dict.fromkeys(rest))
    return out


def tags(step: Mapping, resources: Mapping[str, Mapping]) -> list[str]:
    """ステップが持つ資源のタグ（名前の順）。型が `types` にあるか、run のステップの `cmd` が `commands` のどれかを含めば持つ。"""
    cmd = str(step.get("cmd") or "") if step.get("type") == "run" else ""
    return sorted(
        tag
        for tag, res in resources.items()
        if step.get("type") in (res.get("types") or []) or any(c and c in cmd for c in (res.get("commands") or []))
    )
