"""サブコマンド `ndf progress` のエントリポイント（サブコマンドの表の `progress` の行がこのモジュールを指す）。

`ndf answer`（`ndf.progress.gate`）と同じ名前のサブコマンド `work` を持つため、エントリポイントのモジュールを分けてサブコマンドごとに受ける。
受ける関数は `ndf.progress.gate.entry.progress_main` にある。版上げのスプリントのステップ（`bump-assemble`・`bump-present`・`bump-record`）は `ndf.progress.gate.bump` が受ける。
"""

from __future__ import annotations

from ndf import cli
from ndf.progress.gate.entry import progress_main as main

TOOL = cli.tool_of(__name__)

__all__ = ["TOOL", "main"]
