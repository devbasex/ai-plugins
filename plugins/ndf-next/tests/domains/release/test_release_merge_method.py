"""マージの方式（production_merge.method）と本番ブランチの ruleset の照らし合わせ、方式ごとの本番の PR とバージョン上げの PR の入れ方（#3572 の決定 17〜20）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from ndf.adapters.github import MERGE_QUEUE_PENDING, MergeOutcome, QueueConfig, QueueEntry
from ndf.domains.release import land, merge_method, records_trail
from ndf.domains.release.declaration import GITHUB_FLOW

from ._fakes import HEAD, OTHER, FakeHub, pull, release_lander, trail_comment

MERGE_SHA = "c" * 40
CHECKS = {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "pytest"}]}}
STRICT_CHECKS = {
    "type": "required_status_checks",
    "parameters": {"strict_required_status_checks_policy": True, "required_status_checks": [{"context": "pytest"}]},
}
REVIEW_1 = {"type": "pull_request", "parameters": {"required_approving_review_count": 1}}
QUEUE = QueueConfig("merge")


def _facts(queue: bool = False, method: str | None = "merge", strict: bool | None = None, review: int = 0) -> merge_method.RulesFacts:
    return merge_method.RulesFacts(queue, method if queue else None, strict, review)


# --- 純粋な判定（rules_of・decide） ---------------------------------------------------------------------


def test_rules_of_reads_the_queue_strict_and_the_review_count() -> None:
    got = merge_method.rules_of([STRICT_CHECKS, REVIEW_1, {"type": "deletion"}], QUEUE)
    assert got == merge_method.RulesFacts(True, "merge", True, 1)
    assert merge_method.rules_of([], None) == merge_method.RulesFacts(False, None, None, 0)
    assert merge_method.rules_of([CHECKS], None).strict is False


@pytest.mark.parametrize(
    ("flow", "declared", "facts", "review", "value", "problem"),
    [
        (GITHUB_FLOW, None, _facts(), None, None, "マージの方式"),
        ("promote-whole", None, _facts(), None, None, None),
        (GITHUB_FLOW, merge_method.QUEUE, _facts(), None, None, "merge queue が無い"),
        (GITHUB_FLOW, merge_method.QUEUE, _facts(queue=True, method="squash"), None, None, "merge commit でない"),
        (GITHUB_FLOW, merge_method.STRICT, _facts(queue=True, strict=True), None, None, "strict と食い違う"),
        (GITHUB_FLOW, merge_method.STRICT, _facts(strict=False), None, None, "最新の取り込み"),
        (GITHUB_FLOW, merge_method.STRICT, _facts(strict=None), None, None, "最新の取り込み"),
        (GITHUB_FLOW, merge_method.QUEUE, _facts(queue=True, review=1), None, None, "レビューの承認"),
        (GITHUB_FLOW, merge_method.QUEUE, _facts(queue=True, review=1), "APPROVED", merge_method.QUEUE, None),
        (GITHUB_FLOW, merge_method.QUEUE, _facts(queue=True), None, merge_method.QUEUE, None),
        (GITHUB_FLOW, merge_method.STRICT, _facts(strict=True), None, merge_method.STRICT, None),
        ("promote-selected", merge_method.STRICT, _facts(strict=True), None, merge_method.STRICT, None),
    ],
)
def test_decide_follows_the_table(flow, declared, facts, review, value, problem) -> None:
    """受け入れ条件 24: 方式の無い github-flow・ruleset と食い違う方式は、既定の方式へ倒さずに problem を返す。"""
    got = merge_method.decide(flow, declared, facts, review, GITHUB_FLOW)
    assert got.value == value
    assert (got.problem is None) if problem is None else (problem in str(got.problem))
    assert got.declared == declared and got.actual


# --- 方式ごとの本番の PR の入れ方 ----------------------------------------------------------------------


@dataclass
class MethodHub(FakeHub):
    """merge queue と ruleset を持つ偽物。投入・迂回のマージ・比べを数える。"""

    queue: QueueConfig | None = None
    review: str | None = None
    behind: int = 0
    refuse_enqueue: bool = False
    enqueued: list[tuple[int, str, bool]] = field(default_factory=list)
    admin_merged: list[int] = field(default_factory=list)
    behind_reads: int = 0

    def merge_queue(self, branch: str) -> QueueConfig | None:
        return self.queue

    def review_decision(self, number: int) -> str | None:
        return self.review

    def behind_by(self, base: str, head: str) -> int:
        self.behind_reads += 1
        return self.behind

    def enqueue(self, number: int, expect_head: str, jump: bool = False) -> MergeOutcome:
        self.enqueued.append((number, expect_head, jump))
        head = self.pulls[number].head_sha
        if expect_head != head or self.refuse_enqueue:
            return MergeOutcome(False, "refused", f"#{number} をキューへ入れられない", head)
        self.queued.add(number)
        return MergeOutcome(False, MERGE_QUEUE_PENDING, f"#{number} をキューへ入れた", head)

    def queue_entry(self, number: int) -> QueueEntry:
        p = self.pulls[number]
        inside = number in self.queued
        return QueueEntry(p.merged, p.head_sha, inside, "QUEUED" if inside else None)

    def merge_as_admin(self, number: int, expect_head: str, method: str | None = None) -> MergeOutcome:
        self.admin_merged.append(number)
        return MergeOutcome(True, None, f"#{number} を迂回でマージした", self.pulls[number].head_sha)


def _merged(hub: MethodHub, number: int, head: str = HEAD) -> None:
    """キューがマージした後の PR（REST の応答も合わせる）。"""
    hub.queued.discard(number)
    hub.pulls[number] = hub.pulls[number]._replace(merged=True, merge_sha=MERGE_SHA, head_sha=head)
    hub.responses[("GET", f"/repos/o/r/pulls/{number}")] = {
        "merged": True,
        "merged_by": {"login": "octocat"},
        "merge_commit_sha": MERGE_SHA,
        "head": {"sha": head, "ref": "sprint/x"},
        "base": {"ref": "main"},
    }


def _lander(hub: MethodHub, method: str | None, rules: list[dict], on_sleep: Any = None, **kw: Any) -> land.Lander:
    base = release_lander(hub, kw.pop("failed", False), [])
    hub.responses[("GET", "/repos/o/r/rules/branches/main")] = [*hub.responses[("GET", "/repos/o/r/rules/branches/main")], *rules]
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds
        if on_sleep is not None:
            on_sleep()

    flow = kw.pop("flow", GITHUB_FLOW)
    return land.Lander(hub, "main", 3600.0, wait=base.wait, clock=lambda: now[0], sleep=sleep, flow=flow, method=method, **kw)


def _approved_hub(**kw: Any) -> MethodHub:
    return MethodHub(pulls={7: pull(ref="sprint/x")}, comments_of={7: [trail_comment("approved", HEAD)]}, **kw)


def test_queue_enqueues_the_approved_head_records_it_and_waits_for_the_merge() -> None:
    """受け入れ条件 5・22: 承認した head を渡してキューへ入れ、投入の記録を残し、マージまで待つ。head は変わらない。"""
    hub = _approved_hub(queue=QUEUE)
    done = _lander(hub, merge_method.QUEUE, [], on_sleep=lambda: _merged(hub, 7)).land(7)
    assert done.merged and done.method == merge_method.QUEUE and hub.enqueued == [(7, HEAD, False)]
    assert records_trail.queued_heads(hub, 7) == frozenset({HEAD}) and hub.merged == []
    assert done.approval is not None and done.approval.approved_head == HEAD and done.production_sha == MERGE_SHA


def test_queue_removed_stops_without_enqueueing_again_and_offers_only_reject() -> None:
    """受け入れ条件 25: キューから外れたら queue_removed で止まり、打ち直しても同じ head を入れ直さない。"""
    hub = _approved_hub(queue=QUEUE)
    first = _lander(hub, merge_method.QUEUE, [], on_sleep=lambda: hub.queued.discard(7)).land(7)
    assert not first.merged and first.kind == "queue_removed"
    again = _lander(hub, merge_method.QUEUE, []).land(7)
    assert again.kind == "queue_removed" and len(hub.enqueued) == 1


def test_queue_record_by_an_untrusted_author_does_not_stop_the_first_enqueue() -> None:
    forged = trail_comment("approved", HEAD)._replace(author="mallory", body=records_trail.render_queue_record(HEAD))
    hub = _approved_hub(queue=QUEUE)
    hub.comments_of[7].append(forged)
    _lander(hub, merge_method.QUEUE, [], on_sleep=lambda: _merged(hub, 7)).land(7)
    assert hub.enqueued == [(7, HEAD, False)]


def test_queue_does_not_use_the_bypass_even_with_the_admin_declaration() -> None:
    """受け入れ条件 26・I17: マージの方式があれば、迂回の宣言があっても迂回のマージを呼ばない。"""
    hub = _approved_hub(queue=QUEUE)
    done = _lander(hub, merge_method.QUEUE, [], on_sleep=lambda: _merged(hub, 7), admin=True).land(7)
    assert done.merged and hub.admin_merged == [] and not done.admin


def test_head_pushed_after_the_approval_is_not_enqueued() -> None:
    """受け入れ条件 6: 承認の後に head が動いていれば、キューへ入れずに refused で止まる。"""
    hub = MethodHub(pulls={7: pull(head=OTHER, ref="sprint/x")}, comments_of={7: [trail_comment("approved", HEAD)]}, queue=QUEUE)
    done = _lander(hub, merge_method.QUEUE, []).land(7)
    assert not done.merged and done.kind == "refused" and hub.enqueued == []


@pytest.mark.parametrize(
    ("method", "queue", "rules", "review"),
    [
        (None, None, [STRICT_CHECKS], None),
        (merge_method.QUEUE, None, [STRICT_CHECKS], None),
        (merge_method.STRICT, None, [CHECKS], None),
        (merge_method.STRICT, QUEUE, [STRICT_CHECKS], None),
        (merge_method.QUEUE, QUEUE, [REVIEW_1], None),
    ],
)
def test_mismatched_method_neither_merges_nor_enqueues(method, queue, rules, review) -> None:
    """受け入れ条件 24: 方式の宣言と ruleset が食い違えば precondition で止まり、理由に両方の値が出る。"""
    hub = _approved_hub(queue=queue, review=review)
    done = _lander(hub, method, rules).land(7)
    assert not done.merged and done.kind == "precondition" and hub.merged == [] and hub.enqueued == []
    assert done.mismatch is not None and "ruleset" in done.detail


def test_strict_merges_a_head_that_contains_the_tip_with_the_head_match() -> None:
    hub = _approved_hub()
    done = _lander(hub, merge_method.STRICT, [STRICT_CHECKS]).land(7)
    assert done.merged and hub.merged == [7] and hub.merge_required == [True] and done.method == merge_method.STRICT


def test_strict_stops_behind_without_merging_and_offers_only_reject() -> None:
    """受け入れ条件 23: 先端を含まない head はマージせずに behind で止まる。"""
    hub = _approved_hub(behind=2)
    done = _lander(hub, merge_method.STRICT, [STRICT_CHECKS]).land(7)
    assert not done.merged and done.kind == "behind" and hub.merged == [] and "2 コミット" in done.detail


def test_current_row_without_a_method_merges_and_opens_the_sync_pr() -> None:
    """受け入れ条件 18: github-flow でない行は方式なしで今のマージを打ち、ベースブランチへ戻す同期の PR を作る。"""
    hub = _approved_hub()
    hub.responses[("POST", "/repos/o/r/pulls")] = {"number": 8}
    hub.pulls[8] = pull(number=8, ref="main", base="develop")
    hub.responses[("GET", "/repos/o/r/rules/branches/develop")] = []
    done = _lander(hub, None, [], flow="promote-whole", base="develop").land(7)
    assert done.sync_pr == 8 and hub.merged == [7, 8] and hub.behind_reads == 0


def test_github_flow_makes_no_sync_pr() -> None:
    """I10: github-flow の行では同期の PR を作らない。"""
    hub = _approved_hub()
    done = _lander(hub, merge_method.STRICT, [STRICT_CHECKS], base="develop").land(7)
    assert done.merged and done.sync_pr is None
    assert not any((m, p) == ("POST", "/repos/o/r/pulls") for m, p, _ in hub.requests)


# --- バージョン上げの PR（land_bump） ------------------------------------------------------------------


def _bump_hub(**kw: Any) -> MethodHub:
    return MethodHub(pulls={20: pull(number=20, ref="release/v1.3.0")}, **kw)


def test_bump_pr_jumps_to_the_head_of_the_queue_without_the_bypass() -> None:
    """受け入れ条件 26: バージョン上げの PR はキューの先頭へ入れ、迂回しない。"""
    hub = _bump_hub(queue=QUEUE)
    done = _lander(hub, merge_method.QUEUE, [], on_sleep=lambda: _merged(hub, 20), admin=True).land_bump(20, lambda p: [])
    assert done.merged and hub.enqueued == [(20, HEAD, True)] and hub.admin_merged == []


def test_bump_pr_behind_the_tip_is_neither_enqueued_nor_merged() -> None:
    hub = _bump_hub(queue=QUEUE, behind=1)
    done = _lander(hub, merge_method.QUEUE, []).land_bump(20, lambda p: [])
    assert done.kind == "behind" and hub.enqueued == [] and hub.merged == []


def test_bump_pr_stops_on_the_review_rule_or_a_mismatch_and_on_paths_outside() -> None:
    hub = _bump_hub(queue=QUEUE)
    assert _lander(hub, merge_method.QUEUE, [REVIEW_1]).land_bump(20, lambda p: []).kind == "precondition"
    assert _lander(_bump_hub(), merge_method.STRICT, [CHECKS]).land_bump(20, lambda p: []).kind == "precondition"
    outside = _lander(_bump_hub(queue=QUEUE), merge_method.QUEUE, []).land_bump(20, lambda p: ["src/x.py"])
    assert outside.kind == "refused" and hub.enqueued == []


def test_bump_pr_with_strict_merges_once_with_the_head_match() -> None:
    hub = _bump_hub()
    done = _lander(hub, merge_method.STRICT, [STRICT_CHECKS], admin=True).land_bump(20, lambda p: [])
    assert done.merged and hub.merged == [20] and hub.admin_merged == [] and done.method == merge_method.STRICT
