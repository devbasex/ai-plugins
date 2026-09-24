"""取り込みコマンドに共通する準備。"""
from __future__ import annotations

import pathlib
from typing import Any

from ..gitfacts import discard_impl_leftovers, flush_pending_push
from ..undo import resume_pending_drop


def prepare_intake(path: pathlib.Path, state: dict[str, Any]) -> None:
    """取り込み前に未コミット変更、未完の取り消し、未完の公開を片づける。"""
    discard_impl_leftovers(state, str(state["worktrees"]["work"]))
    resume_pending_drop(path, state)
    flush_pending_push(path, state, state)
