from __future__ import annotations

import os
from pathlib import Path

import pytest

from ndf.testing.isolation import forbid_real_state  # noqa: F401  （fixture として受ける）


@pytest.fixture(autouse=True)
def _isolated(forbid_real_state):  # noqa: F811
    return forbid_real_state


@pytest.fixture
def src_env(forbid_real_state) -> dict[str, str]:  # noqa: F811
    """子プロセスへ src を読ませる環境（今の環境に PYTHONPATH=<プラグインの根>/src を足した辞書）。"""
    return {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
