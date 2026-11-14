"""アダプターのテストに共通する前提。"""

from __future__ import annotations

import pytest

from ndf.adapters import run_log


@pytest.fixture(autouse=True)
def _fresh_run_log(monkeypatch):
    """実行の記録を開いていない状態から始め、終わったら手放す。"""
    for name in (run_log.ENV_RUN_ID, run_log.ENV_RUN_LOG, run_log.ENV_RUN_LOG_SINK):
        monkeypatch.delenv(name, raising=False)
    run_log.close()
    yield
    run_log.close()
