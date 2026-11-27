"""工程の承認の回答の走査（`run.StageAnswers`）と、記録済みの値の読み方（`run.released_answer`。#4513）。

事象は偽のビューに並べ、ステートマシンも git も使わない。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ndf.contracts import worker_kinds
from ndf.progress import model, run, stages

STAGE = "デプロイ"
ROW = {"target": "本番", "kind": "manual", "trigger": "手で出す", "versioned": False}


def _answered(answer: str, *, by: str = "organizer", gate="release", kind: str = "listen") -> model.EventDraft:
    payload = {"answer": answer, "by": by, "kind": kind, **({"gate": gate} if gate else {}), "seen": {"head": "h"}}
    return model.EventDraft(run.ANSWERED, "organizer", STAGE, payload=payload)


def _outputs(version="main abc1234", delivery=ROW, store_kind="none") -> model.EventDraft:
    outputs = {"product_version": version, **({"delivery": delivery} if delivery else {})}
    return model.EventDraft(stages.OUTPUTS_EVENT, "workflow", STAGE, payload={"outputs": outputs, "release_store": store_kind})


def _other() -> model.EventDraft:
    return model.EventDraft("step_recorded", "workflow", STAGE, payload={})


class _Counted(list):
    """`reversed` でたどった事象の数を数える事象の並び。"""

    def __init__(self, drafts):
        super().__init__(model.Event(i + 1, datetime.now(UTC), d) for i, d in enumerate(drafts))
        self.seen = 0

    def __reversed__(self):
        for ev in super().__reversed__():
            self.seen += 1
            yield ev


def _view(*drafts):
    return SimpleNamespace(events=_Counted(drafts), machine_id="m")


def test_release_gate_continues_from_where_approved_stopped_and_walks_once():
    # worker の判断の求めへの承認の後に、リリース承認ゲートへの承認がある
    v = _view(_other(), _answered("approved"), _outputs(), _other(), _answered("approved", by="human:x", gate=None, kind="plan"), _other())
    answers = run.StageAnswers(v, STAGE)
    assert answers.approved().seq == 5
    assert run.released_answer(answers) == worker_kinds.Recorded("main abc1234", ROW, "none")
    answers.approved()
    answers.release_gate()
    assert v.events.seen <= len(v.events)


def test_the_latest_approval_at_the_release_gate_does_not_walk_further():
    v = _view(_other(), _other(), _answered("approved"), _outputs())
    answers = run.StageAnswers(v, STAGE)
    answers.approved()
    seen = v.events.seen
    assert answers.release_gate()[0].seq == 3 and v.events.seen == seen


def test_a_rejection_seen_on_the_way_to_an_older_approval_is_the_release_gate_answer():
    v = _view(_answered("approved"), _outputs(), _answered("rejected"))
    answers = run.StageAnswers(v, STAGE)
    assert answers.approved().seq == 1
    assert answers.release_gate()[0].seq == 3 and run.released_answer(answers) is None


@pytest.mark.parametrize(
    "drafts",
    [
        (_answered("approved", by="human:x"), _outputs()),  # 利用者の承認
        (_answered("approved", by="service:mvv"), _outputs()),  # MVV 判定の承認
        (_answered("approved"), _other()),  # 次の事象が出力でない
        (_answered("approved"), _outputs(version="  ")),
        (_answered("approved"),),
        (),
    ],
)
def test_released_answer_is_none_unless_the_organizer_approved_with_a_value(drafts):
    assert run.released_answer(run.StageAnswers(_view(*drafts), STAGE)) is None


def test_a_delivery_and_a_store_kind_that_were_not_recorded_are_none():
    v = _view(_answered("approved"), _outputs(delivery=None, store_kind=None))
    assert run.released_answer(run.StageAnswers(v, STAGE)) == worker_kinds.Recorded("main abc1234", None, None)


# 以下は I-005（識別子と届け先の確かめを `worker_kinds.released_of` と共通にする）の前に今の振る舞いを固定する


@pytest.mark.parametrize("version", ["main abc1234", " v1.2.3 ", "x"])
def test_released_answer_returns_the_version_as_it_is_without_stripping(version):
    v = _view(_answered("approved"), _outputs(version=version))
    assert run.released_answer(run.StageAnswers(v, STAGE)).version == version


@pytest.mark.parametrize("version", [None, 123, ["v"], {"v": 1}, "", "\n\t"])
def test_released_answer_is_none_unless_the_version_is_a_non_blank_string(version):
    v = _view(_answered("approved"), _outputs(version=version))
    assert run.released_answer(run.StageAnswers(v, STAGE)) is None


@pytest.mark.parametrize("outputs", [None, "main abc1234", ["main abc1234"], {}])
def test_released_answer_is_none_when_the_outputs_are_not_an_object_with_a_version(outputs):
    after = model.EventDraft(stages.OUTPUTS_EVENT, "workflow", STAGE, payload={"outputs": outputs, "release_store": "none"})
    assert run.released_answer(run.StageAnswers(_view(_answered("approved"), after), STAGE)) is None


@pytest.mark.parametrize("delivery", ["本番", ["本番"], 1, True])
def test_a_delivery_that_is_not_an_object_is_none_and_keeps_the_version(delivery):
    v = _view(_answered("approved"), _outputs(delivery=delivery))
    assert run.released_answer(run.StageAnswers(v, STAGE)) == worker_kinds.Recorded("main abc1234", None, "none")


def test_an_empty_delivery_object_is_kept_as_an_empty_object():
    after = model.EventDraft(
        stages.OUTPUTS_EVENT, "workflow", STAGE, payload={"outputs": {"product_version": "v", "delivery": {}}, "release_store": "none"}
    )
    assert run.released_answer(run.StageAnswers(_view(_answered("approved"), after), STAGE)) == worker_kinds.Recorded("v", {}, "none")


def test_the_delivery_is_a_copy_of_the_recorded_object():
    delivery = dict(ROW)
    got = run.released_answer(run.StageAnswers(_view(_answered("approved"), _outputs(delivery=delivery)), STAGE))
    assert got.delivery == ROW and got.delivery is not delivery
    got.delivery["target"] = "変えた"
    assert delivery["target"] == "本番"


@pytest.mark.parametrize("store_kind", ["none", "versioned", "unknown", "誰も知らない種類"])
def test_a_string_store_kind_is_returned_as_it_is(store_kind):
    # 今は 3 つの種類に絞らない（`released_of` は絞る）
    v = _view(_answered("approved"), _outputs(store_kind=store_kind))
    assert run.released_answer(run.StageAnswers(v, STAGE)).store == store_kind


@pytest.mark.parametrize("store_kind", [1, ["none"], {"kind": "none"}, True])
def test_a_store_kind_that_is_not_a_string_is_none(store_kind):
    v = _view(_answered("approved"), _outputs(store_kind=store_kind))
    assert run.released_answer(run.StageAnswers(v, STAGE)) == worker_kinds.Recorded("main abc1234", ROW, None)
