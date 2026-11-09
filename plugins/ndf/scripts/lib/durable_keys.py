"""耐久の記録の置き場と実行の鍵（`durable.py` から分けた、DBOS を import しない層）。

記録が有るかを確かめるだけの呼び出し（空の投稿キューの `count()` など）が、DBOS の import と
耐久ワークフローの登録を起こさずに答えられるように分ける。`durable.py` はここの名前をそのまま出す。

- 置き場: `NDF_DBOS_DIR` → `<チェックアウトの根>/dbos`（`records_dir`。根は `state_home.checkout_home`。旧の置き場は読まない）
- 実行の鍵: `<種類>-<識別の sha256 の先頭 12 字>`（I17）
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

from state_home import checkout_home
from typing import Mapping, Optional

KIND_RE = re.compile(r"^[a-z][a-z0-9]*$")


class DurableError(RuntimeError):
    """耐久の記録を開けない・書けない・使い方の誤り。理由を文にして持つ。"""


def records_dir(env: Optional[Mapping[str, str]] = None, start: Optional[Path] = None) -> Path:
    """耐久の記録の置き場（モジュールの docstring の順）。`start` を渡すと、作業ディレクトリでなくそのチェックアウトの根から決める
    （別のリポジトリのレーンが計画の置き場の記録を読む。#2733）。"""
    env = os.environ if env is None else env
    if env.get("NDF_DBOS_DIR"):
        return Path(env["NDF_DBOS_DIR"])
    return checkout_home(start, env=env) / "dbos"


def key_hash(text: str, n: int = 12) -> str:
    """鍵に使う sha256 の先頭 `n` 字。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def launch_key(kind: str, identity: str) -> str:
    """実行の鍵（`<種類>-<12 字>`）。`kind` は英小文字と数字だけ。"""
    if not KIND_RE.match(kind):
        raise DurableError(f"耐久の記録の種類は英小文字と数字だけ: {kind!r}")
    return f"{kind}-{key_hash(identity)}"


def record_path(kind: str, identity: str, start: Optional[Path] = None) -> Path:
    """耐久の記録のファイル。無ければ DBOS を起動せずに「積んでいない」と答えられる。`start` は `records_dir` と同じ。"""
    return records_dir(start=start) / f"{launch_key(kind, identity)}.sqlite"


RETIRED = ".retired-"  # 退避した記録の名前の印（`<鍵>.sqlite.retired-<時刻>`。#2647 の決定 4）


def retire_record(path: Path) -> Path:
    """記録 `path`（`-wal`・`-shm` ごと）を `<名前>.retired-<時刻>` へ移し、移した先を返す。中身は書き換えない。"""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dst = path.with_name(f"{path.name}{RETIRED}{stamp}")
    for suffix in ("-wal", "-shm", ""):
        src = path.with_name(path.name + suffix)
        if src.exists():
            os.replace(src, dst.with_name(dst.name + suffix))
    return dst


def purge_retired(directory: Path, days: float, now: Optional[float] = None) -> list[str]:
    """最終の更新から `days` 日を過ぎた退避の記録を消す。消した名前を返す。"""
    now = time.time() if now is None else now
    gone = []
    for p in sorted(Path(directory).glob(f"*.sqlite{RETIRED}*")):
        try:
            if now - p.stat().st_mtime >= days * 86400:
                p.unlink()
                gone.append(p.name)
        except OSError:
            continue
    return gone
