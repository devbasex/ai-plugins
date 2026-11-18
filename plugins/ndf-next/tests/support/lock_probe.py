"""錠を別の開き方で確かめるテストの補助（本体の `identity.lock_held` を使わず、テストの側で独立に判定する）。"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path


def taken_by_another(path: Path) -> bool:
    """別の開き方（別のプロセスと同じ）で、待たない排他の取得が失敗するか。"""
    fd = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False
