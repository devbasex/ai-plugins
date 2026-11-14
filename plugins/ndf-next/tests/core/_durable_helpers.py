"""durable のテストが共有する補助（鍵 1 つで耐久ワークフローを流す子プロセスの起動）。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
RUN_SCRIPT = Path(__file__).with_name("_durable_run.py")


def run_key_process(root: Path, key: str, mode: str, url: str | None = None) -> None:
    """`_durable_run.py` を子プロセスで打ち、正常に終わったことを確かめる。`url` が無ければ引数に足さない。"""
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    args = [sys.executable, str(RUN_SCRIPT), str(root), key, mode, *([] if url is None else [url])]
    done = subprocess.run(
        args,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
