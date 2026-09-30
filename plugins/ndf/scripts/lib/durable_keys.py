"""耐久の記録の置き場と実行の鍵（`durable.py` から分けた、DBOS を import しない層）。

記録が有るかを確かめるだけの呼び出し（空の投稿キューの `count()` など）が、DBOS の import と
耐久ワークフローの登録を起こさずに答えられるように分ける。`durable.py` はここの名前をそのまま出す。

- 置き場: `NDF_DBOS_DIR` → `${XDG_STATE_HOME}/ndf/dbos` → `~/.local/state/ndf/dbos`（`records_dir`）
- 実行の鍵: `<種類>-<識別の sha256 の先頭 12 字>`（I17）
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Mapping, Optional

KIND_RE = re.compile(r"^[a-z][a-z0-9]*$")


class DurableError(RuntimeError):
    """耐久の記録を開けない・書けない・使い方の誤り。理由を文にして持つ。"""


def records_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    """耐久の記録の置き場（モジュールの docstring の順）。"""
    env = os.environ if env is None else env
    if env.get("NDF_DBOS_DIR"):
        return Path(env["NDF_DBOS_DIR"])
    base = env.get("XDG_STATE_HOME") or str(Path(env.get("HOME") or Path.home()) / ".local" / "state")
    return Path(base) / "ndf" / "dbos"


def key_hash(text: str, n: int = 12) -> str:
    """鍵に使う sha256 の先頭 `n` 字。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def launch_key(kind: str, identity: str) -> str:
    """実行の鍵（`<種類>-<12 字>`）。`kind` は英小文字と数字だけ。"""
    if not KIND_RE.match(kind):
        raise DurableError(f"耐久の記録の種類は英小文字と数字だけ: {kind!r}")
    return f"{kind}-{key_hash(identity)}"


def record_path(kind: str, identity: str) -> Path:
    """耐久の記録のファイル。無ければ DBOS を起動せずに「積んでいない」と答えられる。"""
    return records_dir() / f"{launch_key(kind, identity)}.sqlite"
