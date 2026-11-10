"""PR の照合（`gh_parts.py pr-status`、#2909 の F4）。

`gh` は定義元の `gh_call.RUNNER` を見本の応答へ差し替えて呼ぶ。GitHub へは届かない。判定の表（`gh_pr_status.py`）の
6 行それぞれで、次の 1 手・終了コード・`next` が表どおりになるかを見る。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import gh_call  # noqa: E402
import gh_pr_status  # noqa: E402
import step_result  # noqa: E402

REPO = "o/r"
PR = 812
SHA = "abc123"


def _rest_out(body):
    return f"HTTP/2.0 200 OK\nX-Ratelimit-Remaining: 4999\r\n\r\n{json.dumps(body)}"


def _pr(state="OPEN"):
    return json.dumps(
        {
            "number": PR,
            "title": "t",
            "body": "",
            "state": state,
            "isDraft": False,
            "author": {"login": "a"},
            "headRefName": "feat/x",
            "headRefOid": SHA,
            "baseRefName": "develop",
            "url": "u",
            "additions": 1,
            "deletions": 0,
            "changedFiles": 1,
            "labels": [],
            "isCrossRepository": False,
        }
    )


def _run(name, conclusion, status="completed", run_id=1):
    return {"id": run_id, "name": name, "status": status, "conclusion": conclusion, "started_at": "2026-11-10T00:00:00Z"}


def _graphql(merge="CLEAN", unresolved=0, issues=((659, "OPEN"),)):
    nodes = [{"isResolved": False}] * unresolved + [{"isResolved": True}]
    pr = {
        "mergeStateStatus": merge,
        "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": nodes},
        "closingIssuesReferences": {"nodes": [{"number": n, "state": s} for n, s in issues]},
    }
    return json.dumps({"data": {"repository": {"pullRequest": pr}}})


class Fake:
    def __init__(self, state="OPEN", runs=(), merge="CLEAN", unresolved=0, checks_fail=False, graphql_fail=False):
        self.calls: list[list[str]] = []
        self.state, self.runs, self.merge, self.unresolved = state, list(runs), merge, unresolved
        self.checks_fail, self.graphql_fail = checks_fail, graphql_fail

    def __call__(self, args, stdin=None, cwd=None):
        self.calls.append(list(args))
        joined = " ".join(args)
        if args[:2] == ["pr", "view"]:
            return gh_call.GhResult(0, _pr(self.state), "")
        if "check-runs" in joined:
            if self.checks_fail:
                return gh_call.GhResult(1, "", "HTTP 500")
            return gh_call.GhResult(0, _rest_out({"total_count": len(self.runs), "check_runs": self.runs}), "")
        if args[:2] == ["api", "graphql"]:
            if self.graphql_fail:
                return gh_call.GhResult(1, "", "GraphQL: Something went wrong")
            return gh_call.GhResult(0, _graphql(self.merge, self.unresolved), "")
        if f"repos/{REPO}/issues/" in joined:
            return gh_call.GhResult(0, _rest_out({"state": "closed"}), "")
        pytest.fail(f"想定外の gh の呼び出し: {args}")


@pytest.fixture()
def use(monkeypatch):
    def install(fake: Fake) -> Fake:
        monkeypatch.setattr(gh_call, "RUNNER", fake)
        return fake

    return install


@pytest.mark.parametrize(
    "fake,action,code,next_has",
    [
        (Fake(state="MERGED", runs=[_run("pytest", "success")]), "escalate", 10, "PR は merged"),
        (Fake(runs=[_run("pytest", "success")], merge="DIRTY"), "escalate", 10, "衝突"),
        (Fake(checks_fail=True), "escalate", 10, "チェックを照会できない"),
        (Fake(runs=[_run("pytest", "failure"), _run("lint", None, status="in_progress", run_id=2)]), "fix", 20, "失敗: pytest"),
        (Fake(runs=[_run("pytest", "success")], unresolved=2), "fix", 20, "未解決のスレッド 2 件"),
        (Fake(runs=[_run("pytest", None, status="in_progress")]), "wait", 21, "実行中: pytest"),
        (Fake(runs=[]), "wait", 21, "チェックがまだ 1 件も無い"),
        (Fake(runs=[_run("pytest", "success"), _run("docs", "skipped", run_id=2)]), "merge", 0, "merge-when-green 812"),
    ],
)
def test_decision_follows_the_table(use, fake, action, code, next_has):
    use(fake)
    out, got = gh_pr_status.pr_status(PR, REPO)
    assert step_result.validate_result(out, got) == []
    assert (out["metrics"]["action"], got) == (action, code)
    assert next_has in out["next"]
    assert out["status"] == ("ok" if action == "merge" else "gate")


def test_threads_unavailable_escalates_with_gaps(use):
    use(Fake(runs=[_run("pytest", "success")], graphql_fail=True))
    out, code = gh_pr_status.pr_status(PR, REPO)
    assert (out["metrics"]["action"], code) == ("escalate", 10)
    assert out["metrics"]["unresolved_threads"] is None
    assert [g["name"] for g in out["gaps"]] == ["threads"]


def test_issues_are_reported_but_do_not_decide(use):
    fake = use(Fake(runs=[_run("pytest", "success")]))
    out, code = gh_pr_status.pr_status(PR, REPO, [700])
    assert code == 0
    assert [i for i in out["items"] if i["kind"] == "issue"] == [
        {"kind": "issue", "name": "#659", "result": "open"},
        {"kind": "issue", "name": "#700", "result": "closed"},
    ]
    assert (out["metrics"]["issues_open"], out["metrics"]["issues_closed"]) == (1, 1)
    # メタ 1 回・チェック 1 回・GraphQL 1 回・足した Issue 1 回（別々に打つときの view・checks・スレッド・Issue ごとより多くない）
    assert len(fake.calls) == 4


def test_failed_metrics_count_folded_runs(use):
    use(Fake(runs=[_run("pytest", "failure", run_id=1), _run("pytest", "success", run_id=2)]))
    out, code = gh_pr_status.pr_status(PR, REPO)
    # 再実行で成功した同名のチェックは最新の 1 件に畳む
    assert (out["metrics"]["failed_checks"], out["metrics"]["action"], code) == (0, "merge", 0)
