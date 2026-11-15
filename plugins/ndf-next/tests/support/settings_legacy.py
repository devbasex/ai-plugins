"""旧いキーを持つ設定を、改名表（old と values の旧い値）から組み立てる（旧いキーの名前をテストに書かない）。"""

from __future__ import annotations

from typing import Any

from ndf.domains.settings import renames


def _row(row_id: str) -> renames.Rename:
    return next(r for r in renames.load() if r.id == row_id)


def last(pointer: str) -> str:
    return renames.tokens(pointer)[-1]


def old_value(row_id: str, new_value: str) -> str:
    """値の対応の右側（新しい値）に当たる旧い値。"""
    return next(k for k, v in _row(row_id).values.items() if v == new_value)


def worktree(flow_value: str = "release_branch") -> dict[str, Any]:
    """`branch_flow` のリリース方式を旧いキーと旧い値で持つ worktree の設定。"""
    row = _row("worktree-release-strategy")
    return {
        "version": 1,
        "base_branch": "develop",
        "production_branch": "main",
        "branch_flow": {"start": "production", last(row.old): old_value(row.id, flow_value), "doc_prs": "sprint"},
    }


def old_validation(checked_at: str = "2026-01-01T00:00:00+00:00", result: str = "pass") -> tuple[str, dict[str, Any]]:
    """MVV のバージョンの、旧いキーの名前と旧い中身（名前を変えない accepted_unknown を含む）。"""
    parent, res, at = _row("mvv-validation"), _row("mvv-validation-result"), _row("mvv-validation-checked-at")
    return last(parent.old), {last(res.old): old_value(res.id, result), last(at.old): checked_at, "accepted_unknown": False}


def mvv(versions: int = 2) -> dict[str, Any]:
    """各版に旧いキーの確かめの記録を持つ MVV の設定。表に無いキー（revise_after）を持つ。"""
    rows = []
    for n in range(1, versions + 1):
        key, value = old_validation(f"2026-01-0{n}T00:00:00+00:00")
        rows.append({"version": n, "sha256": "0" * 64, "approved_at": "2026-01-01T00:00:00+00:00", "approved_by": "a", key: value, "changes": [], "body": "# MVV\n"})
    return {"version": 1, "body": "mvv.md", "settings": {"revise_after": {"overrides": 3}}, "versions": rows}
