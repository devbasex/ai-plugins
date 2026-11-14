"""URL（PostgreSQL）のときに NDF が起こした実行の鍵を書く台帳 `ndf_runs`（決定 83・I12・I13）。DBOS は import しない。

台帳は進行表と同じ DB に置き、DBOS の表ではない。DBOS の殻（`ndf.core.durable`）が起動の前に鍵を書き、片付けで読んで消す。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa

from ndf.core import durable_locks as locks
from ndf.core.durable_locks import StoreBroken

# URL のとき、NDF が起こした鍵を書く台帳の表。進行表と同じ DB に置き、DBOS の表ではない（I12）
RUNS_TABLE = "ndf_runs"


@dataclass(frozen=True)
class LedgerRow:
    """台帳 `ndf_runs` の 1 行。鍵の名前・実行の鍵・起こした時刻。"""

    run_name: str
    run_key: str
    started_at: datetime


def _runs_table(metadata: sa.MetaData) -> sa.Table:
    return sa.Table(
        RUNS_TABLE,
        metadata,
        sa.Column("run_name", sa.Text, primary_key=True),
        sa.Column("run_key", sa.Text, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    )


def _ensure_runs_table(engine: sa.Engine, table: sa.Table) -> None:
    """台帳の表を作る。別の鍵の起動が同時に作ると片方が重複で落ちるため、落ちても表があれば続ける。"""
    try:
        with engine.begin() as conn:
            table.create(conn, checkfirst=True)
    except sa.exc.DBAPIError:
        with engine.connect() as conn:
            if not sa.inspect(conn).has_table(RUNS_TABLE):
                raise


def _register_run(database_url: str, name: str, run_key: str, now: datetime) -> None:
    """台帳 `ndf_runs` へ鍵を 1 行書く（既にあれば書かない）。書けなければ `StoreBroken`。"""
    with locks.open_engine(database_url) as engine:
        table = _runs_table(sa.MetaData())
        try:
            _ensure_runs_table(engine, table)
            with engine.begin() as conn:
                _insert_run(conn, table, name, run_key, now)
        except sa.exc.SQLAlchemyError as exc:
            raise StoreBroken(f"台帳 {RUNS_TABLE} に鍵を書けない: {exc}") from exc


def _insert_run(conn: sa.Connection, table: sa.Table, name: str, run_key: str, now: datetime) -> None:
    """台帳に鍵 `name` の行が無ければ足す。"""
    if conn.execute(sa.select(table.c.run_name).where(table.c.run_name == name)).first() is None:
        conn.execute(table.insert().values(run_name=name, run_key=run_key, started_at=now))


def _ledger(database_url: str) -> list[LedgerRow]:
    """台帳の行（鍵の名前・実行の鍵・起こした時刻）。表が無ければ空。"""
    table = _runs_table(sa.MetaData())
    with locks.open_engine(database_url) as engine, engine.connect() as conn:
        if not sa.inspect(conn).has_table(RUNS_TABLE):
            return []
        return [LedgerRow(*row) for row in conn.execute(sa.select(table))]


def _forget_run(database_url: str, name: str) -> None:
    table = _runs_table(sa.MetaData())
    with locks.open_engine(database_url) as engine, engine.begin() as conn:
        conn.execute(table.delete().where(table.c.run_name == name))
