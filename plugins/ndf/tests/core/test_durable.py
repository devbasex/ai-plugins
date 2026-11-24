"""DBOS の殻を通した判断のループ（耐久を通す代表）と、キーの名前・アプリのバージョン
（docs/specifications/ndf-rebuild.md の決定 81・84）。
"""

from __future__ import annotations

import pytest

from ndf.core import durable, loop

from ._counting_loader import _load_counting

performed: list[int] = []

decide = _load_counting().decide


@durable.step
def perform(act: int) -> int:
    performed.append(act)
    return act * 10


@durable.workflow
def flow(start: list[int]) -> list[int]:
    assert durable.current_id() is not None
    return loop.run(decide, perform, start)


@pytest.fixture
def launched(tmp_path):
    root = tmp_path / "state"
    performed.clear()
    with durable.session(root, "test-key"):
        yield root
    durable.release_store(root)


def test_run_name_is_a_valid_dbos_app_name_and_stable():
    name = durable.run_name("plan-7/run-1")
    assert name == durable.run_name("plan-7/run-1") != durable.run_name("plan-7/run-2")
    assert name.startswith(durable.RUN_PREFIX)
    assert 3 <= len(name) <= 256
    assert all(c.islower() or c.isdigit() or c in "-_" for c in name)


def test_run_url_points_at_the_key_file(tmp_path):
    assert durable.run_url(tmp_path, "k") == f"sqlite:///{tmp_path / 'dbos' / (durable.run_name('k') + '.sqlite')}"


def test_workflow_runs_loop_and_rerun_returns_recorded_result(launched):
    assert durable.current_id() is None
    assert durable.start(flow, "wf-1", []) == [10, 20, 30]
    assert performed == [1, 2, 3]
    assert durable.status("wf-1") == "SUCCESS"
    assert durable.status("wf-missing") is None

    # 同じ ID は流し直さず、記録した結果を返す
    assert durable.start(flow, "wf-1", []) == [10, 20, 30]
    assert durable.result("wf-1") == [10, 20, 30]
    assert performed == [1, 2, 3]


def test_launch_names_executor_and_application_after_the_key(tmp_path):
    root = tmp_path / "state"
    with durable.session(root, "named-key"):
        durable.start(flow, "wf-named", [])
    durable.release_store(root)

    name = durable.run_name("named-key")
    retired = root / "dbos" / "retired" / f"{name}.sqlite"
    records = durable.workflows(f"sqlite:///{retired}")
    assert [(r.run_name, r.workflow_id, r.status) for r in records] == [(name, "wf-named", "SUCCESS")]
