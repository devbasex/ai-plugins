"""step_read.py: 手順のスクリプトの結果と材料を読む側の共通の規則（#2227）。

- `result_line` / `not_ok_reason`: 子プロセスの標準出力の最後の行を結果 JSON として読み、`ok`（終了コード 0）で
  なければ理由の 1 行を返す。結果 JSON が無い・形が誤っていることも成功と読まない

読むだけで、結果を書き換えない。形の正本は同じディレクトリの README.md の
「手順のスクリプトの結果」。標準ライブラリだけで書く。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import step_result  # noqa: E402


def result_line(stdout: str) -> dict | None:
    """標準出力の最後の空でない行を結果 JSON として読む。無い・形が誤っていれば None。"""
    lines = [ln for ln in (stdout or "").splitlines() if ln.strip()]
    try:
        obj = json.loads(lines[-1]) if lines else None
    except ValueError:
        return None
    return obj if obj is not None and not step_result.validate_result(obj) else None


def not_ok_reason(stdout: str, code: int, stderr: str = "") -> str | None:
    """結果 JSON が ok（終了コード 0）なら None、そうでなければ状態・終了コード・要約・欠けの理由の 1 行を返す。"""
    obj = result_line(stdout)
    if obj is None:
        return f"結果 JSON が無い（exit={code}）: {(stderr or '').strip()[:200]}"
    if obj["status"] == "ok" and code == 0:
        return None
    reasons = "; ".join(g["reason"] for g in obj.get("gaps", []))
    return f"{obj['status']}（exit={code}）: {obj['summary']}" + (f"（{reasons}）" if reasons else "")


def process_if_not_ok(run: str, completed) -> list[dict]:
    """子プロセス（`subprocess.CompletedProcess`）の結果 JSON が ok でなければ、過程の項目 1 件（`run` / `result`）の列を返す。"""
    why = not_ok_reason(completed.stdout, completed.returncode, completed.stderr)
    return [{"run": run, "result": why}] if why else []
