"""入れ子の読み取りと REST に無い操作（GraphQL）（#1142 の L0・決定 16）。

レビューのスレッドとコメントのような入れ子は、GraphQL なら 1 回で読める（REST ではページとスレッドの数だけ要る）。
スレッドの resolve・Projects のボード・draft を ready にする操作は REST に無く、GraphQL の枠でしか行えない。
"""
from __future__ import annotations

import json
from typing import Any, Callable

import gh_call
import gh_quota

UNRESOLVED_THREADS_QUERY = """
query($owner: String!, $name: String!, $pr: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100, after: $endCursor) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved path line }
      }
    }
  }
}
"""

UNRESOLVED_THREADS_JQ = (
    ".data.repository.pullRequest.reviewThreads.nodes[]"
    " | select(.isResolved == false)"
    ' | [.id, (.path // ""), (.line // "" | tostring)] | @tsv'
)


def unresolved_threads(repo: str, pr: int,
                       output: Callable[[list[str]], str | None] | None = None
                       ) -> list[dict[str, str]] | None:
    """未解決のレビュースレッドを `thread_id` つきで返す。0 件は空の一覧、取得できなければ `None`。

    `output` は `gh` の argv を受けて標準出力（失敗は `None`）を返す関数。省くと `RUNNER` を使う。
    Resolve の状態は REST から読めないため、上限のときは退避せず `None` を返す。
    """
    owner, sep, name = str(repo or "").partition("/")
    if not (owner and sep and name):
        return None
    out = (output or gh_call._output_via_runner)([
        "gh", "api", "graphql", "--paginate",
        "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"pr={int(pr)}",
        "-f", f"query={UNRESOLVED_THREADS_QUERY}",
        "--jq", UNRESOLVED_THREADS_JQ,
    ])
    if out is None:
        return None
    threads: list[dict[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        threads.append({
            "thread_id": cols[0],
            "path": cols[1] if len(cols) > 1 else "",
            "line": cols[2] if len(cols) > 2 else "",
        })
    return threads


def graphql(query: str, variables: dict[str, Any] | None = None) -> gh_quota.Attempt:
    """GraphQL の 1 回の要求。値は応答の `data`。上限は `Attempt.limited` で分かる（代われないので待つのは呼び出し側）。

    githubkit が使えれば githubkit で、使えなければ `gh api graphql` で送る。変数は JSON の型のまま渡す。
    """
    c = gh_call.client()
    if c is not None:
        try:
            return gh_quota.Attempt(c.graphql(query, variables or {}), "", "graphql")
        except Exception as exc:  # noqa: BLE001  githubkit の失敗（上限・GraphQL のエラー・通信）を 1 つの形へ揃える
            return gh_quota.Attempt(None, f"{type(exc).__name__}: {exc}"[:500] or "GraphQL が失敗", "graphql")
    payload = json.dumps({"query": query, "variables": variables or {}}, ensure_ascii=False)
    r = gh_call.gh(["api", "graphql", "--input", "-"], payload)
    try:
        d = json.loads(r.stdout) if r.stdout.strip() else None
    except ValueError:
        d = None
    errors = (d or {}).get("errors") if isinstance(d, dict) else None
    if r.returncode != 0 or errors or not isinstance(d, dict):
        why = (r.stderr.strip() or json.dumps(errors, ensure_ascii=False) if errors else r.stderr.strip()) \
            or "GraphQL の応答を読めない"
        return gh_quota.Attempt(None, why, "graphql")
    return gh_quota.Attempt(d.get("data"), "", "graphql")
