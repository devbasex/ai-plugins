"""アダプターのテストに共通する前提。"""

from __future__ import annotations

import time

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


@pytest.fixture(autouse=True)
def _launch_allowed(monkeypatch):
    """起動の口の前提をそろえる: 学習に使わない設定の控えが期限内にあり、重い起動の枠はテストの枠の中として取らない。

    前提そのものを確かめるテストは、控えを消すか `slots.HELD_ENV` を外して上書きする。
    """
    from ndf.adapters import training
    from ndf.testing import slots

    training.save([training.Reading(rt, False, "テストの前提") for rt in training.checked()], time.time())
    monkeypatch.setenv(slots.HELD_ENV, "テストの前提")
