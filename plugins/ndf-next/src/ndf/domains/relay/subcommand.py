"""中継が NDF の副命令を打って結果 JSON を読む（`ndf plan pending`・`ndf lanes question`）。

中継は進行表の DB を自分では開かず、副命令の結果 JSON の最後の行だけを読む（同じ事実を 2 か所で集めない）。
副命令が無い・落ちた・読めないときは送出せず、読めなかった理由を返す。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import identity
from ndf.contracts.exit_codes import OK

# 読めなかった理由に載せる、結果の要約か標準エラーの末尾の字数
NOTE_CHARS = 200


class Called(NamedTuple):
    """`result` は status が ok の結果 JSON（読めなければ None）、`note` は読めなかった理由。"""

    result: Optional[dict[str, Any]]
    note: str


def call(plugin_root: Path, args: list[str], timeout: float, runner: Any = subprocess.run, cwd: Optional[Path] = None) -> Called:
    """`ndf <args>` を打ち、結果 JSON の最後の行を読む。"""
    command = [str(identity.entrypoint(plugin_root)), *args]
    label = "副命令 " + " ".join(args[:2])
    try:
        done = runner(command, capture_output=True, text=True, timeout=timeout, check=False, cwd=None if cwd is None else str(cwd))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Called(None, f"{label} を打てない: {exc}")
    last = last_json(done.stdout)
    if last is None or last.get("status") != OK:
        return Called(None, _failure_note(label, last, done))
    return Called(last, "")


def last_json(stdout: str) -> Optional[dict[str, Any]]:
    """空でない最後の行を JSON として読む。行が無い・JSON でない・オブジェクトでなければ None。"""
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return None
    try:
        last = json.loads(lines[-1])
    except ValueError:  # noqa: NDF401 外部コマンド: JSON でない行は読めない理由へ回す
        return None
    return last if isinstance(last, dict) else None


def _failure_note(label: str, last: Optional[dict[str, Any]], done: Any) -> str:
    """読めなかった理由。結果の要約（無ければ標準エラーの末尾）を 1 行・200 字に詰める。"""
    summary = last.get("summary") if last is not None else (done.stderr.strip()[-NOTE_CHARS:] or "出力が無い")
    one_line = " ".join(str(summary).split())[:NOTE_CHARS]
    return f"{label} の結果を読めない（終了コード {done.returncode}）: {one_line}"
