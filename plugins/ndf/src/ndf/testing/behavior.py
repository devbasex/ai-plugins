"""振る舞いの確認の起動（#3187）。

対象ごとの `claude -p` は、その対象のリクエスト文だけを入力に受ける。標準入力を閉じて起動し、呼び出し元の標準入力
（繰り返しで流すリクエスト文の並びなど）を受け継がせない。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ndf.contracts import limits

# 1 件の確認の費用の上限（USD）の方針（contracts/limits/）
BUDGET_POLICY = "behavior-check.budget"


def claude_check_command(plugin_root: Path, prompt: str, budget_usd: float | None = None) -> list[str]:
    """1 件の確認の起動の語の並び。リクエスト文は引数で渡す。費用の上限（USD）は省けば設定の値。"""
    return [
        "claude",
        "-p",
        "--plugin-dir",
        str(plugin_root),
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--max-budget-usd",
        str(limits.value(BUDGET_POLICY, "count") if budget_usd is None else budget_usd),
        prompt,
    ]


def launch(argv: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    """標準入力を閉じて起動し、出力を集める。"""
    return subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, check=False)
