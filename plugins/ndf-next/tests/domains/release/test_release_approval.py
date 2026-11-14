"""承認の証跡（判定・答えの書き込みと読み戻し）と、証跡の後のマージ。"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from ndf.adapters import ci
from ndf.adapters.github import Authored, GitHubError
from ndf.domains.release import approval_decide, land, trail

from ._fakes import HEAD, OTHER, FakeHub, pull


def _trail(answer: str, sha: str, author: str = "octocat") -> Authored:
    return Authored(author, "OWNER", approval_decide.render_trail(answer, sha, author))


def test_judge_uses_latest_trusted_answer_on_head() -> None:
    comments = [
        ("octocat", approval_decide.render_trail("approved", HEAD, "x"), True),
        ("octocat", approval_decide.render_trail("rejected", HEAD, "x"), True),
        ("mallory", approval_decide.render_trail("approved", HEAD, "x"), False),
        ("octocat", approval_decide.render_request("資料", HEAD), True),
    ]
    got = approval_decide.judge(approval_decide.trails(comments), HEAD)
    assert got.state == approval_decide.REJECTED and got.trail is not None and got.trail.order == 1


def test_judge_reports_stale_and_missing() -> None:
    stale = approval_decide.judge(approval_decide.trails([("octocat", approval_decide.render_trail("approved", OTHER, "x"), True)]), HEAD)
    assert stale.state == approval_decide.STALE and not stale.approved
    assert approval_decide.judge([], HEAD).state == approval_decide.MISSING
    with pytest.raises(ValueError):
        approval_decide.render_trail("maybe", HEAD, "x")


def test_known_bug_answer_after_reapproval_reaches_the_record() -> None:
    """リリース 8 の事例: 先端が動いた後の承認し直しの答えが、今の先端への承認として判定に届く。"""
    hub = FakeHub(pulls={7: pull(head=HEAD)}, comments_of={7: [_trail("approved", OTHER)]})
    assert trail.read(hub, 7).judgment.state == approval_decide.STALE
    got = trail.answer(hub, 7, "approved", "人", presented=HEAD)
    assert got.approved and trail.read(hub, 7).judgment.approved


def test_answer_is_refused_when_it_does_not_reach_the_record() -> None:
    """書いた答えが読み直しで見えなければ、答えたつもりで進めずに止める。"""
    hub = FakeHub(pulls={7: pull(head=HEAD)}, drop_comments=True)
    with pytest.raises(trail.TrailError, match="届いていない"):
        trail.answer(hub, 7, "approved", "人", presented=HEAD)


def test_answer_is_refused_when_head_moved_since_presented() -> None:
    hub = FakeHub(pulls={7: pull(head=HEAD)})
    with pytest.raises(trail.TrailError, match="動いた"):
        trail.answer(hub, 7, "approved", "人", presented=OTHER)
    assert hub.comments(7) == []


def test_check_posts_status_on_head() -> None:
    hub = FakeHub(pulls={7: pull(head=HEAD)}, comments_of={7: [_trail("approved", HEAD)]})
    trail.check(hub, 7)
    method, path, payload = hub.requests[-1]
    assert (method, path) == ("POST", f"/repos/o/r/statuses/{HEAD}")
    assert payload["state"] == "success" and payload["context"] == approval_decide.STATUS_CONTEXT


def _reading(failed: bool) -> ci.Reading:
    bad = [ci.Check("pytest", "done", "failure", 1)] if failed else []
    return ci.Reading(bad, [], [], [], [], [], True)


def _wait(failed: bool, calls: list[int]) -> Callable[[Callable[[], ci.Reading], float], ci.WatchResult]:
    def wait(reader: Callable[[], ci.Reading], budget: float) -> ci.WatchResult:
        calls.append(1)
        return ci.WatchResult(_reading(failed), 1, 0.0, 0.0)

    return wait


def _lander(hub: FakeHub, failed: bool, calls: list[int]) -> land.Lander:
    hub.responses[("GET", "/repos/o/r/rules/branches/main")] = [
        {
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": "pytest"}, {"context": approval_decide.STATUS_CONTEXT}]},
        }
    ]
    hub.responses[("GET", "/repos/o/r/rules/branches/develop")] = []
    return land.Lander(hub, "develop", "main", 60.0, wait=_wait(failed, calls))


def test_production_merge_needs_approval() -> None:
    hub = FakeHub(pulls={7: pull()})
    done = _lander(hub, False, []).land(7)
    assert not done.merged and done.kind == "refused" and hub.merged == []


def test_issue_1983_failed_ci_stops_without_retry() -> None:
    """#1983: 必須のチェックが落ちたら、打ち直さずに 1 回で止めて人へ戻す（マージしない）。"""
    calls: list[int] = []
    hub = FakeHub(pulls={7: pull()}, comments_of={7: [_trail("approved", HEAD)]})
    done = _lander(hub, True, calls).land(7)
    assert not done.merged and done.kind == "test_failed" and calls == [1] and hub.merged == []


def test_production_merge_then_sync_back_to_base() -> None:
    hub = FakeHub(pulls={7: pull(), 8: pull(number=8, base="develop")}, comments_of={7: [_trail("approved", HEAD)]})
    hub.responses[("POST", "/repos/o/r/pulls")] = {"number": 8}
    done = _lander(hub, False, []).land(7)
    assert done.merged and done.sync_pr == 8 and hub.merged == [7, 8]
    assert land.Lander(hub, "develop", "main", 1.0).required("main") == ["pytest"]


def test_required_includes_classic_branch_protection() -> None:
    """ruleset に加えて従来の保護（branch protection）の必須のチェックも待つ。"""
    hub = FakeHub()
    hub.responses[("GET", "/repos/o/r/branches/main")] = {
        "protection": {"required_status_checks": {"contexts": ["lint"], "checks": [{"context": "build"}]}}
    }
    assert land.Lander(hub, "develop", "main", 1.0).required("main") == ["build", "lint"]


def test_commit_status_counts_as_required_check() -> None:
    """必須のチェックが外部の CI の commit status でも、通っていれば missing にしない。"""
    readers: list[Callable[[], ci.Reading]] = []

    def wait(reader: Callable[[], ci.Reading], budget: float) -> ci.WatchResult:
        readers.append(reader)
        return ci.WatchResult(reader(), 1, 0.0, 0.0)

    hub = FakeHub(pulls={8: pull(number=8, base="develop")})
    hub.responses[("GET", "/repos/o/r/branches/develop")] = {"protection": {"required_status_checks": {"contexts": ["ext-ci"]}}}
    hub.responses[("GET", f"/repos/o/r/commits/{HEAD}/status")] = {
        "statuses": [{"context": "ext-ci", "state": "success", "updated_at": "t1", "created_at": "t0", "id": 3}]
    }
    done = land.Lander(hub, "develop", "main", 1.0, wait=wait).land(8)
    reading = readers[0]()
    assert reading.missing == [] and reading.running == [] and done.merged and hub.merged == [8]


def test_sync_pr_uses_production_branch_as_head() -> None:
    """戻す PR は本番ブランチを head にして作る（リリースのブランチはマージの後に消えることがある）。"""
    hub = FakeHub(pulls={7: pull(), 8: pull(number=8, base="develop")}, comments_of={7: [_trail("approved", HEAD)]})
    hub.responses[("POST", "/repos/o/r/pulls")] = {"number": 8}
    _lander(hub, False, []).land(7)
    made = [payload for method, path, payload in hub.requests if (method, path) == ("POST", "/repos/o/r/pulls")]
    assert [(m["head"], m["base"]) for m in made] == [("main", "develop")]


def test_merged_release_resumes_sync_with_open_pr() -> None:
    """本番 PR がマージ済みでも、開いている戻す PR を使い直してマージを再開する。"""
    hub = FakeHub(pulls={7: pull(merged=True), 9: pull(number=9, base="develop")})
    hub.responses[("GET", "/repos/o/r/pulls")] = [{"number": 9}]
    done = _lander(hub, False, []).land(7)
    assert done.merged and done.sync_pr == 9 and hub.merged == [9]
    assert not any((m, p) == ("POST", "/repos/o/r/pulls") for m, p, _ in hub.requests)


def test_sync_without_diff_is_done_but_other_422_raises() -> None:
    """差分が無い 422 だけを「戻す差分なし」とし、ほかの 422 は隠さない。"""
    hub = FakeHub(pulls={7: pull(merged=True)})
    hub.responses[("POST", "/repos/o/r/pulls")] = GitHubError("No commits between develop and main", 422)
    done = _lander(hub, False, []).land(7)
    assert done.merged and done.sync_pr is None and hub.merged == []
    hub.responses[("POST", "/repos/o/r/pulls")] = GitHubError("Validation Failed: head invalid", 422)
    with pytest.raises(GitHubError):
        _lander(hub, False, []).land(7)
