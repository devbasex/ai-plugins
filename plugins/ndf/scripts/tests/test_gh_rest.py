"""PR と課題の単発の読み書き（lib/gh_rest.py）と GraphQL の 1 回の要求（lib/gh_graphql.py）（#1142 の L0・不足 g）。

REST を先に使い、REST が上限のときだけ同じ操作を gh pr / gh issue（GraphQL）で行う。値は GraphQL の --json と同じ形。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gh_fake import RATE, REST_RATE, fake, rest_out  # noqa: E402,F401
import gh_graphql  # noqa: E402
import gh_rest  # noqa: E402

REPO = "o/r"
REST_PR = {"number": 5, "title": "t", "body": "b", "state": "closed", "draft": True, "merged_at": "2026-09-26T00:00:00Z",
           "merge_commit_sha": "m", "html_url": "https://github.com/o/r/pull/5", "user": {"login": "alice"},
           "head": {"ref": "feat/x", "sha": "h"}, "base": {"ref": "develop"}, "labels": [{"name": "bug"}]}


def test_view_reads_rest_first_in_graphql_shape(fake):
    fake.on("api", "-i", f"repos/{REPO}/pulls/5", out=rest_out(REST_PR))
    a = gh_rest.view("pr", 5, "number,state,isDraft,author,headRefName,headRefOid,baseRefName,labels,mergeCommit", REPO)
    assert a.ok and a.via == "rest"
    assert a.value == {"number": 5, "state": "MERGED", "isDraft": True, "author": {"login": "alice"},
                       "headRefName": "feat/x", "headRefOid": "h", "baseRefName": "develop",
                       "labels": [{"name": "bug"}], "mergeCommit": {"oid": "m"}}
    assert not any(a.startswith("pr view") for a in fake.argvs())


def test_view_falls_back_to_graphql_when_rest_is_limited(fake):
    fake.on("api", rc=1, err=REST_RATE)
    fake.on("pr", "view", out=json.dumps({"number": 5}))
    a = gh_rest.view("pr", 5, "number", REPO)
    assert a.ok and a.via == "graphql" and a.value == {"number": 5}


def test_view_with_fields_rest_cannot_make_uses_graphql(fake):
    fake.on("pr", "view", out=json.dumps({"statusCheckRollup": []}))
    a = gh_rest.view("pr", 5, "statusCheckRollup", REPO)
    assert a.value == {"statusCheckRollup": []} and not any(x.startswith("api") for x in fake.argvs())


def test_view_other_rest_failure_is_not_retried(fake):
    fake.on("api", rc=1, err="HTTP 404: Not Found")
    a = gh_rest.view("issue", 9, "number", REPO)
    assert not a.ok and "404" in a.error and fake.argvs() == [f"api -i repos/{REPO}/issues/9"]


def test_issue_list_skips_prs_and_pages_until_the_limit(fake, monkeypatch):
    monkeypatch.setattr(gh_rest, "PER_PAGE", 2)
    pages = {1: [{"number": 1, "title": "a"}, {"number": 2, "pull_request": {}}],
             2: [{"number": 3, "title": "c"}, {"number": 4, "title": "d"}], 3: []}
    fake.on_fn("api", fn=lambda args, stdin: fake_page(pages, args))
    a = gh_rest.issue_list(REPO, "number,title", labels=["bug"], limit=2)
    assert a.value == [{"number": 1, "title": "a"}, {"number": 3, "title": "c"}]
    assert "labels=bug" in fake.calls[0][0][2]


def fake_page(pages, args):
    from gh_call import GhResult
    page = int(args[2].rsplit("page=", 1)[1])
    return GhResult(0, rest_out(pages[page]), "")


def test_pr_list_merged_filters_merged_at(fake):
    fake.on("api", out=rest_out([{"number": 1, "merged_at": None}, {"number": 2, "merged_at": "x"}]))
    a = gh_rest.pr_list(REPO, "number", state="merged")
    assert a.value == [{"number": 2}] and "state=closed" in fake.calls[0][0][2]


def test_list_falls_back_to_gh_list_with_the_same_arguments(fake):
    fake.on("api", rc=1, err=REST_RATE)
    fake.on("issue", "list", out=json.dumps([{"number": 1}]))
    a = gh_rest.issue_list(REPO, "number", state="all", labels=["bug"], limit=5)
    assert a.value == [{"number": 1}] and a.via == "graphql"
    assert fake.calls[-1][0] == ["issue", "list", "--repo", REPO, "--state", "all", "--limit", "5",
                                 "--json", "number", "--label", "bug"]


def test_pr_create_by_rest_and_by_graphql(fake):
    fake.on("api", out=rest_out({"number": 7, "html_url": "https://github.com/o/r/pull/7"}))
    a = gh_rest.pr_create(REPO, "t", "本文", "feat/x", "develop", draft=True)
    assert a.value == {"number": 7, "url": "https://github.com/o/r/pull/7"}
    args, stdin = fake.calls[0]
    assert args[:3] == ["api", "-X", "POST"] and json.loads(stdin)["draft"] is True


def test_pr_create_falls_back_to_gh_pr_create(fake):
    fake.on("api", rc=1, err=REST_RATE)
    fake.on("pr", "create", out="https://github.com/o/r/pull/8\n")
    a = gh_rest.pr_create(REPO, "t", "本文", "feat/x", "develop")
    assert a.value == {"number": 8, "url": "https://github.com/o/r/pull/8"}
    args, stdin = fake.calls[-1]
    assert "--body-file" in args and stdin == "本文" and "--draft" not in args


def test_issue_edit_updates_fields_and_labels(fake):
    fake.on("api", out=rest_out({}))
    a = gh_rest.issue_edit(REPO, 3, body="新", add_labels=["a"], remove_labels=["needs triage"])
    assert a.ok and a.value is True
    assert [[c[0][2], c[0][4]] for c in fake.calls] == [["PATCH", f"repos/{REPO}/issues/3"], ["POST", f"repos/{REPO}/issues/3/labels"],
                                             ["DELETE", f"repos/{REPO}/issues/3/labels/needs%20triage"]]


def test_pr_edit_falls_back_to_gh_pr_edit(fake):
    fake.on("api", rc=1, err=REST_RATE)
    fake.on("pr", "edit", out="")
    a = gh_rest.pr_edit(REPO, 3, title="新", add_labels=["a"])
    assert a.ok and a.via == "graphql"
    assert fake.calls[-1][0] == ["pr", "edit", "3", "--repo", REPO, "--title", "新", "--add-label", "a"]


def test_comment_returns_the_url_either_way(fake):
    fake.on("api", out=rest_out({"html_url": "https://github.com/o/r/issues/3#c1"}))
    assert gh_rest.comment(REPO, 3, "x").value == {"url": "https://github.com/o/r/issues/3#c1"}


def test_pr_merge_checks_the_method_and_passes_the_head(fake):
    with pytest.raises(ValueError):
        gh_rest.pr_merge(REPO, 3, "fast-forward")
    fake.on("api", rc=1, err=REST_RATE)
    fake.on("pr", "merge", out="")
    a = gh_rest.pr_merge(REPO, 3, "squash", sha="abc")
    assert a.value == {"merged": True, "sha": None}
    assert fake.calls[-1][0] == ["pr", "merge", "3", "--repo", REPO, "--squash", "--match-head-commit", "abc"]


def test_pr_merge_by_rest(fake):
    fake.on("api", out=rest_out({"merged": True, "sha": "m"}))
    assert gh_rest.pr_merge(REPO, 3).value == {"merged": True, "sha": "m"}
    assert json.loads(fake.calls[0][1]) == {"merge_method": "merge"}


def test_graphql_via_gh_api(fake):
    fake.on("api", "graphql", out=json.dumps({"data": {"viewer": {"login": "me"}}}))
    a = gh_graphql.graphql("query { viewer { login } }", {"n": 1})
    assert a.value == {"viewer": {"login": "me"}}
    assert json.loads(fake.calls[0][1]) == {"query": "query { viewer { login } }", "variables": {"n": 1}}


def test_graphql_errors_and_limits_are_attempt_errors(fake):
    fake.on("api", "graphql", rc=1, err=RATE)
    assert gh_graphql.graphql("q").limited
    fake.routes.clear()
    fake.on("api", "graphql", out=json.dumps({"errors": [{"message": "bad"}]}))
    a = gh_graphql.graphql("q")
    assert not a.ok and "bad" in a.error
