"""リレーハブの起こし直し（restart）の起動の入力（#3555）。`hub.py` から分けた。"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from . import claude as cl
from . import switch as sw
from .common import fallback_cwd

if TYPE_CHECKING:
    from .hub import Wrapper


def _resumed(start: dict) -> bool:
    """その区間が前の会話を `--resume` で続けたか。印（`resumed`）の無い古い行は、入力が再開の文かで見る（#3555）。"""
    return bool(start.get("resumed")) or start.get("command") in sw.RESUME_TEXTS


def restart_input(name: str, row: dict, w: Wrapper | None) -> tuple[str, list[str]] | None:
    """やり直しの (cwd, 引数)。区間 1 なら登録簿の引数、2 以降は最後の start の行の入力（決定 10）。
    cwd は最後の start の行のもの（区間の切り替えで worktree へ移っている）。

    最後の区間が前の会話を続けた区間（上限での切り替え）なら、その会話を `--resume` で再開する。再開の文は会話の外では
    意味を持たず、新しい会話へ打つと別の役目の作業を拾う（#3555）。`--resume` は同じ会話の ID のまま続けるため、
    `from_session` がその区間の会話である。会話を決められなければ None（起こさない）。
    印の無い古い行のうち、未達の `/goal` を入れ直して再開した区間は見分けられず、今までどおり新しい会話へ打つ。"""
    args = [a for a in row.get("args") or [] if isinstance(a, str)]
    start = w.start if w else None
    cwd = (start or {}).get("cwd") or row.get("cwd") or os.path.expanduser("~")
    if not os.path.isdir(cwd):
        cwd = fallback_cwd(cwd)
    if start and isinstance(start.get("section"), int) and start["section"] >= 2 and start.get("command"):
        args = cl.carried_args(args)
        if not _resumed(start):
            return cwd, [*args, start["command"]]
        sid = start.get("from_session")
        return (cwd, [*args, "--resume", sid, start["command"]]) if isinstance(sid, str) and sid else None
    if args and args[-1] in sw.RESUME_TEXTS and "--resume" not in args:
        return None  # 前の起こし直しが登録簿へ残した、会話の無い再開の文（利用者が同じ文を渡した場合も止まる側に倒す）
    return cwd, args
