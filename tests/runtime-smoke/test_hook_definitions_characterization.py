"""hooks 定義の検査（assert-hook-definitions.sh / codex-hooks-list.py）の判定分岐を固定する。

本体は bash の assert-characterization.sh にあり、claude / codex をスタブへ置き換えて走る。
実機のスモークに混ぜると、ランタイムごとに同じ単体テストが重ねて走るため、ここから 1 回だけ起動する。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tests" / "runtime-smoke" / "fixtures" / "hooks-positive-control" / "assert-characterization.sh"


def test_hook_definitions_characterization() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={**os.environ, "REPO_ROOT": str(REPO)},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"assert-characterization.sh exited {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
