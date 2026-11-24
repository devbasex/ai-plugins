"""工程の文脈の形のバージョンと、出した製品のバージョン（product_version）の受け渡し。#2339 の受け入れ条件 1・5。"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

import pytest

from ndf.contracts import causes, internal
from ndf.domains.release import cli as release_cli
from ndf.domains.release import names_decide, record, record_decide
from ndf.progress import command, definition, invoke, model, run, stages, store

AT = datetime(2026, 11, 24, tzinfo=UTC)
VERSION = "1.2.4"
INITIAL = {"issues": [1], "mode": "standard", "pace": "auto", "mvv_ref": None}


def _snapshot() -> stages.Snapshot:
    return definition.load_definition("standard")


def _doc(context: dict | None = None) -> dict:
    return json.loads(json.dumps(command._new_doc(_snapshot(), INITIAL if context is None else context, {})))


def _old_doc(context: dict) -> dict:
    """製品のバージョンを version で持っていたときに保存した文書（形のバージョンを持たない）。"""
    new = _doc()
    old = {k: v for k, v in new.items() if k not in ("version", "definition_version")}
    rows = [
        {**s, **{k: ["version" if key == "product_version" else key for key in s[k]] for k in ("inputs", "outputs")}} for s in old["stages"]
    ]
    return {**old, "version": new["definition_version"], "stages": rows, "context": context}


def _recorded(seq: int, outputs: dict) -> model.Event:
    return model.Event(seq, AT, model.EventDraft(stages.OUTPUTS_EVENT, "workflow", "デプロイ", payload={"outputs": outputs}, rev=1))


def _read(monkeypatch, doc: dict, events: list[model.Event]) -> command.Current:
    monkeypatch.setattr(store, "read_machine", lambda conn, machine_id: {"id": machine_id})
    monkeypatch.setattr(store, "read_definition", lambda conn, machine_id: (1, doc))
    monkeypatch.setattr(store, "read_events", lambda conn, machine_id: events)
    return command.read_current(None, "m")


def _given_to_verification(current: command.Current) -> dict:
    """リリース後テストが受ける入力（`View.context` と `machine_context` のどちらから解いても同じ）。"""
    stage = current.snapshot.stage("リリース後テスト")
    view = run.View("m", current.rev, current.snapshot, current.raw, current.events)
    assert view.context() == command.machine_context(current)
    return invoke.resolve_inputs(stage, view.context())


def test_the_released_version_goes_from_the_record_to_the_verification_input(monkeypatch):
    made = record_decide.Record(VERSION, 50, (11,))
    monkeypatch.setattr(release_cli, "_hub", lambda args: None)
    monkeypatch.setattr(release_cli, "_strategy", lambda args: argparse.Namespace(section={"release": {}}))
    monkeypatch.setattr(names_decide, "formats_of", lambda section: None)
    monkeypatch.setattr(record, "write", lambda *a: (made, []))
    report = release_cli.cmd_record(argparse.Namespace(pr=50, production="main", production_sha=None))

    deploy = _snapshot().stage("デプロイ")
    step = next(st for st in deploy.steps if st.id == "release")
    got = invoke.finished(invoke.stage_run(deploy, step, {}), report)
    assert got.outputs == {"product_version": VERSION}
    draft, _ = stages.outputs_draft(deploy, 1, got.outputs, "workflow")
    assert draft is not None
    current = _read(monkeypatch, _doc(), [model.Event(1, AT, draft)])
    assert _given_to_verification(current) == {"product_version": VERSION}
    assert command.machine_context(current)["version"] == internal.version()


@pytest.mark.parametrize(
    ("context", "outputs"),
    [
        (INITIAL, {"version": VERSION}),
        ({**INITIAL, "version": "1.2.3"}, {"version": VERSION}),
        ({**INITIAL, "version": VERSION}, {"pr": 3}),
    ],
)
def test_a_sprint_released_before_the_rename_resumes_with_the_same_version(monkeypatch, tmp_path, context, outputs):
    current = _read(monkeypatch, _old_doc(context), [_recorded(1, outputs)])
    assert current.snapshot == _snapshot()
    assert _given_to_verification(current) == {"product_version": VERSION}
    merged = command.machine_context(current)
    assert merged["version"] == 1
    assert command.facts_of(("release_record",), merged, tmp_path) == {"release_record": True}


def test_outputs_without_a_version_keep_the_version_of_the_context(monkeypatch, tmp_path):
    current = _read(monkeypatch, _doc(), [_recorded(1, {"pr": 3}), _recorded(2, {"pr": 4})])
    assert command.machine_context(current) == {**INITIAL, "version": 1, "pr": 4}
    assert command.facts_of(("release_record",), command.machine_context(current), tmp_path) == {"release_record": False}


def test_only_the_outputs_of_the_given_revision_are_composed():
    later = model.Event(2, AT, model.EventDraft(stages.OUTPUTS_EVENT, "workflow", "デプロイ", payload={"outputs": {"pr": 9}}, rev=2))
    assert stages.recorded_outputs([_recorded(1, {"pr": 3}), later], 1) == [{"pr": 3}]


def test_a_context_of_a_newer_version_stops_both_readers(monkeypatch):
    doc = {**_doc(), "context": {**INITIAL, "version": 2}}
    current = _read(monkeypatch, doc, [])
    for read in (run.View("m", 1, current.snapshot, current.raw, []).context, lambda: command.machine_context(current)):
        with pytest.raises(internal.InternalError) as exc:
            read()
        out = command.stopped_from(exc.value)
        assert out["cause"]["kind"] == "precondition" and "プラグインを更新する" in out["summary"]
        assert causes.exit_code(out["cause"]["kind"]) == 3
    assert internal.InternalError in command.STOPPABLE


def test_a_snapshot_of_a_newer_version_is_not_read(monkeypatch):
    with pytest.raises(internal.InternalError) as exc:
        _read(monkeypatch, {**_doc(), "version": 2}, [])
    assert exc.value.kind == "precondition"


def test_a_definition_is_not_written_with_a_context_of_the_wrong_shape():
    with pytest.raises(internal.InternalError) as exc:
        command._new_doc(_snapshot(), {**INITIAL, "product_version": 3}, {})
    assert exc.value.kind == "invalid"
