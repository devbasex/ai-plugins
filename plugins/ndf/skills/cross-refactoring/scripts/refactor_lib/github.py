"""GitHub の照会（レビュースレッドの解決と継続的統合のチェックジョブ）を、cross-refactoring が読む形へ変える。"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from typing import Any, Optional

import gh_parts

from . import info
from .paths import sh


_REVIEW_THREADS_QUERY = """
query($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved }
      }
    }
  }
}
"""


def _fetch_review_threads_page(
    owner: str, name: str, pr: int, cursor: Optional[str]
) -> Optional[dict[str, Any]]:
    """レビュースレッドを 1 ページ分だけ取得する。取れなければ `None` を返す。

    呼び出しの失敗と応答の解釈の失敗を、どちらも `None` へ畳む。ページ送りの側は
    「取れたか」だけを見ればよく、GraphQL の呼び方を知らずに済む。
    """
    cmd = [
        "gh", "api", "graphql",
        "-f", f"query={_REVIEW_THREADS_QUERY}",
        "-F", f"owner={owner}", "-F", f"repo={name}", "-F", f"pr={pr}",
    ]
    if cursor:
        cmd += ["-F", f"cursor={cursor}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        info(f"⚠ レビュースレッドの取得に失敗しました: {r.stderr.strip()[:200]}")
        return None
    try:
        return (
            json.loads(r.stdout)["data"]["repository"]["pullRequest"]["reviewThreads"]
        )
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        info(f"⚠ レビュースレッドの応答を解釈できませんでした: {e}")
        return None


def resolved_threads_on_github(repo: str, pr: int) -> Optional[set[str]]:
    """GitHub 上で実際に解決済みのレビュースレッド ID を返す。

    取得できなければ `None` を返す。呼び出し側は**空集合と区別する**こと。
    「取得できなかった」を「解決済みが 0 件」と混同すると、通信が失敗しただけで
    全ての指摘を未解決扱いにするか、逆に自己申告を素通しすることになる。
    """
    owner, _, name = repo.partition("/")
    if not owner or not name:
        return None
    resolved: set[str] = set()
    cursor: Optional[str] = None
    while True:
        threads = _fetch_review_threads_page(owner, name, pr, cursor)
        if threads is None:
            return None
        resolved.update(
            n["id"] for n in threads.get("nodes", []) if n.get("isResolved")
        )
        page = threads.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return resolved
        cursor = page.get("endCursor")
        if not cursor:
            return resolved


# 継続的統合の照会は `commits/{sha}/check-runs` だけにする。**併記された状態
# （`commits/{sha}/status`）は使わない。** GitHub Actions はチェックジョブを記録し commit の
# 状態を記録しないため、すべて成功した commit でも `pending` を返す。保留として読むと、
# 通っているチェックで通過できなくなる。
#
# ページの読み方と、同名のチェックジョブを名前ごとの最新の実行へ畳む処理は共通層の
# `gh_parts`（`pr-info --with checks` と cross-review の判定が使う実装）が持つ（#632）。


def _gh_api_get(path: str) -> Optional[SimpleNamespace]:
    """`gh api <path>` の JSON を `.body` に持つ応答。照会できなければ `None`。"""
    out = sh(["gh", "api", path], check=False)
    if not out:
        return None
    try:
        return SimpleNamespace(body=json.loads(out))
    except json.JSONDecodeError:
        return None


def check_run_result(repo: str, sha: str, name: str) -> Optional[str]:
    """名前が一致したチェックジョブの、最新の実行の結果を 1 つの語で返す。

    - 完了して結論が `success` なら `"success"`
    - 未完了なら `"pending"`
    - それ以外はその結論（`"failure"` など。空なら `"unknown"`）
    - **照会できない・名前が一致するチェックが 1 件も無いときは `None`**

    **「照会できなかった」と「成功した」を区別する。** 呼び出し側は `None` を
    通過させない（fail-closed）。名前で絞るのは、別のチェックの成功で通さないためである。
    別の実行で成功した同名のチェックの、前の実行の失敗は数えない。
    """
    if not repo or not sha or not name:
        return None
    runs = gh_parts.fetch_check_runs(repo, sha, rest_get=_gh_api_get)
    return gh_parts.check_result(runs, name)
