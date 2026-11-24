"""プラグインの更新を反映する再起動の前に載せる、待ちを抱えた実行の一覧（docs/specifications/ndf-rebuild.md の決定 86・M3-1）。

一覧はプランの実行のサブコマンド `ndf plan pending`（C2）が出す結果 JSON の `items` である。中継はそれを読むだけで、
保存先のキーごとのファイルを自分では読まない（同じ事実を 2 か所で集めない）。サブコマンドが無い・落ちた・読めないときも
再起動は止めず、読めなかった理由を結果に載せる。打って読むのは `subcommand.call` である。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf.domains.relay import subcommand


class Pending(NamedTuple):
    """`runs` は待ちを抱えた実行（読めなければ None）、`note` は読めなかった理由。"""

    runs: Optional[list[Any]]
    note: str


def read(plugin_root: Path, timeout: float, runner: Any = subprocess.run) -> Pending:
    """`ndf plan pending` を打って一覧を読む。"""
    got = subcommand.call(plugin_root, ["plan", "pending"], timeout, runner)
    if got.result is None:
        return Pending(None, got.note)
    items = got.result.get("items")
    return Pending(items if isinstance(items, list) else [], "")
