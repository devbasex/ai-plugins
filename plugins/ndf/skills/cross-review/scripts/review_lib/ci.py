"""継続的統合のチェックの取得と、失敗したチェックの振り分け（#1142 の C2）。

`judge` と `merge-fix` だけが使う。

継続的統合の照会は `commits/{sha}/check-runs` の 1 回だけにする。
**併記された状態（`commits/{sha}/status`）は使わない。** GitHub Actions はチェックジョブを
記録し commit の状態を記録しないため、9 件すべてが成功した commit でも
`state: "pending"` / `total_count: 0` を返す（実測）。保留として読むと、承認された
ラウンドが収束しなくなる。
"""
from __future__ import annotations

import re
from typing import Any, NamedTuple

import gh_parts  # noqa: E402
from review_lib import github  # noqa: E402


# 失敗したチェックジョブの名前の振り分け。**一覧に無い名前は code-related へ倒す。**
CI_META_PATTERNS = ("check_pr_requirements", "assignees", "reviewers", "labels", "meta")
# メタチェックの名前は**語として**一致したときだけ meta-only にする。部分一致で拾うと
# `metabase tests` や `metadata lint` のようなコードチェックまで meta-only になり、失敗した
# まま収束する。前後が英数字でないことを求めるため、区切り（空白・`_`・`-`・`/`）で
# 挟まれた語だけが一致する。**一覧に無い名前を code-related へ倒す既定は変わらない。**
_CI_META_RE = re.compile(
    "(?<![0-9a-z])(?:" + "|".join(re.escape(p) for p in CI_META_PATTERNS) + ")(?![0-9a-z])")
# 完了したチェックジョブのうち、失敗として数える結論。`cancelled` / `skipped` / `neutral`
# は失敗にしない。
CI_FAILED_CONCLUSIONS = ("failure", "timed_out", "action_required", "startup_failure")

# チェックジョブの一覧は 1 ページ 100 件（REST の上限）で読む。**既定の 30 件のままにしない。**
# 31 件目以降に code-related の失敗があるリポジトリでは、失敗を見ないまま収束する。
CHECK_RUNS_PER_PAGE = 100
# 読むページ数の上限。100 件で収まるリポジトリは 1 回のままである（このリポジトリは 9 件）。
# 上限に達しても止めず、読めた範囲で判定する。**進行を止めない側へ倒す。**
CHECK_RUNS_MAX_PAGES = 10


class CiClassification(NamedTuple):
    """チェックジョブを、修正の要る失敗・コードと無関係な失敗・未完了へ分けた結果。"""

    code_failed: list[str]
    meta_failed: list[str]
    pending: list[str]


def _classify_ci(runs: list[dict[str, Any]]) -> CiClassification:
    """チェックジョブを振り分ける。`cmd_judge` と `cmd_merge_fix` が同じ実装を呼ぶ。

    **`status` が `completed` 以外のチェックジョブは失敗にしない。** 完了を待たずに
    未完了として別に返し、呼び出し側が「未完了のまま収束した」ことを残す。
    """
    code_failed: list[str] = []
    meta_failed: list[str] = []
    pending: list[str] = []
    for run in runs:
        name = str(run.get("name") or "")
        if str(run.get("status") or "completed").lower() != "completed":
            pending.append(name)
            continue
        if str(run.get("conclusion") or "").lower() not in CI_FAILED_CONCLUSIONS:
            continue
        if _CI_META_RE.search(name.lower()):
            meta_failed.append(name)
        else:
            # 一覧に無い名前も含めて code-related へ倒す（保守的）。
            code_failed.append(name)
    return CiClassification(code_failed, meta_failed, pending)


def _classify_failed_names(names: list[str]) -> CiClassification:
    """申告された失敗名の一覧を振り分ける薄い入口。

    修正の担当が申告するのは、完了した失敗の名前だけである。分類層の入力の形
    （`name` / `status` / `conclusion`）と、完了・失敗を表す文字列（`completed` /
    `failure`）を握るのはここ 1 か所にする。呼び出し側は失敗名の一覧を渡すだけでよい。
    """
    return _classify_ci(
        [{"name": str(n), "status": "completed", "conclusion": "failure"} for n in names]
    )


def _fetch_check_runs(repo: str, sha: str) -> list[dict[str, Any]] | None:
    """head の commit に対するチェックジョブの一覧を返す。照会できなければ `None`。

    **「照会できなかった」と「すべて成功」を区別する。** `HTTP 422`（GitHub 側に
    無い commit）も `total_count` が 0 のリポジトリも、失敗が無いことの根拠に
    ならない。どちらも `None` を返し、呼び出し側は収束を止めずに理由を残す。

    **`total_count` に届くまでページを読む。** 1 ページの上限は 100 件で、
    `total_count` はページの件数ではなく全体の件数を返す。読み切らないまま
    `_classify_ci` へ渡すと、後ろのページにある失敗が無いものとして扱われる。
    100 件で収まるリポジトリは 1 回で終わり、呼び出し回数は変わらない。

    **同名のチェックジョブは名前ごとの最新の実行へ畳む**（#632）。再実行で `failure` →
    `success` になったチェックを失敗として数えない。読み方と畳み方は共通層の `gh_parts`
    （`pr-info --with checks` と同じ実装）が持ち、ここは REST の呼び出しだけを渡す。
    """
    runs = gh_parts.fetch_check_runs(
        repo, sha, rest_get=lambda path: github._gh_rest(path),
        per_page=CHECK_RUNS_PER_PAGE, max_pages=CHECK_RUNS_MAX_PAGES)
    return gh_parts.fold_check_runs(runs) if runs else None


def _round_ci(st: dict[str, Any], last: dict[str, Any], pr: int) -> dict[str, Any]:
    """収束の直前にチェックジョブを 1 度だけ照会し、判定に使う記録を返す。

    head の commit は `rounds[-1].head_sha` から読む。承認したレビューが読んだ commit と
    同じ値であり、追加の呼び出しが要らない。値が無いときだけ REST を 1 回投げる。

    **照会できないことは、承認されたラウンドを差し戻す理由にならない。** `gh` の失敗・
    `HTTP 422`・チェックジョブ 0 件はいずれも `unverified` として収束させ、確かめられ
    なかったことを記録に残す。
    """
    repo = str(st.get("repo") or "")
    sha = str(last.get("head_sha") or "")
    if not sha:
        meta = github._fetch_pr_metadata(pr, repo or None)
        if meta is not None:
            sha = meta.head_sha
            repo = repo or meta.repo
    if not repo or not sha:
        return {"verdict": "unverified", "reason": "head のコミットを特定できない"}
    runs = _fetch_check_runs(repo, sha)
    if runs is None:
        return {
            "verdict": "unverified",
            "reason": "チェックジョブを照会できない（未 push・権限・チェックジョブ 0 件のいずれか）",
            "sha": sha,
        }
    c = _classify_ci(runs)
    if c.code_failed:
        return {"verdict": "code_failure", "sha": sha, "failed": c.code_failed,
                "meta_failed": c.meta_failed, "pending": c.pending}
    if c.meta_failed:
        return {"verdict": "meta_only", "sha": sha, "meta_failed": c.meta_failed,
                "pending": c.pending,
                "note": f"メタチェックのみ失敗: {c.meta_failed} — コードと無関係のため収束"}
    if c.pending:
        return {"verdict": "pending", "sha": sha, "pending": c.pending}
    return {"verdict": "success", "sha": sha}
