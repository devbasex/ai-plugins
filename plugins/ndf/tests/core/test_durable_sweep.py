"""キーごとのファイルの検査・退避・片付けと、待ちの一覧（SQLite。docs/specifications/ndf-rebuild.md の決定 82・83・86・I6・I8）。"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ndf.core import durable


KEY_SUFFIXES = (".sqlite", ".sqlite-wal", ".sqlite-shm", ".sqlite-journal", ".lock", ".checked")


@durable.workflow
def sweep_done_flow(value: str) -> str:
    return value


def _later(hours: float = 0, days: float = 0) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours, days=days)


def _key_files(root: Path, key: str) -> list[str]:
    name = durable.run_name(key)
    return sorted(p.name for p in (root / "dbos").glob(f"{name}*") if p.is_file())


def _finish_without_retire(root: Path, key: str) -> None:
    """耐久ワークフローを流し終え、退避の前に抜けた（移す前にプロセスが落ちた）キーのファイルを作る。"""
    durable.prepare_store(root)
    with durable.run_lock(root, key):
        durable.launch(durable.run_url(root, key), key)
        try:
            durable.start(sweep_done_flow, f"wf-{key}", key)
        finally:
            durable.shutdown()


def test_session_retires_a_finished_key_with_all_its_files(root):
    with durable.session(root, "done"):
        assert durable.start(sweep_done_flow, "wf-done", "x") == "x"

    name = durable.run_name("done")
    assert _key_files(root, "done") == []
    assert (root / "dbos" / "retired" / f"{name}.sqlite").is_file()


def test_sweep_skips_a_key_whose_lock_is_held_and_retires_it_later(root):
    """I6: 過去の実行が終わったキーでも、`run_lock` を持つプロセスがいる間は移さない。"""
    _finish_without_retire(root, "reopened")
    with durable.run_lock(root, "reopened"):
        held = durable.sweep(root, _later(hours=25))
        assert held.busy == 1 and held.retired == []
        assert f"{durable.run_name('reopened')}.sqlite" in _key_files(root, "reopened")

    later = durable.sweep(root, _later(hours=50))
    assert [p.name for p in later.retired] == [f"{durable.run_name('reopened')}.sqlite"]
    assert _key_files(root, "reopened") == []


def test_sweep_deletes_retired_and_broken_files_after_keep_days(root):
    with durable.session(root, "aged"):
        durable.start(sweep_done_flow, "wf-aged", "x")
    retired = root / "dbos" / "retired" / f"{durable.run_name('aged')}.sqlite"
    broken = root / "dbos" / "broken" / "run-x-20261101T000000.sqlite"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"broken")

    # 退避の時刻から数える（ファイルの元の更新時刻ではない）
    kept = durable.sweep(root, _later(days=durable.keep_days() - 1))
    assert kept.deleted == [] and retired.exists() and broken.exists()

    gone = durable.sweep(root, _later(days=durable.keep_days() + 1))
    assert {str(retired), str(broken)} <= set(gone.deleted)
    assert not retired.exists() and not broken.exists()


def test_sweep_runs_once_a_day(root):
    root.mkdir(parents=True)
    mark = root / "dbos" / ".swept"
    mark.parent.mkdir()
    now = datetime.now(UTC)
    an_hour_ago = (now - timedelta(hours=1)).timestamp()
    mark.touch()
    os.utime(mark, (an_hour_ago, an_hour_ago))

    assert durable.sweep(root, now).skipped
    assert mark.stat().st_mtime == pytest.approx(an_hour_ago)

    stale = (now - timedelta(hours=25)).timestamp()
    os.utime(mark, (stale, stale))
    assert not durable.sweep(root, now).skipped
    assert mark.stat().st_mtime == pytest.approx(now.timestamp())


def test_sweep_is_skipped_while_another_process_sweeps(root):
    root.mkdir(parents=True)
    with durable.maint_lock(root, wait=True):
        assert durable.sweep(root, _later(hours=25)).skipped
    assert not (root / "dbos" / ".swept").exists()


def test_broken_key_file_is_moved_to_broken_and_stops(root):
    durable.prepare_store(root)
    body = Path(durable.run_url(root, "bad").removeprefix("sqlite:///"))
    body.parent.mkdir(parents=True)
    body.write_bytes(b"this is not a database" * 100)
    body.with_name(body.name + "-wal").write_bytes(b"wal")

    with pytest.raises(durable.StoreBroken) as err, durable.session(root, "bad"):
        pass
    assert err.value.kind == "store_broken"
    moved = err.value.paths[0]
    assert moved.parent == root / "dbos" / "broken"
    assert moved.name.startswith(durable.run_name("bad") + "-")
    assert moved.with_name(moved.name + "-wal").read_bytes() == b"wal"
    assert _key_files(root, "bad") == []


# --- 現状固定: キーごとのファイルの走査（_sweep_live） --------------------------------------


def test_sweep_retires_finished_keys_in_name_order_and_reports_unreadable_files(root):
    """現状固定: 読めないキーのファイルは warnings に載せて残し、ほかの終わったキーは名前の順に移す。"""
    _finish_without_retire(root, "b-done")
    _finish_without_retire(root, "a-done")
    bad = root / "dbos" / "run-unreadable.sqlite"
    bad.write_bytes(b"this is not a database" * 100)

    result = durable.sweep(root, _later(hours=25))

    assert not result.skipped and result.busy == 0 and result.deleted == []
    names = sorted([durable.run_name("a-done"), durable.run_name("b-done")])
    assert [p.name for p in result.retired] == [f"{n}.sqlite" for n in names]
    assert all(p.parent == root / "dbos" / "retired" for p in result.retired)
    assert len(result.warnings) == 1 and result.warnings[0].startswith(f"{bad}: ")
    assert bad.read_bytes() == b"this is not a database" * 100
    # 読めないファイルのキーは、走査が取った `.lock` が残る
    assert (root / "dbos" / "run-unreadable.lock").exists()
    assert _key_files(root, "a-done") == [] and _key_files(root, "b-done") == []


def test_sweep_keeps_a_lock_without_a_key_file_while_it_is_held(root):
    """現状固定: 本体の無い `.lock` でも、`run_lock` を持つプロセスがいる間は消さず、busy にも数えない。"""
    root.mkdir(parents=True)
    with durable.run_lock(root, "holding"):
        lock = root / "dbos" / f"{durable.run_name('holding')}.lock"
        assert lock.exists()
        result = durable.sweep(root, _later(hours=25))
        assert result.busy == 0 and result.retired == [] and result.warnings == []
        assert lock.exists()


# --- 現状固定: 記録の読み出し（_records） ---------------------------------------------------


def _fields(record: durable.RunRecord) -> tuple:
    return (record.run_name, record.workflow_id, record.status, record.parent_workflow_id, record.app_version)


def test_workflows_reads_records_of_the_key_with_their_fields(root):
    """現状固定: `workflows` はキーの名前・状態・親・アプリのバージョンと、UTC の更新時刻を持つ記録を返す。"""
    _finish_without_retire(root, "fields")
    url = durable.run_url(root, "fields")
    name = durable.run_name("fields")
    version = durable.app_version("fields")

    every = durable.workflows(url)
    by_key = durable.workflows(url, "fields")

    assert [_fields(r) for r in every] == [(name, "wf-fields", "SUCCESS", None, version)]
    assert [_fields(r) for r in by_key] == [_fields(r) for r in every]
    assert all(r.updated_at is not None and r.updated_at.tzinfo == UTC for r in every)
    assert durable.workflows(url, "another") == []


class _FailingClient:
    """DBOSClient の代わりに、操作で落ち、destroy の回数を数える。"""

    destroyed = 0

    def __init__(self, system_database_url: str, retry_connection_errors: bool = True, serializer: object = None) -> None:
        self.url = system_database_url

    def _fail(self, *args, **kwargs):
        raise RuntimeError(f"failed: {self.url}")

    list_workflows = cancel_workflow = delete_workflows = _fail

    def destroy(self) -> None:
        type(self).destroyed += 1


def test_client_errors_propagate_and_the_client_is_destroyed(monkeypatch):
    """現状固定: DBOSClient の操作が落ちても destroy してから、例外をそのまま上げる。"""
    monkeypatch.setattr(durable, "DBOSClient", _FailingClient)
    monkeypatch.setattr(_FailingClient, "destroyed", 0)

    with pytest.raises(RuntimeError, match="failed: sqlite:///x"):
        durable.workflows("sqlite:///x")
    with pytest.raises(RuntimeError, match="failed: sqlite:///y"):
        durable.cancel_before_launch("sqlite:///y", "wf")
    assert _FailingClient.destroyed == 2


def test_check_file():
    assert durable.check_file(Path("/nonexistent/x.sqlite"))


def test_check_file_reads_quick_check(tmp_path):
    good = tmp_path / "good.sqlite"
    conn = sqlite3.connect(good)
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    assert durable.check_file(good)
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"garbage" * 1000)
    assert not durable.check_file(bad)


def test_key_file_is_checked_once_a_day(root, monkeypatch):
    calls: list[Path] = []
    real = durable.check_file
    monkeypatch.setattr(durable, "check_file", lambda path: calls.append(path) or real(path))

    _finish_without_retire(root, "checked")
    _finish_without_retire(root, "checked")
    assert len(calls) == 1
    assert (root / "dbos" / f"{durable.run_name('checked')}.checked").exists()
