"""次の区間から使うアカウントの指定（`relay.py account use`。#2460）。

指定は置き場（`lib/claude_accounts.py` の `store_dir`）の `pinned.json`（0600）に持ち、ラッパーが区間の起動のたびに
読む。環境変数で持つと動いているラッパーへ届かないためファイルにする。同じ置き場を使うラッパーすべてに効く。
"""

from __future__ import annotations

import os
import time

from .common import load_json, write_json_atomic

import claude_accounts as ca  # noqa: E402,I001  common が lib/ を sys.path に置く
from claude_usage import iso_utc  # noqa: E402

PIN_FILE = "pinned.json"


def pin_path() -> str:
    return os.path.join(ca.store_dir(), PIN_FILE)


def pinned() -> str | None:
    """指定したアカウントの名前。指定が無い・読めない・登録に無い名前なら None。"""
    d = load_json(pin_path())
    name = d.get("name") if isinstance(d, dict) else None
    return name if isinstance(name, str) and name in ca.names() else None


def set_pinned(name: str) -> None:
    ca.make_store()
    write_json_atomic(pin_path(), {"version": 1, "name": name, "pinned_at": iso_utc(time.time())})


def clear() -> bool:
    """指定を外す。無ければ偽。"""
    try:
        os.remove(pin_path())
    except FileNotFoundError:
        return False
    return True
