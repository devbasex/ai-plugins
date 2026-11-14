"""URL（PostgreSQL）を渡したときの台帳と片付け（決定 83・I8・I12・I13）。

使い捨ての PostgreSQL を `initdb` と `pg_ctl` で起こす。GitHub Actions の ubuntu の Runner は PostgreSQL を
入れてある（サービスは止まっている）。手元に無ければ飛ばし、CI（環境変数 `CI`）では落とす。
"""

from __future__ import annotations

import glob
import os
import shutil
import socket
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from ndf.core import durable

from ._durable_helpers import run_key_process as _run


def _pg_bin() -> Path | None:
    found = shutil.which("pg_ctl")
    if found:
        return Path(found).parent
    candidates = sorted(glob.glob("/usr/lib/postgresql/*/bin/pg_ctl"))
    return Path(candidates[-1]).parent if candidates else None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def pg_url(tmp_path_factory):
    bindir = _pg_bin()
    # initdb は root では動かない
    missing = "PostgreSQL（pg_ctl）が無い" if bindir is None else "root では initdb を打てない" if os.geteuid() == 0 else ""
    if missing:
        if os.environ.get("CI"):
            pytest.fail(f"CI で PostgreSQL を起こせない: {missing}")
        pytest.skip(missing)
    data = tmp_path_factory.mktemp("pgdata")
    port = _free_port()
    subprocess.run([bindir / "initdb", "-D", data, "-U", "ndf", "--auth=trust", "-E", "UTF8"], check=True, capture_output=True)
    options = f"-c listen_addresses=127.0.0.1 -p {port} -k {data}"
    subprocess.run([bindir / "pg_ctl", "-D", data, "-o", options, "-l", data / "log", "-w", "start"], check=True, capture_output=True)
    try:
        yield f"postgresql://ndf@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run([bindir / "pg_ctl", "-D", data, "-m", "immediate", "stop"], check=False, capture_output=True)


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
