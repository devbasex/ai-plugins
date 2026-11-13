from __future__ import annotations

import inspect

import pytest

from ndf.contracts import causes, limits


def _edit_cause(kind: str, **changes):
    def fn(data):
        data["causes"] = [{**c, **changes} if c["kind"] == kind else c for c in data["causes"]]
        return data

    return fn


def test_plugin_table_rows_have_type_and_stopped_exit_code():
    table = causes.table()
    assert len(table) == 24
    codes = causes.stopped_codes()
    assert all(row.exit in codes and row.std_type for row in table.values())


@pytest.mark.parametrize(
    ("kind", "code"), [("base_conflict", 4), ("not_applicable", 3), ("precondition", 3), ("deps_missing", 69), ("infra_wait", 75)]
)
def test_exit_code(kind, code):
    assert causes.exit_code(kind) == code


def test_unknown_kind_stops():
    with pytest.raises(causes.CausesError):
        causes.exit_code("no_such_kind")


def test_policy_of_reads_limits_only_for_retry_kinds():
    assert causes.policy_of("ci_pending") == limits.policy("cause.ci-pending")
    assert causes.policy_of("unknown") is None


@pytest.mark.parametrize(
    "fn",
    [
        _edit_cause("unknown", std_type="fatal"),
        _edit_cause("unknown", exit=7),
        _edit_cause("unknown", exit=10),
        _edit_cause("unknown", route="later"),
        _edit_cause("ci_pending", retry="cause.no-such"),
        _edit_cause("ci_pending", retry=None),
        _edit_cause("unknown", retry="cause.ci-pending"),
        lambda d: {**d, "causes": d["causes"] + [d["causes"][0]]},
        lambda d: {**d, "version": 2},
    ],
)
def test_wrong_table_stops(contracts_copy, fn):
    contracts_copy("causes.json", fn)
    with pytest.raises(causes.CausesError):
        causes.table()


def test_retry_policy_without_interval_needs_retry_after(contracts_copy):
    contracts_copy("causes.json", _edit_cause("usage_limit", requires_retry_after=False))
    with pytest.raises(causes.CausesError, match="interval"):
        causes.table()


def test_exit_code_range_ends_are_accepted(contracts_copy):
    contracts_copy("causes.json", _edit_cause("unknown", exit=69))
    assert causes.exit_code("unknown") == 69


def test_cause_builds_dict():
    assert causes.cause("test_failed", "pytest が落ちた") == {"kind": "test_failed", "detail": "pytest が落ちた"}
    full = causes.cause("usage_limit", "上限", instance="claude", retry_after=30)
    assert full == {"kind": "usage_limit", "detail": "上限", "instance": "claude", "retry_after": 30}


@pytest.mark.parametrize(("kind", "detail"), [("no_such", "x"), ("unknown", " "), ("unknown", "")])
def test_cause_refuses_unknown_kind_and_empty_detail(kind, detail):
    with pytest.raises(ValueError):
        causes.cause(kind, detail)


@pytest.mark.parametrize(
    ("value", "count"),
    [
        ({"kind": "unknown", "detail": "x"}, 0),
        ({"kind": "unknown", "detail": "x", "instance": "s"}, 0),
        ({"kind": "nope", "detail": "x"}, 1),
        ({"kind": "unknown", "detail": ""}, 1),
        ({"kind": "unknown", "detail": "x", "retry_after": -1}, 1),
        ({"kind": "unknown", "detail": "x", "retry_after": True}, 1),
        ({"kind": "unknown", "detail": "x", "extra": 1}, 1),
        ("unknown", 1),
    ],
)
def test_errors(value, count):
    assert len(causes.errors(value)) == count


def _pick(kind, on_cause=None, on_fail=None, used=0, retry_after=None, cap=None, policy="auto"):
    p = causes.policy_of(kind) if policy == "auto" else policy
    return causes.pick(kind, on_cause or {}, on_fail, used, p, retry_after, cap)


def test_pick_on_cause_wins():
    assert _pick("test_failed", {"test_failed": "ci-fix"}, on_fail="other") == causes.Route("step", "ci-fix", 0.0, "test_failed")
    assert _pick("not_applicable", {"not_applicable": "end"}).to == "end"
    assert _pick("refused", {"refused": "stop"}).to == "stop"


def test_pick_on_cause_retry_uses_the_kind_policy():
    route = _pick("ci_pending", {"ci_pending": "retry"})
    assert route.to == "retry" and route.wait == 60


def test_pick_default_retry_within_attempts_then_moves_on():
    attempts = limits.policy("cause.infra-wait").attempts
    assert _pick("infra_wait", used=attempts - 1).to == "retry"
    assert _pick("infra_wait", used=attempts, on_fail="fix") == causes.Route("step", "fix", 0.0, "infra_wait")
    assert _pick("infra_wait", used=attempts).to == "human"


def test_pick_usage_limit_needs_retry_after():
    assert _pick("usage_limit", retry_after=120).wait == 120
    assert _pick("usage_limit", on_fail="x").to == "step"
    assert _pick("usage_limit").to == "human"


def test_pick_default_human_ignores_on_fail():
    assert _pick("unknown", on_fail="fix").to == "human"
    assert _pick("fix_limit", on_fail="fix").to == "human"


def test_pick_default_step_goes_to_on_fail_or_human():
    assert _pick("work_stopped", on_fail="judge") == causes.Route("step", "judge", 0.0, "work_stopped")
    assert _pick("work_stopped").to == "human"


def test_pick_does_not_take_text():
    params = set(inspect.signature(causes.pick).parameters)
    assert not params & {"summary", "detail", "text"}


def test_pick_refuses_negative_count():
    with pytest.raises(ValueError):
        _pick("unknown", used=-1)


def test_backoff_grows_and_is_capped():
    p = limits.Policy(name="x.y", summary="s", interval=10, factor=2, max_interval=50)
    assert [causes.backoff(p, n, None, None) for n in range(4)] == [10, 20, 40, 50]
    assert causes.backoff(p, 0, None, 5) == 5
    assert causes.backoff(p, 0, 999, 100) == 50
    flat = limits.Policy(name="x.y", summary="s", interval=10)
    assert causes.backoff(flat, 3, None, None) == 10
