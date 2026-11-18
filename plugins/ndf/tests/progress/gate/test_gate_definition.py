"""承認ゲートの待ち方を工程の定義の `listen` の要素で書く（#3092 の決定 7・決定 77）。"""

from __future__ import annotations

import copy

from ndf import contracts
from ndf.progress import definition, invoke


def _gate_body(doc: dict) -> dict:
    return next(body for item in doc["do"] for name, body in item.items() if name == "design-gate")


def test_gates_are_listen_elements_that_wait_for_the_answer() -> None:
    gates = [x for st in definition.load_definition("standard").stages for x in st.steps if invoke.is_listen(x)]
    assert [g.gate for g in gates] == ["design", "release"]
    doc = contracts.load_json("workflow/stages/design.json")
    assert _gate_body(doc)["listen"] == {"to": {"one": {"with": {"type": definition.LISTENED_EVENT}}}}
    assert definition.check_vocabulary(doc, "stage") == []
    assert definition.check_dsl(doc) == []


def test_listen_for_another_event_is_refused() -> None:
    doc = copy.deepcopy(contracts.load_json("workflow/stages/design.json"))
    _gate_body(doc)["listen"]["to"]["one"]["with"]["type"] = "step_recorded"
    errs = definition.check_vocabulary(doc, "stage")
    assert len(errs) == 1 and "listen" in errs[0] and "step_recorded" in errs[0]
