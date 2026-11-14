"""進行表の型と純粋な判断（受け入れ条件 2・3・5・10）。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ndf.progress import definition, model, stages

AT = datetime(2026, 11, 14, tzinfo=UTC)


def _events(*drafts: model.EventDraft) -> list[model.Event]:
    out = []
    for i, d in enumerate(drafts, 1):
        out.append(model.Event(i, AT, model.EventDraft(**{**vars(d), "state": model.resulting_state("sprint", d)})))
    return out


def _d(kind: str, actor: str, row: str | None = "実装", reason: str | None = None, state: str | None = None) -> model.EventDraft:
    return model.EventDraft(kind, actor, row, state=state, reason=reason, rev=1)


def test_table_id_is_stable_and_separates_repositories():
    assert model.table_id("sprint", "git@x:a/b.git", "s1") == model.table_id("sprint", "git@x:a/b.git", "s1")
    assert model.table_id("sprint", "git@x:a/b.git", "s1") != model.table_id("sprint", "git@x:a/c.git", "s1")
    assert model.table_id("sprint", "r", "s1").startswith("sprint:") and model.table_id("sprint", "r", "s1").endswith(":s1")


def test_fold_keeps_the_latest_state_per_row_and_skips_stateless_events():
    events = _events(
        _d("created", "conductor"),
        _d("started", "workflow"),
        _d("outputs_recorded", "workflow"),
        _d("finished", "workflow"),
    )
    rows = model.fold(events, 1)
    assert rows["実装"].state == "finished" and rows["実装"].seq == 4
    assert model.fold(events, 2) == {}


def test_six_row_states_follow_the_transitions():
    rows: dict[str, model.RowState] = {}
    for seq, draft in enumerate(
        [_d("created", "conductor"), _d("started", "workflow"), _d("failed", "workflow"), _d("retry", "conductor", reason="直した")], 1
    ):
        model.check_event("sprint", rows, "running", draft)
        rows = model.apply("sprint", rows, draft, seq)
    assert rows["実装"].state == "pending"
    assert set(model.table()["row_states"]["sprint"]) == {"pending", "running", "finished", "failed", "disabled", "cancelled"}


def test_running_row_cannot_be_disabled():
    rows = {"実装": model.RowState("running", 2, AT)}
    with pytest.raises(model.Refused, match="実行中"):
        model.check_event("sprint", rows, "running", _d("disabled", "conductor", reason="止める"))


def test_operations_need_a_reason():
    rows = {"実装": model.RowState("pending", 1, AT)}
    for kind in ("disabled", "manual_finished", "cancelled"):
        with pytest.raises(model.Refused, match="理由が要る"):
            model.check_event("sprint", rows, "running", _d(kind, "conductor", reason="  "))


def test_only_the_table_owner_writes_sprint_states():
    # 受け入れ条件 10: オーガナイザーと耐久ワークフローはスプリントの進行表の状態を書けない（I6）
    rows = {"実装": model.RowState("pending", 1, AT)}
    for actor in ("organizer", "durable"):
        with pytest.raises(model.Refused, match="書けない"):
            model.check_event("sprint", rows, "running", _d("disabled", actor, reason="止める"))
    with pytest.raises(model.Refused, match="書けない"):
        model.check_event("sprint", rows, "running", _d("started", "conductor"))
    model.check_event("sprint", {"実装": model.RowState("running", 2, AT)}, "running", _d("answered", "organizer"))


def test_finished_or_cancelled_table_takes_no_more_events():
    events = _events(_d("created", "conductor"), _d("cancelled", "conductor", row=None, reason="やめる"))
    whole = model.table_state("sprint", events)
    assert whole == "cancelled"
    with pytest.raises(model.Refused, match="中止"):
        model.check_event("sprint", model.fold(events, 1), whole, _d("enabled", "conductor", reason="戻す"))


def test_sprint_events_need_a_revision_and_the_right_scope():
    with pytest.raises(model.Refused, match="改訂"):
        model.check_event("sprint", {}, "running", model.EventDraft("created", "conductor", "実装"))
    with pytest.raises(model.Refused, match="全体の事象"):
        model.check_event("sprint", {}, "running", _d("completed", "workflow"))
    with pytest.raises(model.Refused, match="行の鍵"):
        model.check_event("sprint", {}, "running", _d("started", "workflow", row=None))
    with pytest.raises(model.Refused, match="無い"):
        model.check_event("sprint", {}, "running", _d("no_such", "conductor"))


def test_carried_takes_its_state_from_the_draft():
    with pytest.raises(model.Refused, match="状態は"):
        model.check_event("sprint", {}, "running", _d("carried", "conductor", state="running"))
    model.check_event("sprint", {}, "running", _d("carried", "conductor", state="finished"))


def test_lane_rows_have_their_own_states():
    rows = {"1": model.RowState("running", 3, AT)}
    model.check_event("lane", rows, None, model.EventDraft("row_ended", "workflow", "1", state="completed"))
    with pytest.raises(model.Refused):
        model.check_event("lane", rows, None, model.EventDraft("row_ended", "workflow", "1", state="finished"))
    assert model.table_state("lane", []) is None


def test_enablement_priority_is_sprint_then_project_then_definition():
    snap = definition.load_definition("standard")
    facts = dict.fromkeys(
        [
            "code_in_diff",
            "docs_only_diff",
            "behavior_docs_in_diff",
            "ci_declared",
            "prod_branch",
            "release_record",
            "retro_enabled",
            "design_pr",
        ],
        True,
    )
    facts["code_in_diff"] = False
    declared = {"リファクタリング": {"enabled": True, "reason": ""}, "振り返り": {"enabled": False, "reason": "プロジェクトで止めている"}}
    got = stages.resolve_enabled(snap, facts, declared, {"振り返り": "このスプリントでは止める"})
    assert got["振り返り"] == stages.Enablement(False, "このスプリントでは止める", "sprint")
    assert got["リファクタリング"] == stages.Enablement(True, None, "project")
    assert stages.resolve_enabled(snap, facts, {}, {})["リファクタリング"] == stages.Enablement(False, "code_in_diff", "definition")
    assert stages.resolve_enabled(snap, None, {}, {})["リファクタリング"].enabled


def test_refactoring_suspension_is_a_declaration():
    # 受け入れ条件 3: リファクタリングの停止は .ndf/project.json の宣言で表す
    snap = definition.load_definition("standard")
    declared = {"リファクタリング": {"enabled": False, "reason": "#1142 の作り直しの間は止める"}}
    assert stages.resolve_enabled(snap, None, declared, {})["リファクタリング"] == stages.Enablement(
        False, "#1142 の作り直しの間は止める", "project"
    )


def test_progress_table_contract_is_consistent():
    table = model.table()
    assert set(table["gates"]) == {"design", "release"}
    assert model.branch_prefix("sprint") == "sprint/"
    assert model.label("disabled") == "無効"
