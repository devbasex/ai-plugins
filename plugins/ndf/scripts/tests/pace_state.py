"""supervise.py の pace（fast・auto）のテストに共通するスプリントの状態の土台。

MVV の素材を置いて sprint-state.py init で状態を作り、approve なら MVV の承認を記録する。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SPRINT_STATE = Path(__file__).resolve().parents[1] / "sprint-state.py"
PY = sys.executable


def sprint_state(tmp_path: Path, pace: str, approve: bool = True) -> Path:
    mvv = tmp_path / "mvv-src.md"
    mvv.write_text("## Mission\n速く\n## Vision\n回る\n## Value\n実測\n")
    state = tmp_path / "state" / "sprint-state.json"
    subprocess.run(
        [PY, str(SPRINT_STATE), "init", str(state), "--name", "m", "--pace", pace, "--mvv", str(mvv), "--root", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    if approve:
        subprocess.run([PY, str(SPRINT_STATE), "gate", str(state), "MVV", "--what", "MVV を承認"], check=True, capture_output=True)
    return state
