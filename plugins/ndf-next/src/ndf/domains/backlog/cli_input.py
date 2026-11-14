"""`ndf issues` の副命令が読む入力（JSON・宣言・順位の算出の入力）と、副命令を止める例外。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from ndf.domains.backlog import depth, estimates_decide, model, rank_decide, state


class Stop(Exception):
    """副命令を止める。`kind` は止まった理由の種類。`incomplete` は失敗した行（`failed`）を欠け（gaps）として持つ。"""

    def __init__(self, kind: str, detail: str, items: Optional[list] = None, failed: Optional[list] = None) -> None:
        super().__init__(detail)
        self.kind = kind
        self.items = items
        self.failed = failed if failed is not None else []

    def gaps(self) -> list[dict[str, str]]:
        return [{"kind": "write", "name": _row_name(f), "reason": str(f["error"]) if "error" in f else "失敗した"} for f in self.failed]


def _row_name(row: dict[str, Any]) -> str:
    for key, fmt in (("number", "#{}"), ("title", "{}"), ("milestone", "{}")):
        if key in row and row[key] is not None:
            return fmt.format(row[key])
    return "?"


def read_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Stop("invalid", f"{path} を JSON として読めない: {exc}") from exc


def declaration() -> dict[str, Any]:
    """宣言（.ndf/backlog.json）。"""
    return depth.declaration(depth.declaration_path(Path.cwd()))


def given_rows(path: Optional[str]) -> dict[int, Any]:
    if not path:
        return {}
    got = read_json(path)
    rows = got["issues"] if isinstance(got, dict) and "issues" in got else None
    if not isinstance(rows, list):
        raise Stop("invalid", f'{path} は {{"issues": [...]}} の形で書く')
    return {r["number"]: r for r in rows if isinstance(r, dict) and isinstance(r.get("number"), int)}


def sizes(path: Optional[str]) -> dict[int, Any]:
    """見積もりの記録から決めた Size（{"issues": [{"number", "size"}]}）。"""
    return {n: r["size"] for n, r in given_rows(path).items() if "size" in r}


def previous_rows(rank: Any) -> dict[int, Any]:
    tables = rank["milestones"] if isinstance(rank, dict) and isinstance(rank.get("milestones"), list) else []
    out: dict[int, Any] = {}
    for t in tables:
        for r in t["rows"]:
            out.setdefault(r["number"], r)
    return out


def config(a: argparse.Namespace) -> model.Config:
    try:
        decl = declaration()
    except depth.DepthError as exc:
        raise Stop("invalid", str(exc)) from exc
    quality = decl["quality"] if isinstance(decl.get("quality"), dict) else None
    if quality is not None:
        quality = {"label": "quality", "ratio": 0.2, "min": 1, **quality}
    return model.Config(capacity=a.capacity, quality=quality)


def rank_board(
    a: argparse.Namespace, sd: Path, issues: list[dict[str, Any]]
) -> tuple[rank_decide.Board, list[dict[str, Any]], dict[str, Any], list[int]]:
    cfg = config(a)
    given = given_rows(a.scores)
    stored = state.read(sd, "backlog-estimates", {})
    previous = previous_rows(state.read(sd, "backlog-rank", None))
    digests = {i["number"]: model.snapshot_digest(i) for i in issues}
    review = digests if a.depth == model.REVIEW else None
    ests, excluded = estimates_decide.estimates(list(digests), given, stored, previous, cfg, review)
    try:
        ests, sized = estimates_decide.overlay_sizes(ests, sizes(a.sizes), cfg)
    except model.EstimateError as exc:
        raise Stop("invalid", str(exc)) from exc
    by_num = {i["number"]: i for i in issues}
    order = rank_decide.milestone_order({i["milestone"] for i in issues if i["milestone"]}, a.milestones)
    raws = [*given.values(), *(v for v in stored.values() if isinstance(v, dict))]
    board = rank_decide.Board(by_num, ests, cfg, order, sub_issues=rank_decide.sub_issues_of(issues), edges=rank_decide.dependency_edges(raws, issues),
                              rejected=tuple(state.read(sd, "backlog-rejected", [])), approved=frozenset(a.approved),
                              sized=frozenset(sized))  # fmt: skip
    kept = {**stored, **{str(n): {**given[n], "number": n} for n in ests if n in given}}
    return board, excluded, kept, sized
