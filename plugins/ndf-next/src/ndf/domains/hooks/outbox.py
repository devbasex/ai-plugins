"""送信待ち（#3345 の決定 93・IH7）。

hook のエントリポイントは通信しない。通知は 1 件を 1 ファイルとして `<状態の置き場>/hooks/outbox/` へ一時の名前で書いて
名前を変え（`enqueue`）、NDF 自身の送り手（`ndf.domains.hooks.sender`）を切り離して起動する（`kick`）。
送り手が走っていれば起動しない。どちらも待たない。投稿キュー（Pull Request へのコメントの投稿）とは別である。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from ndf.domains.hooks import tables

SENDER_MODULE = "ndf.domains.hooks.sender"
PID_FILE = ".sender.pid"


def outbox_dir(state_dir: Path) -> Path:
    return state_dir / "hooks" / "outbox"


def enqueue(state_dir: Path, kind: str, payload: dict[str, Any]) -> Path:
    """通知を 1 件書き、置いたパスを返す。"""
    box = outbox_dir(state_dir)
    box.mkdir(parents=True, exist_ok=True)
    name = f"{time.time_ns()}-{os.getpid()}-{uuid.uuid4().hex[:8]}.json"
    tmp = box / f".{name}.tmp"
    tmp.write_text(json.dumps({"kind": kind, "payload": payload}, ensure_ascii=False), encoding="utf-8")
    final = box / name
    os.replace(tmp, final)
    return final


def sender_alive(state_dir: Path) -> bool:
    """送り手の pid のファイルが生きたプロセスを指すか。"""
    try:
        pid = int((outbox_dir(state_dir) / PID_FILE).read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except (OSError, ValueError):  # noqa: NDF401 ファイル: pid が無い・読めない・死んでいれば走っていない
        return False
    return True


def kick(state_dir: Path) -> bool:
    """送り手を切り離して起動する。走っていれば起動しない。起動したら真。"""
    if sender_alive(state_dir):
        return False
    env = {**os.environ, "PYTHONPATH": str(tables.PLUGIN_ROOT / "src")}
    subprocess.Popen(
        [sys.executable, "-P", "-m", SENDER_MODULE, str(state_dir)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
        cwd=str(tables.PLUGIN_ROOT),
    )
    return True
