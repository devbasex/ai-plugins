"""アダプターのテストに共通する前提。"""

from __future__ import annotations

import pytest

from ndf.adapters import cli


@pytest.fixture(autouse=True)
def _fresh_run_log(monkeypatch):
    """実行の記録を開いていない状態から始め、終わったら手放す。"""
    for name in (cli.ENV_RUN_ID, cli.ENV_RUN_LOG, cli.ENV_RUN_LOG_SINK):
        monkeypatch.delenv(name, raising=False)
    cli.close()
    yield
    cli.close()
