"""決まった手順のコマンドの表示と起動（`release-steps.py` の `run_steps` から分けた）。

`step` は `release-steps.py` の `Step`（name・stage・command・writes・guide・timeout）である。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def print_step(step, command: list[str]) -> None:
    """dry-run の表示（実行しない）。"""
    print(f"コマンド: {step.name}（{step.stage}）")
    print(f"  command: {' '.join(command)}")
    print(f"  writes: {', '.join(step.writes) or '（何も書かない）'}")
    if step.guide:
        print(f"  guide: {step.guide}")


def run_command(root: Path, step, command: list[str]) -> int | None:
    """コマンドを走らせて終了コードを返す。時間切れか起動できなければ、知らせて None（1 で止める合図）。"""
    sys.stdout.flush()
    try:
        return subprocess.run(command, cwd=str(root), timeout=step.timeout).returncode
    except subprocess.TimeoutExpired:
        print(f"コマンド: {step.name} → 時間切れ（{step.timeout} 秒）")
    except OSError as e:
        print(f"コマンド: {step.name} → 起動できない: {e}")
    return None
