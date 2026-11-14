from __future__ import annotations

import inspect

import pytest

from ndf.contracts import causes, limits, routing


def test_pick_on_cause_wins():
    pick = routing.pick
    assert pick("test_failed", on_cause={"test_failed": "ci-fix"}, on_fail="other") == routing.Route("step", "ci-fix", 0.0, "test_failed")
    assert pick("not_applicable", on_cause={"not_applicable": "end"}).to == "end"
    assert pick("refused", on_cause={"refused": "stop"}).to == "stop"


def test_pick_on_cause_retry_uses_the_kind_policy():
    route = routing.pick("ci_pending", on_cause={"ci_pending": "retry"})
    assert route.to == "retry" and route.wait == 60


def test_pick_on_cause_retry_without_policy_goes_to_on_fail_or_conductor():
    assert routing.pick("unknown", on_cause={"unknown": "retry"}, on_fail="fix") == routing.Route("step", "fix", 0.0, "unknown")
    assert routing.pick("unknown", on_cause={"unknown": "retry"}).to == "conductor"


def test_pick_default_retry_within_attempts_then_moves_on():
    attempts = limits.policy("cause.infra-wait").attempts
    assert routing.pick("infra_wait", retry=routing.RetryState(used=attempts - 1)).to == "retry"
    assert routing.pick("infra_wait", on_fail="fix", retry=routing.RetryState(used=attempts)) == routing.Route(
        "step", "fix", 0.0, "infra_wait"
    )
    assert routing.pick("infra_wait", retry=routing.RetryState(used=attempts)).to == "conductor"


def test_pick_prefers_the_policy_the_caller_resolved_over_the_kind_default():
    # 宣言（プロジェクトの causes・ステップの retry）の上書きは既定の方針より優先する
    declared = limits.Policy(name="cause.infra-wait", summary="s", attempts=1, interval=5)
    assert limits.policy("cause.infra-wait").attempts > 1
    assert routing.pick("infra_wait", retry=routing.RetryState(used=0, policy=declared)) == routing.Route("retry", None, 5.0, "infra_wait")
    assert routing.pick("infra_wait", retry=routing.RetryState(used=1, policy=declared)).to == "conductor"


def test_pick_transient_retries_with_backoff_and_is_not_a_human_stop():
    # 一時的な失敗は人を待たずに打ち直す（#3346）
    policy = limits.policy("cause.transient")
    waits = [routing.pick("transient", retry=routing.RetryState(used=n)).wait for n in range(policy.attempts)]
    assert waits == [routing.backoff(policy, n, None, None) for n in range(policy.attempts)]
    assert waits == sorted(waits) and waits[0] < waits[-1]
    assert routing.pick("transient", retry=routing.RetryState(used=policy.attempts)).to == "conductor"


def test_pick_usage_limit_without_retry_after_goes_to_human_as_the_table_says():
    # 表は「retry_after が無ければ人へ戻す」。on_fail があっても進めない（#3394）
    assert routing.pick("usage_limit", retry=routing.RetryState(retry_after=120)).wait == 120
    assert routing.pick("usage_limit", retry=routing.RetryState(retry_after=0)) == routing.Route("retry", None, 0.0, "usage_limit")
    assert routing.pick("usage_limit", on_fail="x") == routing.Route("human", None, 0.0, "usage_limit")
    assert routing.pick("usage_limit").to == "human"
    used_up = limits.policy("cause.usage-limit").attempts
    assert routing.pick("usage_limit", on_fail="x", retry=routing.RetryState(used=used_up, retry_after=5)).to == "human"


@pytest.mark.parametrize("kind", ["fix_limit", "needs_human", "precondition", "not_ci"])
def test_pick_default_human_ignores_on_fail(kind):
    assert routing.pick(kind, on_fail="fix") == routing.Route("human", None, 0.0, kind)


@pytest.mark.parametrize("kind", ["unknown", "wait_timeout", "invalid", "incomplete"])
def test_pick_default_conductor_ignores_on_fail(kind):
    # conductor（会話のセッションの AI）が次の手を選ぶ種類は、人へ問わない（#3365）
    assert routing.pick(kind, on_fail="fix") == routing.Route("conductor", None, 0.0, kind)


def test_pick_default_step_goes_to_on_fail_or_conductor():
    assert routing.pick("work_stopped", on_fail="judge") == routing.Route("step", "judge", 0.0, "work_stopped")
    assert routing.pick("work_stopped").to == "conductor"


def test_every_destination_word_of_the_table_is_a_route():
    words = {routing.RETRY, routing.STEP, routing.CONDUCTOR, routing.HUMAN}
    rows = causes.table().values()
    assert {r.route for r in rows} <= words
    assert {r.exhausted for r in rows if r.exhausted} <= words - {routing.RETRY}


def test_pick_does_not_take_text():
    params = set(inspect.signature(routing.pick).parameters)
    assert not params & {"summary", "detail", "text"}


def test_pick_refuses_negative_count():
    with pytest.raises(ValueError):
        routing.pick("unknown", retry=routing.RetryState(used=-1))


def test_backoff_grows_and_is_capped():
    p = limits.Policy(name="x.y", summary="s", interval=10, factor=2, max_interval=50)
    assert [routing.backoff(p, n, None, None) for n in range(4)] == [10, 20, 40, 50]
    assert routing.backoff(p, 0, None, 5) == 5
    assert routing.backoff(p, 0, 999, 100) == 50
    flat = limits.Policy(name="x.y", summary="s", interval=10)
    assert routing.backoff(flat, 3, None, None) == 10


# 現状固定（I-017）: 打ち直しに retry_after が要るかは表の requires_retry_after、打ち直さないときの行き先は表の exhausted から決まる。
def test_pick_reads_requires_retry_after_and_exhausted_from_the_table(contracts_copy):
    assert routing.pick("infra_wait") == routing.Route("retry", None, 600.0, "infra_wait")
    contracts_copy(
        "causes.json",
        lambda d: {**d, "causes": [{**c, "requires_retry_after": True} if c["kind"] == "infra_wait" else c for c in d["causes"]]},
    )
    assert routing.pick("infra_wait") == routing.Route("conductor", None, 0.0, "infra_wait")
    assert routing.pick("infra_wait", on_fail="f") == routing.Route("step", "f", 0.0, "infra_wait")
    assert routing.pick("infra_wait", retry=routing.RetryState(retry_after=7.0)) == routing.Route("retry", None, 7.0, "infra_wait")
    contracts_copy(
        "causes.json",
        lambda d: {**d, "causes": [{**c, "exhausted": "human"} if c["kind"] == "infra_wait" else c for c in d["causes"]]},
    )
    assert routing.pick("infra_wait", on_fail="f") == routing.Route("human", None, 0.0, "infra_wait")
