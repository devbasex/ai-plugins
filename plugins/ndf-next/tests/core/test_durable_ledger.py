"""現状固定: 台帳 `ndf_runs` の読み書きと Engine の解放（_register_run・_ledger・_forget_run）。

台帳の SQL は SQLAlchemy の汎用の形なので、PostgreSQL の無い手元でも SQLite の URL で通す。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from ndf.core import durable

STARTED = datetime(2026, 11, 1, 9, 30, tzinfo=UTC)


@pytest.fixture
def disposed(monkeypatch):
    """durable が作った Engine の数と、dispose された数を数える。"""
    counts = {"created": 0, "disposed": 0}
    real = sa.create_engine

    def create_engine(*args, **kwargs):
        engine = real(*args, **kwargs)
        counts["created"] += 1
        original = engine.dispose

        def dispose(*a, **k):
            counts["disposed"] += 1
            return original(*a, **k)

        engine.dispose = dispose
        return engine

    monkeypatch.setattr(durable.sa, "create_engine", create_engine)
    return counts


def _url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'ledger.sqlite'}"


def test_ledger_is_empty_without_the_table(tmp_path, disposed):
    assert durable._ledger(_url(tmp_path)) == []
    assert disposed == {"created": 1, "disposed": 1}


def test_register_writes_a_key_once_and_forget_removes_it(tmp_path, disposed):
    url = _url(tmp_path)
    durable._register_run(url, "run-a", "key-a", STARTED)
    durable._register_run(url, "run-a", "key-a", datetime(2026, 11, 2, tzinfo=UTC))
    durable._register_run(url, "run-b", "key-b", STARTED)

    rows = durable._ledger(url)
    assert sorted((r[0], r[1]) for r in rows) == [("run-a", "key-a"), ("run-b", "key-b")]
    assert {r[2].replace(tzinfo=None) for r in rows} == {STARTED.replace(tzinfo=None)}

    durable._forget_run(url, "run-a")
    assert [r[0] for r in durable._ledger(url)] == ["run-b"]
    assert disposed["created"] == disposed["disposed"] == 6


def test_register_that_cannot_write_raises_store_broken_and_disposes(tmp_path, disposed):
    (tmp_path / "ledger.sqlite").mkdir()
    with pytest.raises(durable.StoreBroken) as err:
        durable._register_run(_url(tmp_path), "run-a", "key-a", STARTED)
    assert err.value.kind == "store_broken"
    assert str(err.value).startswith(f"台帳 {durable.RUNS_TABLE} に鍵を書けない: ")
    assert isinstance(err.value.__cause__, sa.exc.SQLAlchemyError)
    assert disposed == {"created": 1, "disposed": 1}


def test_register_with_an_unknown_scheme_raises_the_engine_error_as_is(disposed):
    """Engine を作れないときは StoreBroken に変えず、SQLAlchemy の例外のまま上げる。"""
    with pytest.raises(sa.exc.SQLAlchemyError) as err:
        durable._register_run("nosuchdialect://x/y", "run-a", "key-a", STARTED)
    assert not isinstance(err.value, durable.StoreStopped)
    assert disposed == {"created": 0, "disposed": 0}


def test_forget_and_ledger_errors_propagate_and_dispose(tmp_path, disposed):
    (tmp_path / "ledger.sqlite").mkdir()
    url = _url(tmp_path)
    with pytest.raises(sa.exc.SQLAlchemyError) as forget_err:
        durable._forget_run(url, "run-a")
    assert not isinstance(forget_err.value, durable.StoreStopped)
    with pytest.raises(sa.exc.SQLAlchemyError):
        durable._ledger(url)
    assert disposed == {"created": 2, "disposed": 2}
