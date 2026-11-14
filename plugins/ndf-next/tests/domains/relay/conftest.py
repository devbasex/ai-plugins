"""中継のテストに共通する補助。"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import Callable

import pytest


def _hold_lock(path: Path) -> int:
    """親の置き場を作って錠のファイルを開き、排他を持つ。閉じるのは呼んだ側。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


@pytest.fixture
def hold_lock() -> Callable[[Path], int]:
    """錠を持つ関数（ラッパーやリレーハブの代わりに錠を持つ）。"""
    return _hold_lock
