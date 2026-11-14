"""後片付けを終えた PR が閉じる課題に、工程「後片付け」の記録を付ける口（N8a・#1893）。

後片付けの本体（`ndf worktree merged`）が終わった後に呼ぶ。課題ごとに本文の進行の節を読み、行が未チェックなら
チェックを付けて本文を書き直す。記録済みの課題は打ち直さない。読めない・書けない課題は failed にし、後片付けの
本体は戻さない（結果は欠けの残る終わり）。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from ndf.adapters.github import GitHub, GitHubError
from ndf.domains.records import close_decide as cd

# 結果の items の result の語
RECORDED, SKIPPED, FAILED = "recorded", "skipped", "failed"
# PR が閉じる課題を読むクエリ（上限は 1 つの PR が閉じる課題の数として十分な件数）
_CLOSING = "closingIssuesReferences(first: 50) { nodes { number } }"


def closing_issues(hub: GitHub, prs: Iterable[int]) -> dict[int, list[int]]:
    """PR ごとの閉じる課題の番号。読めなければ `GitHubError`。"""
    got = hub.nodes(prs, "pullRequest", _CLOSING)
    out: dict[int, list[int]] = {}
    for pr, node in got.items():
        refs = node["closingIssuesReferences"] if "closingIssuesReferences" in node else None
        nodes = refs["nodes"] if isinstance(refs, dict) and isinstance(refs.get("nodes"), list) else []
        out[pr] = [int(n["number"]) for n in nodes if isinstance(n, dict) and isinstance(n.get("number"), int)]
    return out


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M")


def record_issue(hub: GitHub, issue: int, stamp: str) -> dict[str, Any]:
    """課題 1 件に工程「後片付け」の記録を付け、結果の項目を返す。"""
    try:
        body = hub.issue_body(issue).body
        marked = cd.mark_stage(body, cd.CLEANUP_STAGE, stamp)
        if marked is None:
            return {"issue": issue, "result": SKIPPED, "reason": "記録済みか、進行の節に工程の行が無い"}
        hub.rest("PATCH", f"/repos/{hub.repo}/issues/{int(issue)}", {"body": marked})
    except GitHubError as exc:
        return {"issue": issue, "result": FAILED, "reason": str(exc)}
    return {"issue": issue, "result": RECORDED}


def record_cleanup_stage(hub: GitHub, prs: Iterable[int]) -> list[dict[str, Any]]:
    """PR が閉じる課題ごとに記録を付ける。PR の閉じる課題を読めなければ、PR ごとに failed の項目を返す。"""
    wanted = sorted({int(p) for p in prs})
    try:
        closing = closing_issues(hub, wanted)
    except GitHubError as exc:
        return [{"pr": pr, "result": FAILED, "reason": f"閉じる課題を読めない: {exc}"} for pr in wanted]
    stamp = _stamp()
    seen: dict[int, None] = {}
    for pr in wanted:
        for issue in closing[pr]:
            seen.setdefault(issue, None)
    return [record_issue(hub, issue, stamp) for issue in seen]
