"""step_read.py: 手順のスクリプトの結果と材料を読む側の共通の規則（#2227）。

- `result_line` / `not_ok_reason`: 子プロセスの標準出力の最後の行を結果 JSON として読み、`ok`（終了コード 0）で
  なければ理由の 1 行を返す。結果 JSON が無い・形が誤っていることも成功と読まない
- `read_result_file`: 結果ファイル（worker の作業の結果ファイル・`NDF_RESULT_FILE`）を読む
- `last_json`: 出力の最後の JSON の行を、形を確かめずに読む（要約・件数を写すだけの箇所。成否の判定には使わない）
- `read_completed`: 子プロセス（`subprocess.CompletedProcess`）の結果を読む
  どちらも結果 JSON を返し、無い・読めない・形が違う・状態と終了コードが食い違うときは理由つきの `stopped` を返す

読むだけで、結果を書き換えない。形の正本は同じディレクトリの README.md の
「手順のスクリプトの結果」。標準ライブラリだけで書く。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import step_result  # noqa: E402


def last_json(text: str) -> dict | None:
    """出力の最後の JSON の行を読む（形は確かめない）。成否は `read_completed` / `not_ok_reason` で決める。"""
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            return obj if isinstance(obj, dict) else None
    return None


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


def _stopped_result(tool: str, kind: str, name: str, reason: str) -> dict:
    return step_result.result(tool, "stopped", reason, gaps=[{"kind": kind, "name": name, "reason": reason}])


def read_result_file(path: str | os.PathLike, tool: str = "result-file") -> dict:
    """結果ファイルの 1 行を結果 JSON として読む。無い・読めない・形が違えば理由つきの stopped を返す（I10）。"""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _stopped_result(tool, "result-missing", str(p), f"結果ファイルが無い: {p}")
    except OSError as e:
        return _stopped_result(tool, "result-unreadable", str(p), f"結果ファイルを読めない: {p}: {e}")
    obj = result_line(text)
    if obj is None:
        return _stopped_result(tool, "result-shape", str(p), f"結果ファイルが結果 JSON の形でない: {p}")
    return obj


def read_completed(completed, tool: str = "child") -> dict:
    """子プロセスの標準出力の最後の行を結果 JSON として読む。無い・形が違う・状態と終了コードが食い違えば stopped を返す。"""
    obj = result_line(completed.stdout)
    code = completed.returncode
    if obj is None:
        why = f"結果 JSON が無い（exit={code}）: {(completed.stderr or '').strip()[:200]}"
        return _stopped_result(tool, "result-missing", tool, why)
    if not step_result.code_matches(obj["status"], code):
        return _stopped_result(obj.get("tool") or tool, "result-shape", tool, f"状態 {obj['status']} と終了コード {code} が食い違う")
    return obj


def is_ok(obj: dict | None) -> bool:
    """結果を成功と読むのは status が ok のときだけ。"""
    return isinstance(obj, dict) and obj.get("status") == "ok"
