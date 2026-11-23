"""中継のテストに共通する補助。"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import Callable

import pytest
from ndf.domains.relay import account_dir


def _hold_lock(path: Path) -> int:
    """親の保存先を作って錠のファイルを開き、排他を持つ。閉じるのは呼んだ側。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


@pytest.fixture
def hold_lock() -> Callable[[Path], int]:
    """錠を持つ関数（ラッパーやリレーハブの代わりに錠を持つ）。"""
    return _hold_lock


@pytest.fixture(autouse=True)
def no_managed_settings(tmp_path, monkeypatch):
    """管理の設定の保存先を一時ディレクトリへ替える（手元と CI の /etc の管理の設定で起動の前の確かめを落とさない）。"""
    etc = tmp_path / "etc" / "claude-code"
    monkeypatch.setattr(account_dir, "MANAGED_FILES", (etc / "managed-settings.json",))
    monkeypatch.setattr(account_dir, "MANAGED_DIR", etc / "managed-settings.d")
