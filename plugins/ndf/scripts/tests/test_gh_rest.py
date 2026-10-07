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
REST_PR = {
    "number": 5,
    "title": "t",
    "body": "b",
    "state": "closed",
    "draft": True,
    "merged_at": "2026-09-26T00:00:00Z",
    "merge_commit_sha": "m",
    "html_url": "https://github.com/o/r/pull/5",
    "user": {"login": "alice"},
    "head": {"ref": "feat/x", "sha": "h"},
    "base": {"ref": "develop"},
    "labels": [{"name": "bug"}],
}


def test_view_reads_rest_first_in_graphql_shape(fake):
    fake.on("api", "-i", f"repos/{REPO}/pulls/5", out=rest_out(REST_PR))
    a = gh_rest.view("pr", 5, "number,state,isDraft,author,headRefName,headRefOid,baseRefName,labels,mergeCommit", REPO)
    assert a.ok and a.via == "rest"
    assert a.value == {
        "number": 5,
        "state": "MERGED",
        "isDraft": True,
        "author": {"login": "alice"},
        "headRefName": "feat/x",
        "headRefOid": "h",
        "baseRefName": "develop",
        "labels": [{"name": "bug"}],
        "mergeCommit": {"oid": "m"},
    }
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
    pages = {
        1: [{"number": 1, "title": "a"}, {"number": 2, "pull_request": {}}],
        2: [{"number": 3, "title": "c"}, {"number": 4, "title": "d"}],
        3: [],
    }
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
    assert fake.calls[-1][0] == ["issue", "list", "--repo", REPO, "--state", "all", "--limit", "5", "--json", "number", "--label", "bug"]


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
    assert [[c[0][2], c[0][4]] for c in fake.calls] == [
        ["PATCH", f"repos/{REPO}/issues/3"],
        ["POST", f"repos/{REPO}/issues/3/labels"],
        ["DELETE", f"repos/{REPO}/issues/3/labels/needs%20triage"],
    ]


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


@pytest.mark.parametrize(
    "rc, out, err, expected",
    [
        (0, json.dumps({"errors": [{"message": "bad"}]}), "", json.dumps([{"message": "bad"}])),
        (1, json.dumps({"errors": [{"message": "bad"}]}), "warn\n", "warn"),
        (1, "", "", "GraphQL の応答を読めない"),
        (1, "", " boom \n", "boom"),
        (0, "not json", "", "GraphQL の応答を読めない"),
        (0, "[1]", "", "GraphQL の応答を読めない"),
        (0, json.dumps({"errors": []}), "", ""),
    ],
)
def test_graphql_via_gh_api_outcomes_are_fixed(fake, rc, out, err, expected):
    """現状固定（I-009）: gh api の終了コード・errors・読めない応答から失敗の文を決める順。"""
    fake.on("api", "graphql", rc=rc, out=out, err=err)
    a = gh_graphql.graphql("q")
    assert (a.error, a.via) == (expected, "graphql")
    assert a.value is None
    assert fake.calls[0] == (["api", "graphql", "--input", "-"], json.dumps({"query": "q", "variables": {}}))


def test_graphql_via_gh_api_without_data_is_ok_with_none(fake):
    """現状固定（I-009）: `data` の無い dict の応答は成功で値は None。"""
    fake.on("api", "graphql", out="{}")
    a = gh_graphql.graphql("q", {})
    assert (a.value, a.error, a.via, a.ok) == (None, "", "graphql", True)


def test_graphql_via_githubkit_is_fixed(fake, monkeypatch):
    """現状固定（I-009）: githubkit が使えるときは gh を呼ばず、例外は型名つきの 500 字までの失敗の文になる。"""
    import gh_call

    class Client:
        def __init__(self, result):
            self.result = result
            self.calls = []

        def graphql(self, query, variables):
            self.calls.append((query, variables))
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

    ok = Client({"viewer": {"login": "me"}})
    monkeypatch.setattr(gh_call, "client", lambda: ok)
    assert gh_graphql.graphql("q") == gh_graphql.gh_quota.Attempt({"viewer": {"login": "me"}}, "", "graphql")
    assert ok.calls == [("q", {})]

    monkeypatch.setattr(gh_call, "client", lambda: Client(RuntimeError("x" * 600)))
    a = gh_graphql.graphql("q", {"n": 1})
    assert a.value is None and a.via == "graphql"
    assert a.error == ("RuntimeError: " + "x" * 600)[:500]

    monkeypatch.setattr(gh_call, "client", lambda: Client(ValueError()))
    assert gh_graphql.graphql("q").error == "ValueError: "
    assert fake.calls == []


def _edit_routes(fake, failures):
    """REST の編集の呼び出しを (メソッド, パス) で振り分ける。`failures` に載ったものだけ失敗を返す。"""
    from gh_call import GhResult

    def respond(args, stdin):
        key = (args[3], args[5]) if args[1] == "-i" else (args[2], args[4])
        if key in failures:
            return GhResult(1, "", failures[key])
        return GhResult(0, rest_out({}), "")

    fake.on_fn("api", fn=respond)
    fake.on("pr", "edit", out="ok\n")
    fake.on("issue", "edit", out="ok\n")


def _edit_calls(fake):
    out = []
    for args, stdin in fake.calls:
        if args[0] == "api":
            i = 3 if args[1] == "-i" else 2
            out.append((args[i], args[i + 2], json.loads(stdin) if stdin else None))
        else:
            out.append((args, stdin))
    return out


def test_pr_edit_by_rest_sends_fields_then_labels_is_fixed(fake):
    """現状固定（I-010）: PATCH（pulls）→ ラベルの追加 → 外す、の順。ラベルは issues のパス。"""
    _edit_routes(fake, {})
    a = gh_rest.pr_edit(REPO, "3", title="題", body="本文", add_labels=["a", "b"], remove_labels=["x y", "z"])
    assert a == gh_rest.Attempt(True, "", "rest")
    assert _edit_calls(fake) == [
        ("PATCH", f"repos/{REPO}/pulls/3", {"title": "題", "body": "本文"}),
        ("POST", f"repos/{REPO}/issues/3/labels", {"labels": ["a", "b"]}),
        ("DELETE", f"repos/{REPO}/issues/3/labels/x%20y", None),
        ("DELETE", f"repos/{REPO}/issues/3/labels/z", None),
    ]


def test_edit_with_nothing_to_change_calls_nothing_is_fixed(fake):
    """現状固定（I-010）: 変える項目が無ければ何も呼ばずに成功。空文字の title は変える項目に入る。"""
    _edit_routes(fake, {})
    assert gh_rest.issue_edit(REPO, 3) == gh_rest.Attempt(True, "", "rest")
    assert gh_rest.issue_edit(REPO, 3, add_labels=[], remove_labels=[]) == gh_rest.Attempt(True, "", "rest")
    assert fake.calls == []
    gh_rest.issue_edit(REPO, 3, title="")
    assert _edit_calls(fake) == [("PATCH", f"repos/{REPO}/issues/3", {"title": ""})]


def test_edit_rest_failures_stop_at_the_first_failure_is_fixed(fake):
    """現状固定（I-010）: 上限以外の失敗はそこで止まり、GraphQL へ代わらない。外すラベルの 404 だけは飛ばす。"""
    _edit_routes(fake, {("POST", f"repos/{REPO}/issues/3/labels"): "HTTP 422: Unprocessable"})
    a = gh_rest.issue_edit(REPO, 3, body="b", add_labels=["a"], remove_labels=["x"])
    assert not a.ok and "422" in a.error and a.via == "rest"
    assert [c[:2] for c in _edit_calls(fake)] == [("PATCH", f"repos/{REPO}/issues/3"), ("POST", f"repos/{REPO}/issues/3/labels")]

    fake.calls.clear()
    fake.routes.clear()
    _edit_routes(fake, {("DELETE", f"repos/{REPO}/issues/3/labels/x"): "HTTP 404: Not Found"})
    assert gh_rest.issue_edit(REPO, 3, remove_labels=["x", "y"]) == gh_rest.Attempt(True, "", "rest")
    assert [c[1] for c in _edit_calls(fake)] == [f"repos/{REPO}/issues/3/labels/x", f"repos/{REPO}/issues/3/labels/y"]

    fake.calls.clear()
    fake.routes.clear()
    _edit_routes(fake, {("DELETE", f"repos/{REPO}/issues/3/labels/x"): "HTTP 500: boom"})
    a = gh_rest.issue_edit(REPO, 3, remove_labels=["x", "y"])
    assert not a.ok and "500" in a.error
    assert len(fake.calls) == 1


def test_edit_falls_back_to_the_cli_with_every_field_is_fixed(fake):
    """現状固定（I-010）: 途中で上限になると、残りではなく全項目を gh <kind> edit で送り直す。本文は標準入力。"""
    _edit_routes(fake, {("DELETE", f"repos/{REPO}/issues/4/labels/x"): REST_RATE})
    a = gh_rest.issue_edit(REPO, 4, title="題", body="本文", add_labels=["a"], remove_labels=["x"])
    assert a == gh_rest.Attempt(True, "", "graphql")
    assert _edit_calls(fake)[-1] == (
        ["issue", "edit", "4", "--repo", REPO, "--title", "題", "--body-file", "-", "--add-label", "a", "--remove-label", "x"],
        "本文",
    )
    assert len(fake.calls) == 4


def test_edit_cli_failure_is_returned_is_fixed(fake):
    """現状固定（I-010）: CLI の失敗は標準エラーの文で返し、本文なしなら標準入力は渡さない。"""
    from gh_call import GhResult

    fake.on("api", rc=1, err=REST_RATE)
    fake.on_fn("pr", "edit", fn=lambda args, stdin: GhResult(1, "", " no permission \n"))
    a = gh_rest.pr_edit(REPO, 3, title="t")
    assert a == gh_rest.Attempt(None, "no permission", "graphql")
    assert fake.calls[-1] == (["pr", "edit", "3", "--repo", REPO, "--title", "t"], None)
