"""主ディレクトリの排他（merged-steps.py の後片付けが使う。#2436 の異常系 8）。"""

from __future__ import annotations

import contextlib
from pathlib import Path

import locks
from step_result import StepError, git

MAIN_LOCK_WAIT = 600.0  # メインディレクトリの排他を待つ上限（秒）。後片付け 1 件と pull は数十秒で終わる


@contextlib.contextmanager
def main_dir_lock(main_dir):
    """メインディレクトリの index か HEAD を動かす操作を、レーンの間で 1 本ずつにする（`<git の共通ディレクトリ>/ndf-main.lock`）。
    取れなければ StepError（1）。"""
    common = git(main_dir, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    try:
        with locks.exclusive(Path(common) / "ndf-main", timeout=MAIN_LOCK_WAIT):
            yield
    except locks.LockTimeout as e:
        raise StepError(f"メインディレクトリの排他を取れない: {e}") from None
