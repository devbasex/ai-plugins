"""進行表の保存。同じテストを SQLite と PostgreSQL の両方で流す（受け入れ条件 9・10。P1-1）。

テストの条件: ローカル（conductor の手元・CI の Runner の SQLite）は `sqlite:///<置き場>/progress.sqlite` か
`NDF_PROGRESS_DB` の SQLite の URL で、Factory は `NDF_PROGRESS_DB` に PostgreSQL の URL を渡して開く。
fixture `progress_url` がこの 2 つの置き場にパラメータで分かれる（tests/progress/conftest.py）。
"""

from __future__ import annotations

import pytest

from ndf.core import durable
from ndf.progress import model, store

KEY = ("sprint", "git@github.com:o/r.git", "s1")


def _create(st: store.Store, key: tuple[str, str, str] = KEY) -> str:
    drafts = [model.EventDraft("created", "conductor", row, rev=1) for row in ("設計", "実装")]
    with store.transaction(st) as conn:
        return store.create_table(conn, key, None, {"stages": []}, drafts)


def _append(st: store.Store, tid: str, *drafts: model.EventDraft) -> list[int]:
    with store.transaction(st) as conn:
        events = store.read_events(conn, tid)
        return store.append_events(conn, tid, list(drafts), events[-1].seq)


def test_progress_db_holds_only_the_progress_tables(opened):
    # P1-1: 進行表の DB は DBOS の表を持たない
    assert store.table_names(opened) == ["ndf_progress_definitions", "ndf_progress_events", "ndf_progress_meta", "ndf_progress_tables"]


def test_create_read_and_refuse_a_second_table(opened):
    tid = _create(opened)
    with store.transaction(opened) as conn:
        assert store.read_table(conn, tid)["level"] == "sprint"
        assert store.read_definition(conn, tid) == (1, {"stages": []})
        assert [(e.seq, e.draft.row_key, e.draft.state) for e in store.read_events(conn, tid)] == [(1, "設計", "pending"), (2, "実装", "pending")]
    with pytest.raises(model.Refused) as info:
        _create(opened)
    assert info.value.current_id == tid


def test_append_checks_the_writer_and_keeps_history(opened):
    tid = _create(opened)
    assert _append(opened, tid, model.EventDraft("disabled", "conductor", "実装", reason="今回は外す", rev=1)) == [3]
    with pytest.raises(model.Refused):
        _append(opened, tid, model.EventDraft("enabled", "organizer", "実装", reason="戻す", rev=1))
    with store.transaction(opened) as conn:
        events = store.read_events(conn, tid)
    assert model.fold(events, 1)["実装"].state == "disabled"
    assert events[-1].draft.reason == "今回は外す" and events[-1].at.tzinfo is not None


def test_refused_append_writes_nothing(opened):
    tid = _create(opened)
    with pytest.raises(model.Refused):
        _append(
            opened,
            tid,
            model.EventDraft("disabled", "conductor", "実装", reason="外す", rev=1),
            model.EventDraft("retry", "conductor", "実装", reason="やり直す", rev=1),
        )
    with store.transaction(opened) as conn:
        assert len(store.read_events(conn, tid)) == 2


def test_stale_sequence_is_a_conflict(opened):
    tid = _create(opened)
    with store.transaction(opened) as conn, pytest.raises(store.Conflict):
        store.append_events(conn, tid, [model.EventDraft("disabled", "conductor", "実装", reason="外す", rev=1)], 1)


def test_dedupe_key_writes_the_event_once(opened):
    tid = _create(opened)
    draft = model.EventDraft("started", "workflow", "設計", dedupe_key="run-1:設計:started", rev=1)
    first = _append(opened, tid, draft)
    assert _append(opened, tid, draft) == first


def test_run_lock_admits_one_operation_per_table(opened):
    tid = _create(opened)
    with store.run_lock(opened, tid):
        with pytest.raises(model.Refused, match="動いている"), store.run_lock(opened, tid):
            pass
        with store.run_lock(opened, "sprint:other:s2"):
            pass
    with store.run_lock(opened, tid):
        pass


def test_newer_table_version_is_not_touched(progress_url, root):
    with store.open_store(progress_url, root) as st, store.transaction(st) as conn:
        conn.execute(store.VERSIONS.update().values(value=store.SCHEMA_VERSION + 1))
    with pytest.raises(durable.StoreFormatNewer):
        store.open_store(progress_url, root)
