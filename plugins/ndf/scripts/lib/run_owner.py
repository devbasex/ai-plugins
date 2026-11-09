"""見直しの持ち主の宣言（`.ndf/backlog/owner.json`。#2315）の読み書きと寿命（#2229）。

記録は `run`（実行の識別子）・`seen_at`（最後に動いた時刻）・`finished_at`（終わった時刻）を持つ。終わっていなくても、
最後に動いた時刻から寿命の窓を過ぎた実行は終わったとみなす。レビュー worktree（`pr<n>`・`rf<n>`）の持ち主は
持ち主の部品（`lease.py`。#2342）へ移した。片付け（`run_state.py`）は、旧い版が worktree の隣に残した
`<pr<n>|rf<n>>.owner.json` を worktree と一緒に消すためだけに `owner_path` を使う。
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import socket
from pathlib import Path
from typing import Any

# 寿命の窓: 記録が終わっていなくても、最後に動いた時刻からこの秒数を過ぎた実行を終わったとみなす（決定 4）。
WINDOW = 24 * 3600
# レビュー worktree の中の NDF の状態（未追跡の判定から外し、外す前に消す）。
NDF_STATE_DIRS = (".cross_review", ".cross_refactoring")


def _epoch(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc).timestamp()
    except ValueError:
        return None


def utc_iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def owner_path(state: Path | str) -> Path:
    """持ち主の記録の置き場。レビュー worktree（`pr<n>`・`rf<n>`）は worktree の外（隣）に置く（決定 3）。
    `.json` で終わるパス（見直しの持ち主の宣言 `.ndf/backlog/owner.json`。#2315）はそのまま使う。"""
    p = Path(state)
    return p if p.suffix == ".json" else p.with_name(f"{p.name}.owner.json")


def read_owner(state: Path | str) -> tuple[dict[str, Any] | None, str | None]:
    """(記録, 読めない理由)。記録が無ければ (None, None)。"""
    path = owner_path(state)
    if not path.exists():
        return None, None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"持ち主の記録を読めない ({path}: {e})"
    if not isinstance(record, dict) or not record.get("run"):
        return None, f"持ち主の記録の形が違う ({path})"
    return record, None


def _write_owner(state: Path | str, record: dict[str, Any]) -> None:
    path = owner_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


write_owner = _write_owner  # 見直しの持ち主の宣言（#2315）が使う公開の名前


def _last_seen(state: Path, record: dict[str, Any] | None) -> float:
    if record is not None:
        got = _epoch(record.get("seen_at")) or _epoch(record.get("started_at"))
        if got is not None:
            return got
    return mtime(state)


def mtime(path: Path) -> float:
    """状態の最終更新の時刻。ディレクトリは NDF の状態の置き場の時刻も見る。"""
    times = []
    for p in (path, *(path / d for d in NDF_STATE_DIRS)):
        with contextlib.suppress(OSError):
            times.append(p.stat().st_mtime)
    return max(times) if times else 0.0


def owner_pid_alive(record: dict[str, Any] | None) -> bool:
    """記録の `pid` が同じホスト（`host`）で生きているか。別のホストと pid の無い記録は生きているとみなす（窓だけで決める）。"""
    if not record or record.get("host") not in (None, socket.gethostname()) or not isinstance(record.get("pid"), int):
        return True
    try:
        os.kill(record["pid"], 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def alive(state: Path, record: dict[str, Any] | None, now: float, window: float = WINDOW) -> bool:
    """記録（無ければ状態の最終更新）から、持ち主の実行が生きているかを決める（決定 4）。

    `window` は寿命の窓の秒（見直しの持ち主は `.ndf/backlog.json` の `depths.owner.ttl`）。記録に `pid` と `host` があれば、
    同じホストでは pid の生死も見る（#2315 の不変条件 I2）。"""
    if record is not None and record.get("finished_at"):
        return False
    return now - _last_seen(state, record) < window and owner_pid_alive(record)
