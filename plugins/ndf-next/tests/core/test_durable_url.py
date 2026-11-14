"""URL（PostgreSQL）を渡したときの台帳と片付け（決定 83・I8・I12・I13）。

使い捨ての PostgreSQL を `initdb` と `pg_ctl` で起こす。GitHub Actions の ubuntu の Runner は PostgreSQL を
入れてある（サービスは止まっている）。手元に無ければ飛ばし、CI（環境変数 `CI`）では落とす。
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from ndf.core import durable
from ndf.core import durable_locks as locks

from ._durable_helpers import postgres_url
from ._durable_helpers import run_key_process as _run


@pytest.fixture(scope="module")
def pg_url(tmp_path_factory, start_postgres):
    with postgres_url(tmp_path_factory, start_postgres) as url:
        yield url


@durable.workflow
def url_done_flow(value: str) -> str:
    return value


@contextmanager
def _engine(url: str) -> Iterator[sa.Engine]:
    engine = sa.create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1), poolclass=sa.NullPool)
    try:
        yield engine
    finally:
        engine.dispose()


def _ledger(url: str) -> set[str]:
    with _engine(url) as engine, engine.connect() as conn:
        return {row[0] for row in conn.execute(sa.text(f"SELECT run_name FROM {durable.RUNS_TABLE}"))}


def _ids(url: str, key: str) -> set[str]:
    return {r.workflow_id for r in durable.workflows(url, key)}


def _later(days: float) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


def _sweep(url: str, root: Path, days: float) -> durable.SweepResult:
    """走査の目印を消してから、`days` 日後の時刻で片付ける（目印の 24 時間に依らず走査させる）。"""
    (root / "dbos" / durable.SWEPT_MARK).unlink(missing_ok=True)
    return durable.sweep_url(root, url, _later(days))


def test_launch_writes_the_key_to_the_ledger_and_sweep_deletes_it_after_keep_days(pg_url, root):
    with durable.session(root, "url-done", pg_url):
        durable.start(url_done_flow, "wf-url-done", "x")
    name = durable.run_name("url-done")
    assert name in _ledger(pg_url)
    assert _ids(pg_url, "url-done") == {"wf-url-done"}

    assert _sweep(pg_url, root, durable.KEEP_DAYS - 1).deleted == []
    assert _ids(pg_url, "url-done") == {"wf-url-done"}

    result = _sweep(pg_url, root, durable.KEEP_DAYS + 1)
    assert "wf-url-done" in result.deleted
    assert _ids(pg_url, "url-done") == set()
    assert name not in _ledger(pg_url)


def test_key_with_a_pending_child_is_kept_whole(pg_url, root):
    """I8: 親だけが終わり子が PENDING のままの鍵は、親も子も消さない（delete_children に頼らない）。"""
    _run(root, "url-child", "child-wait", pg_url)
    assert _ids(pg_url, "url-child") == {"wf-url-child", "wf-url-child-child"}

    result = _sweep(pg_url, root, durable.KEEP_DAYS + 1)
    assert not {"wf-url-child", "wf-url-child-child"} & set(result.deleted)
    assert _ids(pg_url, "url-child") == {"wf-url-child", "wf-url-child-child"}
    assert durable.run_name("url-child") in _ledger(pg_url)
    assert "wf-url-child-child" in {r.workflow_id for r in durable.pending(pg_url)}


def test_sweep_leaves_runs_of_applications_not_in_the_ledger(pg_url, root):
    """I12: 台帳に無い `run-` で始まる `application_name` の実行は、終わって日数が過ぎても消さない。"""
    with durable.session(root, "url-other", pg_url):
        durable.start(url_done_flow, "wf-url-other", "x")
    with _engine(pg_url) as engine, engine.begin() as conn:
        conn.execute(sa.text(f"DELETE FROM {durable.RUNS_TABLE} WHERE run_name = :n"), {"n": durable.run_name("url-other")})

    _sweep(pg_url, root, durable.KEEP_DAYS + 1)
    assert _ids(pg_url, "url-other") == {"wf-url-other"}


def test_sweep_leaves_a_key_whose_lock_is_held(pg_url, root):
    """I13: 同じ鍵を打ち直す起動が DB 側の鍵のロックを持つ間は、その鍵の実行も台帳の行も消さない。"""
    with durable.session(root, "url-held", pg_url):
        durable.start(url_done_flow, "wf-url-held", "x")

    with durable.run_lock(root, "url-held", pg_url) as held:
        assert held is not None
        result = _sweep(pg_url, root, durable.KEEP_DAYS + 1)
        assert result.busy >= 1
        assert _ids(pg_url, "url-held") == {"wf-url-held"}
        assert durable.run_name("url-held") in _ledger(pg_url)

    _sweep(pg_url, root, durable.KEEP_DAYS + 1)
    assert _ids(pg_url, "url-held") == set()


def test_run_lock_without_wait_yields_none_while_another_connection_holds_it(pg_url, root):
    with durable.run_lock(root, "url-lock", pg_url) as first:
        assert first is not None
        with durable.run_lock(root, "url-lock", pg_url, wait=False) as second:
            assert second is None
    with durable.run_lock(root, "url-lock", pg_url, wait=False) as again:
        assert again is not None


def test_url_sweep_runs_once_a_day(pg_url, root):
    root.mkdir(parents=True)
    assert not durable.sweep_url(root, pg_url, datetime.now(UTC)).skipped
    assert durable.sweep_url(root, pg_url, datetime.now(UTC) + timedelta(hours=1)).skipped
    with durable.maint_lock(root, wait=True):
        assert durable.sweep_url(root, pg_url, datetime.now(UTC) + timedelta(hours=25)).skipped


def test_waiting_run_lock_times_out_with_the_owner(pg_url, root, monkeypatch):
    """待つ鍵のロックは上限で止まり、持ち主（接続の application_name のホストと pid）を添える（B11）。"""
    monkeypatch.setattr(locks, "LOCK_WAIT_SECONDS", 0.3)
    with durable.run_lock(root, "busy", pg_url):
        with pytest.raises(durable.StoreInUse) as err, durable.run_lock(root, "busy", pg_url):
            pass
    assert f"ndf-lock {socket.gethostname()} {os.getpid()}" in str(err.value)


def test_ndf_connections_carry_keepalive_and_idle_session_timeout(pg_url):
    with locks.open_engine(pg_url) as engine, engine.connect() as conn:
        idle = conn.execute(sa.text("SHOW idle_session_timeout")).scalar()
        keepalive = conn.connection.dbapi_connection.info.get_parameters()
    assert idle == f"{locks.PG_IDLE_SESSION_MS // 60000}min"
    assert keepalive["keepalives_idle"] == str(locks.PG_KEEPALIVE["keepalives_idle"])
