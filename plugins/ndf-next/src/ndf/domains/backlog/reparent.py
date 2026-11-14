"""付け直し（Issue の親を替える）と戻し。

付け直す前に全件の今の親を読み、前の状態（before-<id>.json）を書いてから付け直す。親の無い課題は前の状態に
`null` で残り、戻すと親から外す（#2701）。前の状態を書けなければ 1 件も付け直さない。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ndf.adapters.github import GitHub
from ndf.domains.backlog import actions, issues_gh, state


class ReparentError(Exception):
    """plan の付け直しの形が違う・前の状態が無い。"""


def entries_of(plan: dict[str, Any]) -> list[tuple[int, Optional[int]]]:
    """plan の `reparent` を (番号, 新しい親か None) の列にする。"""
    got = plan["reparent"] if "reparent" in plan else []
    if not isinstance(got, list):
        raise ReparentError("reparent は [{number, parent}] の列で書く")
    out = []
    for e in got:
        if not isinstance(e, dict) or not isinstance(e.get("number"), int) or not isinstance(e.get("parent"), (int, type(None))):
            raise ReparentError(f"reparent の要素は number（整数）と parent（整数か null）を持つ: {e!r}")
        out.append((e["number"], e["parent"]))
    return out


def _move_all(gh: GitHub, moves: list[dict[str, Any]], key_to: str, key_from: str) -> tuple[list[int], list[dict[str, Any]]]:
    """`key_from` の親から `key_to` の親へ付け直す。失敗は貯めて返す（1 件の失敗で残りを止めない）。"""
    return actions.each_number(moves, lambda m: issues_gh.set_parent(gh, m["number"], m[key_to], m[key_from]))


def apply(gh: GitHub, sd: Path, plan: dict[str, Any], run_id: str) -> dict[str, Any]:
    """plan の付け直しを反映する。前の状態のパスと、付け直した・失敗した課題を返す。"""
    entries = entries_of(plan)
    if not entries:
        return {"applied": [], "failed": [], "before": None}
    moves = [{"number": n, "before": issues_gh.parent_of(gh, n), "after": parent} for n, parent in entries]
    path = state.write(sd, "backlog-before", {"repo": gh.repo, "run": run_id, "moves": moves}, id=run_id)
    done, failed = _move_all(gh, moves, "after", "before")
    return {"applied": done, "failed": failed, "before": str(path)}


def revert(gh: GitHub, sd: Path, run_id: str) -> dict[str, Any]:
    """前の状態へ戻す。今の親を読み直し、前の親（無ければ親なし）へ付け直す。"""
    saved = state.read(sd, "backlog-before", None, id=run_id)
    if not isinstance(saved, dict) or not isinstance(saved.get("moves"), list):
        raise ReparentError(f"付け直しの前の状態が無い: 実行 {run_id}")
    if saved.get("repo") != gh.repo:
        raise ReparentError(f"前の状態は {saved.get('repo')} のもので、{gh.repo} へは戻さない")
    moves = [{"number": m["number"], "before": m["before"], "now": issues_gh.parent_of(gh, m["number"])} for m in saved["moves"]]
    done, failed = _move_all(gh, moves, "before", "now")
    return {"reverted": done, "failed": failed}
