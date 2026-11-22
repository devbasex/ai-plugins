"""NDF が起動した `claude -p` の費用の台帳（statusline の合算が読む）。

- `claude -p` の結果の JSON（`--output-format json` は 1 行、`stream-json` は最後の行）の `total_cost_usd` を 1 起動 1 行で追記する
- 保存先は statusline と同じ `<NDF_CONTEXT_DIR か 旧い状態の根/context>/print-costs.jsonl`。statusline は直近 24 時間の行を足す
- 書けなくても起動の結果は変えない（表示のための記録で、失敗は握りつぶす）
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Mapping, Optional

from ndf import identity

LEDGER_NAME = "print-costs.jsonl"


def ledger_path(environ: Mapping[str, str]) -> Path:
    """台帳のパス。statusline の `ctx_sessions` の 1 つ上と同じ場所。"""
    explicit = environ.get("NDF_CONTEXT_DIR")
    base = Path(explicit) if explicit else identity.legacy_state_root(environ) / "context"
    return base / LEDGER_NAME


def reported_cost(stdout: str) -> Optional[float]:
    """結果の JSON の最後の行の `total_cost_usd`。無い・数でない・JSON でなければ None。"""
    lines = stdout.strip().split("\n")
    try:
        payload = json.loads(lines[-1]) if lines and lines[-1] else None
    except json.JSONDecodeError:  # noqa: NDF401 外部コマンド: 終わる前の出力は JSON でない
        return None
    spent = payload.get("total_cost_usd") if isinstance(payload, dict) else None
    return float(spent) if isinstance(spent, (int, float)) and not isinstance(spent, bool) else None


def record(stdout: str, environ: Optional[Mapping[str, str]] = None) -> None:
    """費用が読めれば台帳へ 1 行足す。読めない・書けないときは何もしない。"""
    cost = reported_cost(stdout)
    if cost is None:
        return
    env = os.environ if environ is None else environ
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "usd": cost, "relay": env.get("NDF_RELAY_NAME") or None}
    path = ledger_path(env)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:  # noqa: NDF401 表示のための記録: 書けなくても起動の結果は変えない
        return
