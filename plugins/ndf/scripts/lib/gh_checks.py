"""チェックジョブの読み取りと畳み方（#1142 の L0 で gh_parts から分けた。#632）。"""

from __future__ import annotations

import pathlib
import re
from typing import Any, Callable

import gh_call

CHECK_RUNS_PER_PAGE = 100
CHECK_RUNS_MAX_PAGES = 10
FAILED_CONCLUSIONS = ("failure", "timed_out", "action_required", "startup_failure")


def fetch_check_runs(
    repo: str,
    sha: str,
    rest_get: Callable[[str], Any] | None = None,
    per_page: int = CHECK_RUNS_PER_PAGE,
    max_pages: int = CHECK_RUNS_MAX_PAGES,
    empty_ok: bool = False,
) -> list[dict[str, Any]] | None:
    """head の commit のチェックジョブを `total_count` に届くまで読む（畳む前の生の一覧）。

    **「照会できなかった」と「すべて成功」を区別する。** 失敗・`total_count` 0 はどちらも `None`。
    `empty_ok` を真にすると、照会できてチェックが 1 件も無いとき（push 直後）は空の一覧を返す
    （チェックの開始を待つ側が照会の失敗と分けるため）。
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
            total = _read_total(resp)
            if total is None:
                return None
            if total <= 0:
                return [] if empty_ok else None
        chunk = _page_runs(resp)
        if chunk is None:
            break
        runs.extend(chunk)
        if len(runs) >= total:
            break
    return runs if (runs or empty_ok) else None


def _read_total(resp) -> int | None:
    """応答の `total_count`（無ければ 0）。数として読めなければ None。"""
    try:
        return int(resp.body.get("total_count") or 0)
    except (TypeError, ValueError):
        return None


def _page_runs(resp) -> list[dict[str, Any]] | None:
    """1 ページ分のチェックジョブ（dict の行だけ）。一覧が無いか空なら None（ページ送りの終わり）。"""
    chunk = resp.body.get("check_runs")
    if not isinstance(chunk, list) or not chunk:
        return None
    return [r for r in chunk if isinstance(r, dict)]


def newness(completed: Any, started: Any, number: Any, index: int) -> tuple[int, str, int, int]:
    """チェックの項目の新しさ（大きいほど新しい。#632）。マージの待ちと test-run.py が同じ規則を使う。

    終わった時刻と始まった時刻の新しい方 → 番号（実行・ジョブの番号は増える一方）→ 一覧の中の順で決める。
    時刻がどちらも無い項目（まだ始まっていない）は最も新しいとみなす。ISO 8601 の UTC（`Z` 付き）は
    文字列の比較で時刻の順になる。
    """
    try:
        num = int(number or 0)
    except (TypeError, ValueError):
        num = 0
    stamp = max(str(completed or ""), str(started or ""))
    return (0 if stamp else 1, stamp, num, index)


def fold_latest(items: list[Any], key: Callable[[Any], Any], stamps: Callable[[Any], tuple[Any, Any, Any]]) -> tuple[list[Any], list[Any]]:
    """項目を鍵ごとの最新の 1 件へ畳み、(最新の並び, 置き換わった古い項目の並び) を返す。

    `key` は項目から鍵を、`stamps` は (終わった時刻, 始まった時刻, 番号) を返す。新しさは `newness`。
    最新の並びは最初に現れた鍵の順、古い項目の並びは一覧の順を保つ。
    """
    latest: dict[Any, tuple[tuple, int]] = {}
    order: list[Any] = []
    for i, item in enumerate(items):
        k = key(item)
        rank = newness(*stamps(item), i)
        if k not in latest:
            order.append(k)
            latest[k] = (rank, i)
        elif rank >= latest[k][0]:
            latest[k] = (rank, i)
    keep = {latest[k][1] for k in order}
    return [items[latest[k][1]] for k in order], [item for i, item in enumerate(items) if i not in keep]


def _rest_stamps(run: dict[str, Any]) -> tuple[Any, Any, Any]:
    return run.get("completed_at"), run.get("started_at"), run.get("id")


def fold_check_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同名のチェックジョブを、名前ごとの最新の実行 1 件へ畳む（#632）。

    再実行で `failure` → `success` になったチェックは `success` として返す。新しさは `newness`。
    REST の check run は workflow の名前を持たないため、名前だけで束ねる。並びは最初に現れた名前の順を保つ。
    """
    return fold_latest(runs, lambda r: str(r.get("name") or ""), _rest_stamps)[0]


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


def checks_outcome(runs: list[dict[str, Any]] | None, names: list[str]) -> str | None:
    """待つチェック群の今の結論（CI を待つ側が共有する）。照会できない（`runs` が `None`）ときだけ `None`。

    一覧に**まだ現れていない**チェックは、照会の失敗ではなく `pending` とみなす（push 直後は登録前のため）。
    全て成功なら `success`、未完了があれば `pending`、それ以外は最初の成功でない結論。
    """
    if runs is None:
        return None
    # 畳むのは 1 回だけにし、名前から結果を引く（空の名前は check_result と同じく一致なし）
    by_name = {str(run.get("name") or ""): run_result(run) for run in fold_check_runs(runs)}
    results = [by_name.get(name) if name else None for name in names]
    if any(r is None or r == "pending" for r in results):
        return "pending"
    return "success" if all(r == "success" for r in results) else str(next(r for r in results if r != "success"))


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
