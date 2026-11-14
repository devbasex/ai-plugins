"""判定の反映（plan の `actions`）・起票（plan の `create`）・マイルストーンの移動（plan の `reschedule`）。決まりは `actions_decide` が持つ。

済んだ反映と起票は記録（ledger.json）へ 1 件ずつ書き、打ち直しで 2 度書かない。1 件の失敗で残りを止めない。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Optional

from ndf.adapters.github import GitHub, GitHubError
from ndf.domains.backlog import issues_gh, model, state
from ndf.domains.backlog import actions_decide as ad


class Ledger:
    """記録（鍵 → 済んだ結果）。書くたびにファイルへ置き換えて残す。"""

    def __init__(self, sd: Path) -> None:
        self.sd = sd
        got = state.read(sd, "backlog-ledger", {})
        if not isinstance(got, dict):
            raise state.StateError(f"記録の形が違う（オブジェクトでない）: {sd}/ledger", "invalid")
        self.rows: dict[str, Any] = got

    def get(self, key: str) -> Optional[dict[str, Any]]:
        row = self.rows[key] if key in self.rows else None
        return row if isinstance(row, dict) else None

    def put(self, key: str, row: dict[str, Any]) -> None:
        self.rows[key] = row
        state.write(self.sd, "backlog-ledger", self.rows)


def each_number(
    items: Iterable[dict[str, Any]], write: Callable[[dict[str, Any]], Optional[str]]
) -> tuple[list[int], list[dict[str, Any]]]:
    """1 件ずつ書き、(書けた番号, 失敗の {number, error}) を返す。`write` は書けない理由か None を返す。1 件の失敗で残りを止めない。"""
    done: list[int] = []
    failed: list[dict[str, Any]] = []
    for e in items:
        try:
            why = write(e)
        except GitHubError as exc:
            why = str(exc)
        if why is not None:
            failed.append({"number": e["number"], "error": why})
            continue
        done.append(e["number"])
    return done, failed


def reschedule(gh: GitHub, plan: dict[str, Any], holds: set[int]) -> tuple[list[int], list[dict[str, Any]]]:
    """plan の `reschedule` のうち保留でない課題のマイルストーンを移す。(移した番号, 失敗の {number, error})。"""
    moves = [e for e in plan["reschedule"] if e["number"] not in holds] if "reschedule" in plan else []
    if not moves:
        return [], []
    titles = issues_gh.milestones(gh)

    def write(e: dict[str, Any]) -> Optional[str]:
        if e["milestone"] not in titles:
            return f"マイルストーン {e['milestone']!r} が無い"
        issues_gh.set_milestone(gh, e["number"], titles[e["milestone"]])
        return None

    return each_number(moves, write)


def _write(gh: GitHub, n: int, d: ad.Diff, done: ad.Diff) -> None:
    """書き込む。書けた分を `done` に足していき、途中で失敗したら書けた分までを残して `GitHubError` を投げる。"""
    if d.patch:
        issues_gh.patch_issue(gh, n, d.patch)
        done.patch = d.patch
    if d.add:
        issues_gh.add_labels(gh, n, d.add)
        done.add = d.add
    for lb in d.remove:
        issues_gh.remove_label(gh, n, lb)
        done.remove.append(lb)


def _one(gh: GitHub, act: dict[str, Any], key: str, ledger: Ledger) -> tuple[str, str]:
    """1 件を反映し (区分, 理由) を返す。"""
    n = act["number"]
    rec = ledger.get(key)
    early = ad.gate(act, rec)
    if early is not None:
        return early
    cur = issues_gh.issue(gh, n)
    if ad.changed_since_plan(act, cur, rec):
        return ad.SKIPPED, f"plan の後に書き換わった（updated_at {act['updated_at']} → {cur['updated_at']}）"
    d = ad.diff(cur, ad.changes_of(act))
    if d.empty():
        ledger.put(key, {"result": ad.UNCHANGED})
        return ad.UNCHANGED, "変える内容が無い"
    done = ad.Diff()
    try:
        _write(gh, n, d, done)
    except GitHubError:
        if not done.empty():
            # 書けた分までの要約値を残し、打ち直しで自分の書き込みを並行の更新と取り違えない
            ledger.put(key, {"result": ad.PARTIAL, "digest": model.snapshot_digest(ad.after_write(cur, done))})
        raise
    ledger.put(key, {"result": ad.APPLIED, "fields": sorted(d.patch), "add_labels": d.add, "remove_labels": d.remove,
                     "digest": model.snapshot_digest(ad.after_write(cur, d))})  # fmt: skip
    return ad.APPLIED, ", ".join([*sorted(d.patch), *(f"+{x}" for x in d.add), *(f"-{x}" for x in d.remove)])


def apply_actions(gh: GitHub, ledger: Ledger, plan: dict[str, Any]) -> dict[str, Any]:
    """plan の `actions` を反映する。区分ごとの番号と、課題ごとの行を返す。"""
    buckets: dict[str, list[int]] = {b: [] for b in ad.BUCKETS}
    items = []
    closed: list[int] = []
    failed: list[dict[str, Any]] = []
    for act in plan["actions"] if "actions" in plan else []:
        try:
            bucket, reason = _one(gh, act, ad.ledger_key(gh.repo, act), ledger)
        except GitHubError as exc:
            bucket, reason = ad.FAILED, str(exc)
        buckets[bucket].append(act["number"])
        items.append({"kind": "issue", "name": f"#{act['number']}", "result": bucket, "verdict": act["verdict"], "reason": reason})
        if bucket == ad.FAILED:
            failed.append({"number": act["number"], "error": reason})
        elif bucket == ad.APPLIED and ad.changes_of(act).get("state") == "closed":
            closed.append(act["number"])
    return {"buckets": buckets, "items": items, "closed": closed, "failed": failed}


def create(gh: GitHub, ledger: Ledger, plan: dict[str, Any]) -> dict[str, Any]:
    """plan の `create` を起票する。{created, rejected, failed}。"""
    out: dict[str, list[dict[str, Any]]] = {"created": [], "rejected": [], "failed": []}
    for c in plan["create"] if "create" in plan else []:
        why = ad.create_reason(c)
        if why is not None:
            out["rejected"].append({"title": c.get("title"), "reason": why})
            continue
        key = ad.create_key(c)
        done = ledger.get(key)
        if done is not None:
            out["created"].append({**done, "already": True})
            continue
        labels = ad.create_labels(c)
        try:
            n = issues_gh.create_issue(gh, c["title"], ad.compose_body(c), labels)
        except GitHubError as exc:
            out["failed"].append({"title": c["title"], "reason": str(exc)})
            continue
        row = {"number": n, "title": c["title"], "kind": c["kind"], "labels": labels}
        ledger.put(key, row)
        out["created"].append(row)
    return out
