"""プラグインの更新を反映する再起動の前に載せる、待ちを抱えた実行の一覧（#3344 の決定 86・M3-1）。

一覧はプランの実行の副命令 `ndf plan pending`（C2）が出す結果 JSON の `items` である。中継はそれを読むだけで、
置き場の鍵ごとのファイルを自分では読まない（同じ事実を 2 か所で集めない）。副命令が無い・落ちた・読めないときも
再起動は止めず、読めなかった理由を結果に載せる。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import identity
from ndf.contracts.exit_codes import OK


class Pending(NamedTuple):
    """`runs` は待ちを抱えた実行（読めなければ None）、`note` は読めなかった理由。"""

    runs: Optional[list[Any]]
    note: str


def read(plugin_root: Path, timeout: float, runner: Any = subprocess.run) -> Pending:
    """`ndf plan pending` を打って一覧を読む。"""
    command = [str(plugin_root / "bin" / identity.FIXED_WORDS["entrypoint"]), "plan", "pending"]
    try:
        done = runner(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Pending(None, f"ndf plan pending を打てない: {exc}")
    last = _last_json(done.stdout)
    if last is None or last.get("status") != OK:
        return Pending(None, _failure_note(last, done))
    items = last.get("items")
    return Pending(items if isinstance(items, list) else [], "")


def _last_json(stdout: str) -> Optional[dict[str, Any]]:
    """空でない最後の行を JSON として読む。行が無い・JSON でない・オブジェクトでなければ None。"""
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return None
    try:
        last = json.loads(lines[-1])
    except ValueError:  # noqa: NDF401 外部コマンド: JSON でない行は読めない理由へ回す
        return None
    return last if isinstance(last, dict) else None


def _failure_note(last: Optional[dict[str, Any]], done: Any) -> str:
    """読めなかった理由。結果の要約（無ければ標準エラーの末尾）を 1 行・200 字に詰める。"""
    summary = last.get("summary") if last is not None else (done.stderr.strip()[-200:] or "出力が無い")
    one_line = " ".join(str(summary).split())[:200]
    return f"ndf plan pending の一覧を読めない（終了コード {done.returncode}）: {one_line}"
