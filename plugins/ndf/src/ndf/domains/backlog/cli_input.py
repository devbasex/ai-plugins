"""`ndf issues` のサブコマンドが読む入力（JSON・設定・順位の算出の入力）と、サブコマンドを止める例外。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf.adapters.github import GitHub
from ndf.domains.backlog import depth, estimates_decide, issues_gh, model, rank_decide, state, table_decide

# 実害の目安を書かず、既定の帯の端が尺度に無いときの警告
NO_BAND = "尺度が既定と違い harm_guide が無いため、実害の目安の帯を確かめていない"


class Stop(Exception):
    """サブコマンドを止める。`kind` は停止理由の種類。`incomplete` は失敗した行（`failed`）を欠け（gaps）として持つ。"""

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


def ndf_settings() -> dict[str, Any]:
    """設定（.ndf/backlog.json）。"""
    return depth.ndf_settings(depth.ndf_settings_path(Path.cwd()))


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


def _given_number(key: str, given: dict[int, Any]) -> bool:
    """記録のキー（番号の文字列）が今回の入力にある課題か。"""
    return key.isdigit() and int(key) in given


def config(a: argparse.Namespace) -> model.Config:
    try:
        decl = ndf_settings()
        return model.config_from(decl, a.capacity)
    except (depth.DepthError, model.ConfigError) as exc:
        raise Stop("invalid", str(exc)) from exc


def table_rows(gh: GitHub, issues: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """課題ごとに、今のマイルストーンの説明の `### 順位` の表の行（番号 → 段階）。表に行の無い課題は入らない。"""
    titles = {i["milestone"] for i in issues if i["milestone"]}
    if not titles:
        return {}
    rows = {t: table_decide.parse_rows(m["description"]) for t, m in issues_gh.milestone_rows(gh).items() if t in titles}
    return {
        i["number"]: row for i in issues if (row := (rows[i["milestone"]] if i["milestone"] in rows else {}).get(i["number"])) is not None
    }


class Ranked(NamedTuple):
    """順位の算出の入力と、見積の採り方の結果（外した・見直しへ回した・戻した課題）と、記録へ残す見積と警告。"""

    board: rank_decide.Board
    excluded: list[dict[str, Any]]
    left_for_review: list[int]
    restored: list[int]
    kept: dict[str, Any]
    sized: list[int]
    warnings: list[str]


def rank_board(a: argparse.Namespace, sd: Path, issues: list[dict[str, Any]], gh: GitHub, since: Optional[str] = None) -> Ranked:
    """見積を出どころの順に採り、順位の算出の入力を組む。`since` は差分の棚卸しの起点の時刻（見直しへ回す課題を分ける）。"""
    cfg = config(a)
    given = given_rows(a.scores)
    stored = state.read(sd, "backlog-estimates", {})
    previous = previous_rows(state.read(sd, "backlog-rank", None))
    digests = {i["number"]: model.snapshot_digest(i) for i in issues}
    review = digests if a.depth == model.REVIEW else None
    created = {i["number"]: i["created_at"] for i in issues}
    src = estimates_decide.Sources(given, stored, previous, table_rows(gh, issues))
    picked = estimates_decide.estimates(list(digests), src, cfg, review, since, created)
    try:
        ests, sized = estimates_decide.overlay_sizes(picked.estimates, sizes(a.sizes), cfg)
    except model.EstimateError as exc:
        raise Stop("invalid", str(exc)) from exc
    by_num = {i["number"]: i for i in issues}
    order = rank_decide.milestone_order({i["milestone"] for i in issues if i["milestone"]}, a.milestones)
    # 依存の辺も見積と同じく、課題ごとに今回の入力を優先する（置き換えた記録の depends_on を混ぜない）
    raws = [*given.values(), *(v for k, v in stored.items() if isinstance(v, dict) and not _given_number(k, given))]
    board = rank_decide.Board(by_num, ests, cfg, order, sub_issues=rank_decide.sub_issues_of(issues), edges=rank_decide.dependency_edges(raws, issues),
                              rejected=tuple(state.read(sd, "backlog-rejected", [])), approved=frozenset(a.approved),
                              sized=frozenset(sized))  # fmt: skip
    kept = {**stored, **{str(n): {**given[n], "number": n} for n in ests if n in given}}
    warnings = [] if cfg.harm_guide else [NO_BAND]
    return Ranked(board, picked.excluded, picked.left_for_review, picked.restored, kept, sized, warnings)
