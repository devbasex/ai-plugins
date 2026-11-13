from __future__ import annotations

import inspect

import pytest

from ndf.contracts import causes, limits, routing


def _pick(kind, on_cause=None, on_fail=None, used=0, retry_after=None, cap=None, policy="auto"):
    p = causes.policy_of(kind) if policy == "auto" else policy
    return routing.pick(kind, on_cause or {}, on_fail, routing.RetryState(used, p, retry_after, cap))


def test_pick_on_cause_wins():
    assert _pick("test_failed", {"test_failed": "ci-fix"}, on_fail="other") == routing.Route("step", "ci-fix", 0.0, "test_failed")
    assert _pick("not_applicable", {"not_applicable": "end"}).to == "end"
    assert _pick("refused", {"refused": "stop"}).to == "stop"


def test_pick_on_cause_retry_uses_the_kind_policy():
    route = _pick("ci_pending", {"ci_pending": "retry"})
    assert route.to == "retry" and route.wait == 60


def test_pick_default_retry_within_attempts_then_moves_on():
    attempts = limits.policy("cause.infra-wait").attempts
    assert _pick("infra_wait", used=attempts - 1).to == "retry"
    assert _pick("infra_wait", used=attempts, on_fail="fix") == routing.Route("step", "fix", 0.0, "infra_wait")
    assert _pick("infra_wait", used=attempts).to == "human"


def test_pick_usage_limit_needs_retry_after():
    assert _pick("usage_limit", retry_after=120).wait == 120
    assert _pick("usage_limit", on_fail="x").to == "step"
    assert _pick("usage_limit").to == "human"


def test_pick_default_human_ignores_on_fail():
    assert _pick("unknown", on_fail="fix").to == "human"
    assert _pick("fix_limit", on_fail="fix").to == "human"


def test_pick_default_step_goes_to_on_fail_or_human():
    assert _pick("work_stopped", on_fail="judge") == routing.Route("step", "judge", 0.0, "work_stopped")
    assert _pick("work_stopped").to == "human"


def test_pick_does_not_take_text():
    params = set(inspect.signature(routing.pick).parameters)
    assert not params & {"summary", "detail", "text"}


def test_pick_refuses_negative_count():
    with pytest.raises(ValueError):
        _pick("unknown", used=-1)


def test_backoff_grows_and_is_capped():
    p = limits.Policy(name="x.y", summary="s", interval=10, factor=2, max_interval=50)
    assert [routing.backoff(p, n, None, None) for n in range(4)] == [10, 20, 40, 50]
    assert routing.backoff(p, 0, None, 5) == 5
    assert routing.backoff(p, 0, 999, 100) == 50
    flat = limits.Policy(name="x.y", summary="s", interval=10)
    assert routing.backoff(flat, 3, None, None) == 10
