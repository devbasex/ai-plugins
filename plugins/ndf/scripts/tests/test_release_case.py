"""リリースの場合の判定（#2773 の受け入れ条件 17〜19）。

- CD の行が無ければネットワークを使わずに `no-cd`
- CD の行があり承認数 0 なら `cd-no-review`、1 以上なら `cd-review`、読めなければ `cd-no-review`
- ruleset に規則が無ければ従来のブランチ保護を読み、保護が無ければ 0
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import delivery  # noqa: E402
import gh_call  # noqa: E402
import release_case as rc  # noqa: E402


def pr_rule(n):
    return {"type": "pull_request", "parameters": {"required_approving_review_count": n}}


def test_required_approvals():
    assert rc.required_approvals([{"type": "required_status_checks"}]) is None
    assert rc.required_approvals([pr_rule(0)]) == 0
    assert rc.required_approvals([pr_rule(1), pr_rule(2)]) == 2


@pytest.mark.parametrize(
    "has_cd, approvals, case",
    [(False, 1, rc.NO_CD), (True, 1, rc.CD_REVIEW), (True, 0, rc.CD_NO_REVIEW), (True, None, rc.CD_NO_REVIEW)],
)
def test_decide(has_cd, approvals, case):
    got = rc.decide(has_cd, approvals, "")
    assert got.case == case and got.reason


def fake(table):
    calls = []

    def run(args, stdin=None, cwd=None):
        calls.append(" ".join(args))
        for key, (code, out) in table.items():
            if key in " ".join(args):
                return gh_call.GhResult(code, out if code == 0 else "", "" if code == 0 else out)
        return gh_call.GhResult(1, "", "HTTP 403")

    return run, calls


def test_read_approvals_falls_back_to_branch_protection(monkeypatch):
    run, _ = fake({"rules/branches/main": (0, json.dumps([pr_rule(1)]))})
    monkeypatch.setattr(gh_call, "RUNNER", run)
    assert rc.read_approvals(".", "main") == (1, "")
    run, _ = fake({"rules/branches/main": (0, "[]"), "protection/required_pull_request_reviews": (1, "gh: Not Found (HTTP 404)")})
    monkeypatch.setattr(gh_call, "RUNNER", run)
    assert rc.read_approvals(".", "main") == (0, "")
    run, _ = fake({"rules/branches/main": (0, "[]"), "protection/required_pull_request_reviews": (0, json.dumps({"required_approving_review_count": 2}))})
    monkeypatch.setattr(gh_call, "RUNNER", run)
    assert rc.read_approvals(".", "main") == (2, "")
    run, _ = fake({})
    monkeypatch.setattr(gh_call, "RUNNER", run)
    n, why = rc.read_approvals(".", "main")
    assert n is None and "読めない" in why


def _decl(rows):
    return delivery.DeliveryDecl(base="develop", production="main", rows=rows)


AUTO = {"kind": "auto", "branch": "main", "versioned": True, "publishes_release": True}


def test_case_of_without_cd_does_not_call_gh(monkeypatch):
    monkeypatch.setattr(delivery, "load_delivery", lambda root: _decl([]))
    run, calls = fake({})
    monkeypatch.setattr(gh_call, "RUNNER", run)
    assert rc.case_of(".").case == rc.NO_CD and calls == []


def test_case_of_ai_plugins_shape_is_cd_no_review(monkeypatch):
    monkeypatch.setattr(delivery, "load_delivery", lambda root: _decl([AUTO]))
    run, _ = fake({"rules/branches/main": (0, json.dumps([pr_rule(0)]))})
    monkeypatch.setattr(gh_call, "RUNNER", run)
    assert rc.case_of(".").case == rc.CD_NO_REVIEW


def test_case_of_raises_on_broken_decl(monkeypatch):
    monkeypatch.setattr(delivery, "load_delivery", lambda root: delivery.DeliveryDecl(problems=["壊れた"]))
    with pytest.raises(rc.CaseUnreadable):
        rc.case_of(".")
