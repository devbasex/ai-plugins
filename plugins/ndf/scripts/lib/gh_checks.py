"""チェックジョブの読み取りと畳み方（#1142 の L0 で gh_parts から分けた。#632）。"""
from __future__ import annotations

import pathlib
import re
from typing import Any, Callable

import gh_call

CHECK_RUNS_PER_PAGE = 100
CHECK_RUNS_MAX_PAGES = 10
FAILED_CONCLUSIONS = ("failure", "timed_out", "action_required", "startup_failure")


def fetch_check_runs(repo: str, sha: str,
                     rest_get: Callable[[str], Any] | None = None,
                     per_page: int = CHECK_RUNS_PER_PAGE,
                     max_pages: int = CHECK_RUNS_MAX_PAGES) -> list[dict[str, Any]] | None:
    """head の commit のチェックジョブを `total_count` に届くまで読む（畳む前の生の一覧）。

    **「照会できなかった」と「すべて成功」を区別する。** 失敗・`total_count` 0 はどちらも `None`。
    `rest_get` は `.body` を持つ応答（失敗は `None`）を返す関数。省くと `rest` を使う。
    """
    if not repo or not sha:
        return None
    get = rest_get or gh_call.rest
    base = f"repos/{repo}/commits/{sha}/check-runs?per_page={per_page}"
    runs: list[dict[str, Any]] = []
    total: int | None = None
    for page in range(1, max_pages + 1):
        resp = get(f"{base}&page={page}")
        if resp is None or not isinstance(resp.body, dict):
            return None
        if total is None:
            try:
                total = int(resp.body.get("total_count") or 0)
            except (TypeError, ValueError):
                return None
            if total <= 0:
                return None
        chunk = resp.body.get("check_runs")
        if not isinstance(chunk, list) or not chunk:
            break
        runs.extend(r for r in chunk if isinstance(r, dict))
        if len(runs) >= total:
            break
    return runs or None


def _run_order(indexed: tuple[int, dict[str, Any]]) -> tuple[str, int, int]:
    i, run = indexed
    try:
        run_id = int(run.get("id") or 0)
    except (TypeError, ValueError):
        run_id = 0
    # ISO 8601 の UTC（`Z` 付き）は文字列の比較で時刻の順になる。
    stamp = max(str(run.get("completed_at") or ""), str(run.get("started_at") or ""))
    return (stamp, run_id, i)


def fold_check_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同名のチェックジョブを、名前ごとの最新の実行 1 件へ畳む（#632）。

    再実行で `failure` → `success` になったチェックは `success` として返す。新しさは
    `completed_at` と `started_at` の新しい方 → 実行の番号（`id` は増える一方）→
    一覧の中の順で決める。
    並びは最初に現れた名前の順を保つ。
    """
    latest: dict[str, tuple[int, dict[str, Any]]] = {}
    order: list[str] = []
    for indexed in enumerate(runs):
        name = str(indexed[1].get("name") or "")
        if name not in latest:
            order.append(name)
            latest[name] = indexed
        elif _run_order(indexed) >= _run_order(latest[name]):
            latest[name] = indexed
    return [latest[n][1] for n in order]


def run_result(run: dict[str, Any]) -> str:
    """1 件の結果を 1 語にする。未完了は `pending`。"""
    if str(run.get("status") or "completed").lower() != "completed":
        return "pending"
    return str(run.get("conclusion") or "").lower() or "unknown"


def check_result(runs: list[dict[str, Any]] | None, name: str) -> str | None:
    """名前の一致したチェックの、最新の実行の結果。照会できない・一致なしは `None`。"""
    if not runs or not name:
        return None
    for run in fold_check_runs(runs):
        if str(run.get("name") or "") == name:
            return run_result(run)
    return None


_JOB_ID = re.compile(r"/job/(\d+)")


def _safe(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", name).strip("_") or "check"


def save_failed_log(repo: str, run: dict[str, Any], out_dir: pathlib.Path) -> tuple[str | None, str]:
    """失敗したチェックのログ（`gh run view --log-failed`）をファイルへ書き、パスを返す。"""
    m = _JOB_ID.search(str(run.get("details_url") or run.get("html_url") or ""))
    if not m:
        return None, "GitHub Actions のジョブではない"
    r = gh_call.gh(["run", "view", "--repo", repo, "--job", m.group(1), "--log-failed"])
    if r.returncode != 0:
        return None, f"ログを取得できない: {r.stderr.strip()[:200]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"check-{_safe(str(run.get('name') or ''))}-{m.group(1)}.log"
    path.write_text(r.stdout, encoding="utf-8")
    return str(path), ""
