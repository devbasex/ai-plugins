"""シェルの guard が共有する判定。"""

from __future__ import annotations

import os

from ndf.domains.hooks import tables
from ndf.domains.hooks.context import ShellCommand


def is_ndf(command: ShellCommand) -> bool:
    """NDF のコマンド（guard の表の `ndf_commands` の名前）を打つか。"""
    names = tables.load("hooks/guards.json")["ndf_commands"]
    return bool(command.argv) and os.path.basename(command.argv[0]) in names


def background_call(tool_input: dict) -> bool:
    """Tool の呼び出しそのものを背景で起動するか（Bash の `run_in_background`）。"""
    return tool_input.get("run_in_background") is True
