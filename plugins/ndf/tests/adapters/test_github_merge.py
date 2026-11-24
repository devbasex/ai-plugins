"""`GitHub.merge` の直前の確かめと、マージ先の設定の読み取り（#4409 の決定 3・決定 8）。

要求は `GitHub.rest`・`GitHub.graphql` の口で受け、数と順を確かめる（通信の層は `test_github.py` が見る）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import pytest

from ndf.adapters import github
from ndf.adapters.github import FileAt, GitHubError, MergeExpect, MergeOutcome, Pull

import scenes

REPO = "o/r"
HEAD = "a" * 40
OTHER = "b" * 40
PATH = ".ndf/project.json"
METHODS = {("GET", f"/repos/{REPO}/rules/branches/main"): [], ("GET", f"/repos/{REPO}"): {"allow_merge_commit": True}}


def _pull(base: str = "main", head: str = HEAD) -> Pull:
    return Pull(7, "open", False, head, "sprint/x", base, "", "clean", "octocat", "OWNER")


class Wire:
    """`rest`・`graphql` の偽物。要求を `seen` に残し、応答は関数で決める。"""

    def __init__(self, verify: Optional[dict] = None, queue: Optional[dict] = None, put: Any = None, after: Optional[Pull] = None) -> None:
        self.seen: list[tuple[str, str]] = []
        self.verify = verify if verify is not None else _verify_data()
        self.queue = queue
        self.put = put
        self.after = after

    def rest(self, method: str, path: str, payload: Any = None, params: Any = None) -> Any:
        self.seen.append((method, path))
        if method == "PUT":
            if isinstance(self.put, Exception):
                raise self.put
            return {"merged": True}
        if path == f"/repos/{REPO}/pulls/7":
            pr = self.after or _pull()
            return {
                "number": 7, "state": "open", "merged": False, "body": "", "mergeable_state": "clean",
                "head": {"sha": pr.head_sha, "ref": pr.head_ref}, "base": {"ref": pr.base_ref},
                "user": {"login": "octocat"}, "author_association": "OWNER",
            }  # fmt: skip
        if path == "/user":
            return {"login": "octocat"}
        return METHODS.get((method, path), {})

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        name = "enqueue" if "enqueuePullRequest" in query else "dequeue" if "dequeuePullRequest" in query else "query"
        if name == "query" and "isMergeQueueEnabled" in query:
            name = "queue"
        elif name == "query" and "pullRequest" in query:
            name = "verify"
        elif name == "query" and "repository" not in query:
            name = "viewer"
        elif name == "query":
            name = "file"
        self.seen.append(("GRAPHQL", name))
        if name == "queue":
            return {"repository": self.queue}
        if name == "verify":
            return {"repository": self.verify}
        if name == "viewer":
            return {"viewer": {"login": "octocat"}}
        if name == "file":
            return {"viewer": {"login": "octocat"}, "repository": {"nameWithOwner": REPO, "object": {"oid": "c1", "text": "{}"}}}
        return {}


def _verify_data(base: str = "main", head: str = HEAD, oid: str = "c1", text: str = "{}", repo: str = REPO) -> dict:
    return {"nameWithOwner": repo, "pullRequest": {"baseRefName": base, "headRefOid": head}, "config": {"oid": oid, "text": text}}


def _queue_data(base: str = "main", oid: str = "c1", in_queue: bool = False) -> dict:
    pr = {"baseRefName": base, "headRefOid": HEAD, "id": "PR_7", "isMergeQueueEnabled": True, "isInMergeQueue": in_queue}
    return {"nameWithOwner": REPO, "pullRequest": pr, "config": {"oid": oid, "text": "{}"}, "mergeQueue": None}


def _hub(wire: Wire) -> github.GitHub:
    hub = github.GitHub(REPO, _client())
    object.__setattr__(hub, "rest", wire.rest)
    object.__setattr__(hub, "graphql", wire.graphql)
    return hub


def _client() -> Any:
    from githubkit import GitHub as Client

    return Client("t", http_cache=False, auto_retry=False)


def _expect(rejudge: Callable[[Optional[str]], Optional[MergeOutcome]] = lambda text: None, base: str = "main") -> MergeExpect:
    return MergeExpect(_pull(), base, HEAD, PATH, "c1", rejudge)


def _merge_without_expect_keeps_the_request_count():
    wire = Wire()
    out = _hub(wire).merge(7, expect_head=HEAD)
    assert out.merged
    assert [s[0] for s in wire.seen] == ["GET", "GET", "GET", "PUT"]


def _merge_with_expect_verifies_right_before_sending_and_does_not_reread_the_pull():
    wire = Wire()
    out = _hub(wire).merge(7, expect_head=HEAD, expect=_expect())
    assert out.merged
    # 最初の PR の読み直しは確かめへ移る（方式の問い合わせ 2 回の後、PUT の直前）
    assert wire.seen == [
        ("GET", f"/repos/{REPO}/rules/branches/main"),
        ("GET", f"/repos/{REPO}"),
        ("GRAPHQL", "verify"),
        ("PUT", f"/repos/{REPO}/pulls/7/merge"),
    ]


def _merge_refuses_when_the_pr_changed_after_the_verdict(verify, kind):
    """r1-6: マージ先か HEAD が動いたら `TARGET_MOVED`（呼び手が承認を取り直す）、ベースリポジトリが違えば `refused`。"""
    wire = Wire(verify=verify)
    out = _hub(wire).merge(7, expect_head=HEAD, expect=_expect())
    assert (out.merged, out.kind) == (False, kind)
    assert ("PUT", f"/repos/{REPO}/pulls/7/merge") not in wire.seen


def _changed_config_is_rejudged_from_the_same_response():
    texts: list[Optional[str]] = []

    def rejudge(text: Optional[str]) -> Optional[MergeOutcome]:
        texts.append(text)
        return MergeOutcome(False, "production_gate", "承認が要る", HEAD)

    wire = Wire(verify=_verify_data(oid="c2", text='{"x": 1}'))
    out = _hub(wire).merge(7, expect_head=HEAD, expect=_expect(rejudge))
    assert (out.merged, out.kind) == (False, "production_gate")
    assert texts == ['{"x": 1}']
    # 設定を読み直す要求を足さない
    assert [s for s in wire.seen if s[0] == "GRAPHQL"] == [("GRAPHQL", "verify")]
    assert ("PUT", f"/repos/{REPO}/pulls/7/merge") not in wire.seen


def _changed_config_that_still_allows_the_merge_sends_it():
    wire = Wire(verify=_verify_data(oid="c2"))
    assert _hub(wire).merge(7, expect_head=HEAD, expect=_expect(lambda text: None)).merged


def _queue_refusal() -> GitHubError:
    return GitHubError("merge queue", 405)


def _queue_is_verified_in_the_same_request_and_reread_after_enqueue():
    wire = Wire(queue=_queue_data(), put=_queue_refusal())
    out = _hub(wire).merge(7, expect_head=HEAD, expect=_expect())
    assert (out.merged, out.kind) == (False, "ci_pending")
    assert [s for s in wire.seen if s[0] == "GRAPHQL"] == [("GRAPHQL", "verify"), ("GRAPHQL", "queue"), ("GRAPHQL", "enqueue")]
    assert wire.seen[-1] == ("GET", f"/repos/{REPO}/pulls/7")


def _queue_refuses_when_the_base_changed_before_enqueue():
    wire = Wire(queue=_queue_data(base="release"), put=_queue_refusal())
    out = _hub(wire).merge(7, expect_head=HEAD, expect=_expect())
    assert (out.merged, out.kind) == (False, github.github_merge.TARGET_MOVED)
    assert ("GRAPHQL", "enqueue") not in wire.seen


def _queue_dequeues_when_the_base_changed_after_enqueue():
    wire = Wire(queue=_queue_data(), put=_queue_refusal(), after=_pull(base="release"))
    out = _hub(wire).merge(7, expect_head=HEAD, expect=_expect())
    assert (out.merged, out.kind) == (False, github.github_merge.TARGET_MOVED)
    assert ("GRAPHQL", "dequeue") in wire.seen


def _branch_file_reads_text_and_oid_with_the_repository_name():
    wire = Wire()
    got = _hub(wire).branch_file("main", PATH)
    assert got == FileAt(REPO, "{}", "c1")
    assert wire.seen == [("GRAPHQL", "file")]


def _viewer_login_is_read_once():
    wire = Wire()
    hub = _hub(wire)
    assert hub.viewer_login() == "octocat"
    assert hub.viewer_login() == "octocat"
    assert wire.seen == [("GRAPHQL", "viewer")]


def _viewer_login_after_branch_file_sends_no_request():
    """r1-7: マージ先の設定の読み取りが同じ要求で login を読むので、login の読み取りの要求を増やさない。"""
    wire = Wire()
    hub = _hub(wire)
    hub.branch_file("main", PATH)
    assert hub.viewer_login() == "octocat"
    assert wire.seen == [("GRAPHQL", "file")]


@pytest.mark.parametrize(
    scenes.ARGS,
    [
        scenes.scene(_merge_without_expect_keeps_the_request_count, id="merge_without_expect_keeps_the_request_count"),
        scenes.scene(
            _merge_with_expect_verifies_right_before_sending_and_does_not_reread_the_pull,
            id="merge_with_expect_verifies_right_before_sending_and_does_not_reread_the_pull",
        ),
        *[
            scenes.scene(
                _merge_refuses_when_the_pr_changed_after_the_verdict,
                id=f"merge_refuses_when_the_pr_changed_after_the_verdict-{label}",
                verify=v,
                kind=k,
            )
            for label, v, k in zip(
                ["base-changed", "head-changed", "other-repo"],
                [_verify_data(base="release"), _verify_data(head=OTHER), _verify_data(repo="o/other")],
                [github.github_merge.TARGET_MOVED, github.github_merge.TARGET_MOVED, "refused"],
                strict=True,
            )
        ],
        scenes.scene(_changed_config_is_rejudged_from_the_same_response, id="changed_config_is_rejudged_from_the_same_response"),
        scenes.scene(_changed_config_that_still_allows_the_merge_sends_it, id="changed_config_that_still_allows_the_merge_sends_it"),
        scenes.scene(
            _queue_is_verified_in_the_same_request_and_reread_after_enqueue,
            id="queue_is_verified_in_the_same_request_and_reread_after_enqueue",
        ),
        scenes.scene(_queue_refuses_when_the_base_changed_before_enqueue, id="queue_refuses_when_the_base_changed_before_enqueue"),
        scenes.scene(_queue_dequeues_when_the_base_changed_after_enqueue, id="queue_dequeues_when_the_base_changed_after_enqueue"),
        scenes.scene(
            _branch_file_reads_text_and_oid_with_the_repository_name, id="branch_file_reads_text_and_oid_with_the_repository_name"
        ),
        scenes.scene(_viewer_login_is_read_once, id="viewer_login_is_read_once"),
        scenes.scene(_viewer_login_after_branch_file_sends_no_request, id="viewer_login_after_branch_file_sends_no_request"),
    ],
)
def test_merge_verification_scenes(request, scene, values):
    """GitHub.merge の直前の確かめとマージ先の設定の読み取りの場面（#4409 の決定 3・決定 8）。"""
    scenes.play(request, scene, values)
