"""GitHub のアダプター（`ndf.adapters.github`）のうち、バージョンの無いリリース記録が使う読み書き（#4580）。

ブランチの更新の記録・CD の実行の一覧・コメントの削除。githubkit へ `httpx.MockTransport` を渡し、ネットワークへ出ない。
"""

from __future__ import annotations

import httpx
import pytest

from ndf.adapters import github

from ._github_helpers import RouteServer, hub

REPO = "devbasex/ai-plugins"
HEAD = "2bb66a6c14a618cbac108b61737ad49cfb1ac48b"


def _activity(after: str, before: str) -> dict:
    return {"before": before, "after": after, "activity_type": "push", "ref": "refs/heads/main"}


def test_branch_activity_reads_pages_by_the_next_cursor_until_the_caller_stops() -> None:
    """#4580: アクティビティはページ番号でなく Link の next のカーソル（after）で送る。呼び手が打ち切れば次のページを読まない。"""
    path = f"/repos/{REPO}/activity"
    link = f'<https://api.github.com/repositories/1{path}?ref=refs%2Fheads%2Fmain&per_page=100&after=CUR>; rel="next"'
    server = RouteServer(
        {
            ("GET", path): [
                httpx.Response(200, json=[_activity("c3", "c2")], headers={"link": link}),
                httpx.Response(200, json=[_activity("c2", "c1")]),
            ]
        }
    )
    got = hub(REPO, server).branch_activity("main")
    assert next(got)["after"] == "c3" and len(server.seen) == 1
    assert [a["after"] for a in got] == ["c2"]
    assert [dict(r.url.params) for r in server.seen] == [
        {"ref": "refs/heads/main", "per_page": "100"},
        {"ref": "refs/heads/main", "per_page": "100", "after": "CUR"},
    ]


def test_branch_activity_stops_at_the_page_cap(monkeypatch) -> None:
    path = f"/repos/{REPO}/activity"
    link = f'<https://api.github.com{path}?after=CUR>; rel="next"'
    server = RouteServer({("GET", path): [httpx.Response(200, json=[_activity("c3", "c2")], headers={"link": link})]})
    monkeypatch.setattr(github, "_page_cap", lambda name: 2)
    with pytest.raises(github.GitHubError, match="github.list-pages"):
        list(hub(REPO, server).branch_activity("main"))
    assert len(server.seen) == 2


def test_workflow_runs_reads_only_push_runs_of_the_commit() -> None:
    """#4580: CD の実行は workflow・ブランチ・コミットに加えて起動のイベント push で絞る（手動の実行を根拠にしない）。"""
    path = f"/repos/{REPO}/actions/workflows/deploy.yml/runs"
    runs = [{"id": 1, "status": "completed", "conclusion": "success"}]
    server = RouteServer({("GET", path): [httpx.Response(200, json={"total_count": 1, "workflow_runs": runs})]})
    assert hub(REPO, server).workflow_runs("deploy.yml", "main", HEAD, timeout=5.0) == runs
    assert dict(server.seen[0].url.params) == {"branch": "main", "head_sha": HEAD, "event": "push", "per_page": "100"}
    assert server.seen[0].extensions["timeout"]["read"] == 5.0


def test_delete_comment_sends_delete_and_raises_on_failure() -> None:
    path = f"/repos/{REPO}/issues/comments/42"
    server = RouteServer({("DELETE", path): [httpx.Response(204), httpx.Response(404, json={"message": "Not Found"})]})
    hub(REPO, server).delete_comment(42)
    assert [r.method for r in server.seen] == ["DELETE"]
    with pytest.raises(github.GitHubError):
        hub(REPO, server).delete_comment(42)


def test_commits_pulls_reads_every_commit_in_one_query_and_pull_commit_count() -> None:
    """#4580: コミットに紐づく PR の番号はコミットの数によらず 1 回のクエリで読み、GitHub に無いコミットは None。
    PR のコミットの数（rebase の PR の差分の起点を決める）は REST の 1 回。"""
    other = "1" * 40
    answer = {
        "data": {
            "repository": {
                "a0": {"associatedPullRequests": {"nodes": [{"number": 4485}, {"number": 12}]}},
                "a1": None,
            }
        }
    }
    routes = {
        ("POST", "/graphql"): [httpx.Response(200, json=answer)],
        ("GET", f"/repos/{REPO}/pulls/4485"): [httpx.Response(200, json={"number": 4485, "commits": 26})],
    }
    server = RouteServer(routes)
    assert hub(REPO, server).commits_pulls([HEAD, other]) == {HEAD: [4485, 12], other: None}
    assert [r.method for r in server.seen] == ["POST"]
    assert hub(REPO, server).pull_commit_count(4485) == 26
    with pytest.raises(ValueError):
        hub(REPO, server).commits_pulls(['x") { id } a: object(oid: "y'])
