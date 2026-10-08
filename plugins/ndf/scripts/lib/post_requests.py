"""投稿の待ち行列（`post_queue.py`）が送る内容を、種別ごとに組み立てる。

項目の種別ごとに、送る要求（`request`）と冪等の照会で「同じ」とみなす条件（`match`）を返す。
`post_queue.py` から分けた純粋な関数で、`gh` を呼ばない（#1843 で `post_queue.py` の行数を保つために分けた）。
"""

from __future__ import annotations

from typing import Any

_RESOLVE_MUTATION = """
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread { id isResolved }
  }
}
"""


def _request_pr_comment(repo: str, pr: int, fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "request": {"method": "POST", "path": f"repos/{repo}/issues/{int(pr)}/comments", "fields": {"body": fields["body"]}},
        "match": {"body": fields["body"]},
    }


def _request_review_post(repo: str, pr: int, fields: dict[str, Any]) -> dict[str, Any]:
    body = {"body": fields.get("body", ""), "event": fields["event"]}
    # **投稿先の commit は積んだ時点で決める。** Reviews API は `commit_id` を
    # 省くと送った時点の head へ付けるため、積んでから流すまでに head が進むと、
    # レビューが読んでいない commit に付く。行を指す `comments` はその commit の
    # 差分で解決されるため、位置がずれるか 422 で落ちる。ラウンド開始時に読んだ
    # `rounds[-1].head_sha` を呼び出し側が渡す。
    if fields.get("commit_id"):
        body["commit_id"] = fields["commit_id"]
    if fields.get("comments"):
        body["comments"] = fields["comments"]
    match = {"event": fields["event"], "body": fields.get("body", "")}
    # **照合をラウンドの開始より後へ絞る。** ラウンドの番号は実行ごとに 1 から数え
    # 直すため、番号と席の鍵だけでは前の実行のレビューに一致する。
    if fields.get("since"):
        match["since"] = fields["since"]
    return {
        "request": {"method": "POST", "path": f"repos/{repo}/pulls/{int(pr)}/reviews", "fields": body},
        "match": match,
    }


def _request_review_reply(repo: str, pr: int, fields: dict[str, Any]) -> dict[str, Any]:
    target = int(fields["in_reply_to"])
    return {
        "request": {
            "method": "POST",
            "path": f"repos/{repo}/pulls/{int(pr)}/comments/{target}/replies",
            "fields": {"body": fields["body"]},
        },
        "match": {"in_reply_to": target, "body": fields["body"]},
    }


def _request_thread_resolve(repo: str, pr: int, fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "request": {"method": "GRAPHQL", "path": "graphql", "query": _RESOLVE_MUTATION, "fields": {"threadId": fields["thread_id"]}},
        "match": {"thread_id": fields["thread_id"]},
    }


# 種別ごとの組み立て。**網羅はこの表が持つ。** 制御構文に散らすと、種別を足したときに
# どの枝を足し忘れたかが読めない。
_REQUEST_BUILDERS = {
    "pr-comment": _request_pr_comment,
    "review-post": _request_review_post,
    "review-reply": _request_review_reply,
    "thread-resolve": _request_thread_resolve,
}


def request_for(kind: str, repo: str, pr: int, fields: dict[str, Any]) -> dict[str, Any]:
    """種別ごとに、送る要求と冪等の照合条件を組む。"""
    builder = _REQUEST_BUILDERS.get(kind)
    if builder is None:
        raise ValueError(f"未知の種別: {kind}")
    return builder(repo, pr, fields)
