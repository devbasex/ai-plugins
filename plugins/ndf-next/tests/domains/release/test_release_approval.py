"""承認の証跡（判定・答えの書き込みと読み戻し）と、証跡の後のマージ。"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from ndf.adapters import ci
from ndf.adapters.github import MERGE_QUEUE_PENDING, Authored, GitHubError, MergeOutcome, Pull
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


def test_present_refuses_material_for_another_commit() -> None:
    """資料が対象にしたコミットが今の先端でなければ提示しない。先端と同じなら提示して先端を返す。"""
    for shown in (OTHER, None):
        hub = FakeHub(pulls={7: pull(head=HEAD)})
        material = "# 資料\n" if shown is None else f"{approval_decide.HEAD_LINE} `{shown}`\n"
        with pytest.raises(trail.TrailError):
            trail.present(hub, 7, material)
        assert hub.comments_of == {}
    hub = FakeHub(pulls={7: pull(head=HEAD)})
    assert trail.present(hub, 7, f"{approval_decide.HEAD_LINE} `{HEAD}`\n") == HEAD
    assert approval_decide.REQUEST_MARK in hub.comments_of[7][0].body


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


def test_production_merge_rechecks_approval_after_waiting() -> None:
    """必須のチェックを待つ間に差し戻しが足されたら、マージしない（待ちの後に証跡を読み直す）。"""
    hub = FakeHub(pulls={7: pull()}, comments_of={7: [_trail("approved", HEAD)]})

    def wait(reader: Callable[[], ci.Reading], budget: float) -> ci.WatchResult:
        hub.comments_of[7].append(_trail("rejected", HEAD))
        return ci.WatchResult(_reading(False), 1, 0.0, 0.0)

    lander = _lander(hub, False, [])
    done = land.Lander(hub, lander.base, lander.production, lander.budget, wait=wait).land(7)
    assert not done.merged and done.kind == "refused" and hub.merged == []


def test_production_merge_then_sync_back_to_base() -> None:
    hub = FakeHub(pulls={7: pull(), 8: pull(number=8, base="develop")}, comments_of={7: [_trail("approved", HEAD)]})
    hub.responses[("POST", "/repos/o/r/pulls")] = {"number": 8}
    done = _lander(hub, False, []).land(7)
    assert done.merged and done.sync_pr == 8 and hub.merged == [7, 8]
    # 本番ブランチとベースブランチの祖先の関係を保つため、どちらも merge commit で入れる
    assert hub.merge_methods == [land.MERGE_METHOD, land.MERGE_METHOD] == ["merge", "merge"]
    # 本番へのマージは merge commit を必須とし、キューの方式が違えば入れない
    assert hub.merge_required == [True, True]
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


def test_issue_3487_production_merge_stops_before_merging_with_untrusted_token() -> None:
    """#3487: 書く token が信用されなければ、本番ブランチへ入れる前に止まる（マージの後の記録が必ず落ちる）。"""
    hub = FakeHub(pulls={7: pull()}, comments_of={7: [_trail("approved", HEAD)]})
    hub.trusted = set()
    done = _lander(hub, False, []).land(7)
    assert not done.merged and done.kind == "refused" and "NDF_RELEASE_TOKEN" in done.detail and hub.merged == []


class _QueueHub(FakeHub):
    """マージをキューへ入れ、`merge_after` 回の待ちの後にマージ済みにする（`leave_after` 回の待ちの後にキューが外す）。"""

    merge_after: int = 2
    leave_after: int = 10**6
    polls: int = 0
    # キューへ入れた後に PR を読んだ回数
    reads: int = 0

    def merge(self, number: int, expect_head: str | None = None, method: str | None = None) -> MergeOutcome:
        self.merged.append(number)
        self.queued.add(number)
        return MergeOutcome(False, MERGE_QUEUE_PENDING, "キューへ入れた", self.pulls[number].head_sha)

    def pull(self, number: int) -> Pull:
        got = self.pulls[number]
        self.reads += number in self.merged
        if number in self.merged and self.polls >= self.merge_after:
            self.queued.discard(number)
            return got._replace(merged=True, merge_sha="c" * 40)
        if self.polls >= self.leave_after:
            self.queued.discard(number)
        return got


def _queue_lander(hub: _QueueHub, now: list[float], budget: float, on_poll: Callable[[], None] | None = None) -> land.Lander:
    base = _lander(hub, False, [])

    def sleep(seconds: float) -> None:
        now[0] += seconds
        hub.polls += 1
        if on_poll is not None:
            on_poll()

    return land.Lander(hub, base.base, base.production, budget, wait=base.wait, clock=lambda: now[0], sleep=sleep)


def test_merge_queue_waits_until_merged_then_syncs() -> None:
    """マージキューへ入れたら、マージされるまで待ってから戻す PR へ進む（記録とタグが走る）。"""
    hub = _QueueHub(pulls={7: pull()}, comments_of={7: [_trail("approved", HEAD)]})
    hub.responses[("POST", "/repos/o/r/pulls")] = GitHubError("No commits between develop and main", 422)
    now = [0.0]
    done = _queue_lander(hub, now, 600.0).land(7)
    assert done.merged and done.kind is None and now[0] == 2 * land.QUEUE_POLL_SECONDS


def test_merge_queue_stops_with_rerun_hint_when_budget_runs_out() -> None:
    """予算の内にキューがマージしなければ ci_pending で止まり、打ち直しを促す。"""
    hub = _QueueHub(pulls={7: pull()}, comments_of={7: [_trail("approved", HEAD)]})
    hub.merge_after = 10**6
    now = [0.0]
    done = _queue_lander(hub, now, 45.0).land(7)
    assert not done.merged and done.kind == MERGE_QUEUE_PENDING and "release-steps merge" in done.detail
    assert now[0] == 45.0
    # ループの 1 回ごとに PR を 1 回だけ読み、承認の判定にもその PR を使う（0 秒・30 秒・45 秒の 3 回）
    assert hub.reads == 3


def test_one_budget_is_shared_by_the_release_pr_and_the_sync_pr() -> None:
    """待ちの予算は land のエントリポイントで 1 つに決め、戻す PR のチェックの待ちには本番の PR の待ちの残りだけを渡す。"""
    hub = _QueueHub(pulls={7: pull(), 8: pull(number=8, base="develop")}, comments_of={7: [_trail("approved", HEAD)]})
    hub.responses[("POST", "/repos/o/r/pulls")] = {"number": 8}
    now = [0.0]
    lander = _queue_lander(hub, now, 600.0)
    budgets: list[float] = []

    def wait(reader: Callable[[], ci.Reading], budget: float) -> ci.WatchResult:
        budgets.append(budget)
        return ci.WatchResult(_reading(False), 1, 0.0, 0.0)

    done = land.Lander(hub, lander.base, lander.production, 600.0, wait=wait, clock=lander.clock, sleep=lander.sleep).land(7)
    assert done.merged and done.sync_pr == 8
    assert budgets == [600.0, 600.0 - 2 * land.QUEUE_POLL_SECONDS]


def test_merge_queue_stops_at_once_when_the_queue_drops_the_pull() -> None:
    """キューが PR を外したら（merge_group の CI の失敗など）、予算を使い切らずに not_ci で止まる。"""
    hub = _QueueHub(pulls={7: pull()}, comments_of={7: [_trail("approved", HEAD)]})
    hub.merge_after, hub.leave_after = 10**6, 1
    now = [0.0]
    done = _queue_lander(hub, now, 3600.0).land(7)
    assert not done.merged and done.kind == "not_ci" and "merge_group" in done.detail
    assert now[0] == land.QUEUE_POLL_SECONDS


def test_merge_queue_dequeues_when_approval_is_revoked_while_waiting() -> None:
    """キューの待ちの間に差し戻しが足されたら、キューから外して止まる（前の承認のままマージしない）。"""
    hub = _QueueHub(pulls={7: pull()}, comments_of={7: [_trail("approved", HEAD)]})
    hub.merge_after = 10**6
    now = [0.0]
    done = _queue_lander(hub, now, 3600.0, on_poll=lambda: hub.comments_of[7].append(_trail("rejected", HEAD))).land(7)
    assert not done.merged and done.kind == "refused" and hub.dequeued == [7] and 7 not in hub.queued
