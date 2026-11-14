"""アダプターのテストに共通する前提。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

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


@pytest.fixture
def marker_launch(tmp_path):
    """起動されたら印のファイルを作る起動を組む関数。`marker_launch(agent)` が (印のパス, claude の argv, run_agent の Launch)。

    起動しなかったことは `assert not marker.exists()` で確かめる。
    """
    from ndf.adapters import agents

    def build(agent: str) -> tuple[Path, list[str], agents.Launch]:
        marker = tmp_path / "ran"
        argv = [sys.executable, "-c", f"open({str(marker)!r}, 'w')"]
        return marker, argv, agents.Launch(agent, tuple(argv), tmp_path, agents.AgentPaths.at(tmp_path / "run"), 30)

    return build


@pytest.fixture
def no_run_env(monkeypatch):
    """実行の記録を開いていない状態から始め、終わったら手放す。"""
    # 実行の記録の隔離と close は conftest の autouse の _fresh_run_log が持つ。ここは子へ受け継がれるかを見る親の環境変数だけを置く
    monkeypatch.setenv("PIN_PARENT", "kept")
