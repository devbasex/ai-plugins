"""プランの形（model）: 実行の前に行き先と種類を確かめる（IS3）。"""

from __future__ import annotations

import pytest

from ndf.core.plan import model


def _raw(**step) -> dict:
    return {"name": "p", "steps": [{"id": "a", "run": ["true"], **step}, {"id": "b", "run": ["true"]}]}


def test_parse_reads_the_plan():
    plan = model.parse({**_raw(on_cause={"test_failed": "b"}, retry={"ci_pending": {"attempts": 1}}), "touches": ["src/"]})
    assert plan.order == ("a", "b")
    assert plan.touches == ("src/",)
    assert plan.limit == 2 * model.LIMIT_PER_STEP
    assert plan.step("a").retry["ci_pending"].attempts == 1
    assert plan.following("a") == "b"
    assert plan.following("b") is None


def test_a_plan_without_touches_has_none():
    assert model.parse(_raw()).touches is None


def test_digest_does_not_depend_on_key_order():
    a = model.parse({"name": "p", "steps": [{"id": "a", "run": ["true"]}]})
    b = model.parse({"steps": [{"run": ["true"], "id": "a"}], "name": "p"})
    assert a.digest() == b.digest()


@pytest.mark.parametrize(
    "step",
    [
        {"on_cause": {"test_failed": "nowhere"}},
        {"on_cause": {"no_such_kind": "b"}},
        {"on_answer": {"approved": "retry"}},
        {"on_answer": {"go": "end"}},
        {"next": "nowhere"},
        {"exit_cause": {"1": "no_such_kind"}},
        {"in": "elsewhere"},
        {"retry": {"ci_pending": {"tries": 1}}},
        {"run": []},
    ],
)
def test_parse_refuses_a_wrong_plan(step):
    with pytest.raises(model.PlanError):
        model.parse(_raw(**step))


def test_parse_refuses_duplicate_ids():
    with pytest.raises(model.PlanError):
        model.parse({"name": "p", "steps": [{"id": "a", "run": ["x"]}, {"id": "a", "run": ["x"]}]})


def test_load_reports_a_broken_file(tmp_path):
    path = tmp_path / "p.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(model.PlanError):
        model.load(path)
