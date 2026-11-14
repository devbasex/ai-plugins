"""副命令 `ndf progress` の入口（副命令の表の `progress` の行がこのモジュールを指す）。

`ndf answer`（`ndf.progress.gate`）と同じ名前の副命令 `work` を持つため、入口のモジュールを分けて副命令ごとに受ける。
受ける関数は `ndf.progress.gate.entry.progress_main` にある。
"""

from __future__ import annotations

from ndf import cli
from ndf.progress.gate.entry import progress_main as main

TOOL = cli.tool_of(__name__)

__all__ = ["TOOL", "main"]
