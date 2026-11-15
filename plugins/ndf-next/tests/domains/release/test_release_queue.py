"""マージキューの待ち（キューへ入れた本番の PR がマージされるまでの読み直し・予算・キューの離脱・承認の失効）。"""

from __future__ import annotations

from collections.abc import Callable

from ndf.adapters.github import MERGE_QUEUE_PENDING, GitHubError, MergeOutcome, Pull
from ndf.domains.release import land

from ._fakes import HEAD, FakeHub, not_found, pull, release_lander, trail_comment


class _QueueHub(FakeHub):
    """マージをキューへ入れ、`merge_after` 回の待ちの後にマージ済みにする（`leave_after` 回の待ちの後にキューが外す）。"""

    merge_after: int = 2
    leave_after: int = 10**6
    polls: int = 0
    # キューへ入れた後に PR を読んだ回数
    reads: int = 0

    def merge(self, number: int, expect_head: str | None = None, method: str | None = None, require_method: bool = False) -> MergeOutcome:
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
    base = release_lander(hub, False, [])

    def sleep(seconds: float) -> None:
        now[0] += seconds
        hub.polls += 1
        if on_poll is not None:
            on_poll()

    return land.Lander(hub, base.production, budget, wait=base.wait, clock=lambda: now[0], sleep=sleep)


def test_merge_queue_waits_until_merged_then_syncs() -> None:
    """マージキューへ入れたら、マージされるまで待ってから戻す PR へ進む（記録とタグが走る）。"""
    hub = _QueueHub(pulls={7: pull()}, comments_of={7: [trail_comment("approved", HEAD)]})
    hub.responses[("POST", "/repos/o/r/pulls")] = GitHubError("No commits between develop and main", 422)
    now = [0.0]
    done = _queue_lander(hub, now, 600.0).land(7)
    assert done.merged and done.kind is None and now[0] == 2 * land.QUEUE_POLL_SECONDS


def test_merge_queue_stops_with_rerun_hint_when_budget_runs_out() -> None:
    """予算の内にキューがマージしなければ ci_pending で止まり、打ち直しを促す。"""
    hub = _QueueHub(pulls={7: pull()}, comments_of={7: [trail_comment("approved", HEAD)]})
    hub.merge_after = 10**6
    now = [0.0]
    done = _queue_lander(hub, now, 45.0).land(7)
    assert not done.merged and done.kind == MERGE_QUEUE_PENDING and "release-steps merge" in done.detail
    assert now[0] == 45.0
    # ループの 1 回ごとに PR を 1 回だけ読み、承認の判定にもその PR を使う（0 秒・30 秒・45 秒の 3 回）
    assert hub.reads == 3


def test_merge_queue_stops_at_once_when_the_queue_drops_the_pull() -> None:
    """キューが PR を外したら（merge_group の CI の失敗など）、予算を使い切らずに queue_removed で止まる（#3572 の決定 18）。"""
    hub = _QueueHub(pulls={7: pull()}, comments_of={7: [trail_comment("approved", HEAD)]})
    hub.merge_after, hub.leave_after = 10**6, 1
    now = [0.0]
    done = _queue_lander(hub, now, 3600.0).land(7)
    assert not done.merged and done.kind == "queue_removed" and "同じ head では入れ直さない" in done.detail
    assert now[0] == land.QUEUE_POLL_SECONDS


def test_merge_queue_dequeues_when_approval_is_revoked_while_waiting() -> None:
    """キューの待ちの間に差し戻しが足されたら、キューから外して止まる（前の承認のままマージしない）。"""
    hub = _QueueHub(pulls={7: pull()}, comments_of={7: [trail_comment("approved", HEAD)]})
    hub.merge_after = 10**6
    now = [0.0]
    done = _queue_lander(hub, now, 3600.0, on_poll=lambda: hub.comments_of[7].append(trail_comment("rejected", HEAD))).land(7)
    assert not done.merged and done.kind == "refused" and hub.dequeued == [7] and 7 not in hub.queued


def test_bump_pr_waits_in_the_merge_queue_without_reading_the_approval_trail() -> None:
    """承認数 0 の main がマージキューを使うとき、版上げの PR は承認の証跡を読まずにキューのマージを待ち、マージ済みとして返す。"""
    hub = _QueueHub(pulls={20: pull(number=20, ref="release/v1.3.0")})
    hub.responses[("GET", "/repos/o/r/branches/main/protection/required_pull_request_reviews")] = not_found()
    now = [0.0]
    done = _queue_lander(hub, now, 600.0).land_bump(20, lambda p: [])
    assert done.merged and not done.admin and done.production_sha == "c" * 40
    assert hub.dequeued == [] and now[0] == 2 * land.QUEUE_POLL_SECONDS
