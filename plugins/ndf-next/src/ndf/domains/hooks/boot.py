"""環境が無いときの hook の入口の本体（#3345 の決定 89・91・IH2）。標準ライブラリだけで書く。

登録の command は、環境の python が無ければシステムの `python3` で `hooks/boot.py` を起動し、`hooks/boot.py` が
`run_boot(sys.argv)` を呼ぶ。引数は `entry` と同じ位置（`argv[1]` がランタイム、`argv[2]` が事象）にある。

行うのは次の 2 つだけで、ほかは出力なし・終了コード 0 で通す。

- `strict` の guard の判定（止める答えなら契約表の形で出す）
- SessionStart のとき、根の目印を書き、環境の用意を切り離して起動する（待たない）。前の用意が失敗していれば
  1 行の案内を出す

版がシステムの `python3` の下限（`contracts/hooks/imports.json` の `boot.python`）より古ければ何もせずに 0。
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import List, Optional, TextIO

from ndf.domains.hooks import tables


def _payload(text: str) -> Optional[dict]:
    try:
        data = json.loads(text) if text.strip() else None
    except ValueError:  # noqa: NDF401 利用者の入力: JSON でない入力は通す
        return None
    return data if isinstance(data, dict) else None


def run_boot(argv: List[str], stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
    """環境が無いときの入口。いつも 0。"""
    started = time.monotonic()
    out = stdout or sys.stdout
    record: dict = {"runtime": None, "event": None, "tool": None, "path": "boot", "guards": [], "verdict": "pass", "error": ""}
    try:
        if sys.version_info[:2] < tuple(tables.load("hooks/imports.json")["boot"]["python"]):
            return 0
        runtime, event = argv[1], argv[2]
        record.update(runtime=runtime, event=event)
        payload = _payload((stdin or sys.stdin).read())
        if payload is None or tables.event_spec(runtime, event) is None:
            record["error"] = "input_shape" if payload is None else "unknown_event"
            return 0
        record["tool"] = payload.get("tool_name")
        out.write(_boot(runtime, event, payload, record))
    except Exception as exc:  # noqa: BLE001 利用者の入力: 環境が無い入口のどの失敗も通す（IH4）
        record["error"] = type(exc).__name__
    finally:
        record["total_ms"] = round((time.monotonic() - started) * 1000, 3)
        _write_metrics(record)
    return 0


def _boot(runtime: str, event: str, payload: dict, record: dict) -> str:
    from ndf.domains.hooks import strict

    off = tables.off_names(os.environ.get("NDF_HOOK_OFF"))
    began = time.monotonic()
    verdict = None if tables.is_off("merge-production", off) else strict.check(payload, str(payload.get("cwd") or os.getcwd()))
    if verdict is not None:
        ms = round((time.monotonic() - began) * 1000, 3)
        record.update(verdict="deny", guards=[{"name": "merge-production", "ms": ms, "kind": "deny", "error": ""}])
        return tables.output_for(runtime, event, "deny", verdict.reason)
    if event != "SessionStart":
        return ""
    return _session_start(runtime, event)


def _session_start(runtime: str, event: str) -> str:
    from ndf.domains.hooks import env

    ident = env.Ident.of(tables.PLUGIN_ROOT)
    env.mark_root(ident)
    failed = env.failed_path(ident)
    note = ""
    if failed.exists():
        reason = failed.read_text(encoding="utf-8").strip().splitlines()[:1]
        note = f"hook の依存の環境の用意に失敗していた（{''.join(reason)}）。用意を起動し直した。用意ができるまで hook の guard は働かない"
    env.kick_prepare(ident)
    return tables.output_for(runtime, event, "advise", note)


def _write_metrics(record: dict) -> None:
    from ndf import identity
    from ndf.domains.hooks import metrics

    try:
        state_dir = identity.load().state_dir
    except identity.IdentityError:  # noqa: NDF401 ファイル: plugin.json を読めなければ計測を書かない
        return
    metrics.write_line(state_dir, record)
