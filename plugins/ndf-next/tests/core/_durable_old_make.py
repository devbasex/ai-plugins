"""前の版の DBOS で、途中で落ちた耐久ワークフローを持つ鍵のファイルを作る（B12 の見本 `fixtures/` を作り直すときに打つ）。

ndf を読まず、DBOS だけで作る（前の版の DBOS は今の殻の API を持たないことがある）。DBOS の版を上げる PR は、
上げる前の版でこのスクリプトを打ち、見本を置き換える:

    uv run --no-project --with dbos==<前の版> python tests/core/_durable_old_make.py <出力の .sqlite> <鍵の名前>

鍵の名前は `durable.run_name(OLD_RUN_KEY)`（test_durable_migrate.py）。ステップ 1 を記録し、ステップ 2 で落ちる。
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys

from dbos import DBOS, SetWorkflowID

# ステップ 2 で落ちたことを示す終了コード
CRASH_EXIT_CODE = 9
WORKFLOW_ID = "old-wf"


@DBOS.step()
def old_step(n: int) -> int:
    if n == 2 and os.environ.get("OLD_CRASH"):
        os._exit(CRASH_EXIT_CODE)
    return n * 10


@DBOS.workflow()
def old_flow() -> list[int]:
    return [old_step(1), old_step(2), old_step(3)]


def _crash(path: str, name: str) -> None:
    DBOS(config={"name": name, "system_database_url": f"sqlite:///{path}", "executor_id": name, "application_version": f"format-1-{name}"})
    DBOS.launch()
    with SetWorkflowID(WORKFLOW_ID):
        old_flow()


def main() -> None:
    path, name = sys.argv[1], sys.argv[2]
    if os.environ.get("OLD_CRASH"):
        _crash(path, name)
        return
    done = subprocess.run([sys.executable, __file__, path, name], env={**os.environ, "OLD_CRASH": "1"}, check=False)
    if done.returncode != CRASH_EXIT_CODE:
        raise SystemExit(f"ステップ 2 で落ちなかった: {done.returncode}")
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("VACUUM")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
