"""claude -p の応答の読み（`supervise_lib/claude.py` から分けた）。副作用を持たない。"""

from __future__ import annotations

import json
import subprocess

from claude_usage import LIMIT_EPOCH, limit_reset_at
from monitor import USAGE_LIMIT_FATAL  # 利用上限の文言の表


def response_data(p: subprocess.CompletedProcess) -> dict:
    """claude -p の標準出力を応答の辞書へ正規化する。JSON の辞書でなければ本文と終了コードから作る。"""
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"result": p.stdout, "is_error": p.returncode != 0}
    if not isinstance(data, dict):
        return {"result": p.stdout, "is_error": p.returncode != 0}
    return data


def failure_kind(data: dict, p: subprocess.CompletedProcess, ok: bool) -> dict:
    """失敗の種別（`limit`・`auth`・`resets_at`）を求める。利用上限を認証の失敗より先に見る。"""
    result = str(data.get("result") or "")
    limit = not ok and is_usage_limit("\n".join([result, p.stderr, p.stdout]))
    # 認証の失敗（`result` が `Failed to authenticate` で始まるか、`api_error_status` が 401。#1576 の実測 H）
    auth = not ok and not limit and (result.startswith("Failed to authenticate") or data.get("api_error_status") == 401)
    return {"limit": limit, "auth": auth, "resets_at": limit_reset_at("\n".join([result, p.stderr])) if limit else None}


def is_usage_limit(text: str) -> bool:
    """利用上限で落ちたか。lib/monitor.py の USAGE LIMIT の表と同じ文言で照合する。"""
    return any(rx.search(text or "") for rx in USAGE_LIMIT_FATAL) or bool(LIMIT_EPOCH.search(text or ""))
