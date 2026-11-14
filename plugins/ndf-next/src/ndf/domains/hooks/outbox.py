"""送信待ち（#3345 の決定 93・IH7）。

hook のエントリポイントは通信しない。通知は 1 件を 1 ファイルとして `<状態の置き場>/hooks/outbox/<宛先>/` へ一時の名前で書いて
名前を変え（`enqueue`）、NDF 自身の送り手（`ndf.domains.hooks.sender`）を切り離して起動する（`kick`）。
送り手が走っていれば起動しない。どちらも待たない。投稿キュー（Pull Request へのコメントの投稿）とは別である。

- 送信待ちは宛先（Slack の接続とチャンネル）ごとの置き場に分ける。置き場の名前は宛先の sha256 の先頭で、
  秘密そのものは書かない。別のプロジェクトの接続で起動した送り手は、ほかの宛先の通知を読まない
- 送り手は置き場の錠（`LOCK_FILE`）の排他を取ってから送り、取れなければ送らずに終わる。`kick` は錠が
  持たれていれば起動しない。送り手は錠を離した後に一覧を読み直し、残りがあれば取り直して送る
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional

from ndf.domains.hooks import tables

SENDER_MODULE = "ndf.domains.hooks.sender"
LOCK_FILE = ".sender.lock"


def notify_configured(env: Mapping[str, str]) -> bool:
    """通知の宛先がそろっているか（`SLACK_BOT_TOKEN` と `SLACK_CHANNEL_ID`）。"""
    return bool(env.get("SLACK_BOT_TOKEN") and env.get("SLACK_CHANNEL_ID"))


def outbox_dir(state_dir: Path, env: Mapping[str, str]) -> Path:
    """宛先（`SLACK_BOT_TOKEN` と `SLACK_CHANNEL_ID`）ごとの送信待ちの置き場。"""
    dest = f"{env.get('SLACK_BOT_TOKEN', '')}\n{env.get('SLACK_CHANNEL_ID', '')}"
    return tables.hooks_dir(state_dir) / "outbox" / hashlib.sha256(dest.encode("utf-8")).hexdigest()[:16]


def enqueue(state_dir: Path, kind: str, payload: dict[str, Any], env: Mapping[str, str]) -> Path:
    """通知を 1 件、宛先の置き場へ書き、置いたパスを返す。"""
    box = outbox_dir(state_dir, env)
    box.mkdir(parents=True, exist_ok=True)
    name = f"{time.time_ns()}-{os.getpid()}-{uuid.uuid4().hex[:8]}.json"
    tmp = box / f".{name}.tmp"
    tmp.write_text(json.dumps({"kind": kind, "payload": payload}, ensure_ascii=False), encoding="utf-8")
    final = box / name
    os.replace(tmp, final)
    return final


def try_lock(box: Path) -> Optional[int]:
    """送り手の錠を待たずに取る。取れたら fd、持たれていれば None。"""
    box.mkdir(parents=True, exist_ok=True)
    fd = os.open(box / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:  # noqa: NDF401 ファイル: 錠が持たれていれば送り手が走っている
        os.close(fd)
        return None
    return fd


def release(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def sender_alive(box: Path) -> bool:
    """送り手が置き場の錠を持っているか。"""
    fd = try_lock(box)
    if fd is None:
        return True
    release(fd)
    return False


def kick(state_dir: Path, env: Mapping[str, str]) -> bool:
    """宛先の送り手を切り離して起動する。走っていれば起動しない。起動したら真。"""
    if sender_alive(outbox_dir(state_dir, env)):
        return False
    child_env = {**env, "PYTHONPATH": str(tables.PLUGIN_ROOT / "src")}
    subprocess.Popen(
        [sys.executable, "-P", "-m", SENDER_MODULE, str(state_dir)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=child_env,
        cwd=str(tables.PLUGIN_ROOT),
    )
    return True
