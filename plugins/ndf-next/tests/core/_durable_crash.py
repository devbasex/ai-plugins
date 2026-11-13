"""判断のループを耐久ワークフローで回し、指定の手で落ちるプロセス（test_durable.py が子プロセスで起こす）。

引数: <DB の URL> <ワークフローの ID> <手の記録のファイル> <落ちる手の番号（0 なら落ちない）>
落ちない回は、回復した耐久ワークフローの結果を JSON で標準出力へ 1 行書く。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from _counting import decide

from ndf.core import durable, loop

DB_URL, WORKFLOW_ID, LOG, CRASH_AT = sys.argv[1], sys.argv[2], Path(sys.argv[3]), int(sys.argv[4])
EXECUTOR = "crash-test"
# 落ちる手で落ちたことを示す終了コード（test_durable.py の CRASH_EXIT_CODE と同じ値）
CRASH_EXIT_CODE = 9


@durable.step
def perform(act: int) -> int:
    with LOG.open("a") as f:
        f.write(f"{act}\n")
    if act == CRASH_AT:
        os._exit(CRASH_EXIT_CODE)
    return act * 10


@durable.workflow
def flow(start: list[int]) -> list[int]:
    return loop.run(decide, perform, start)


def main() -> None:
    durable.launch(DB_URL, EXECUTOR, "durable-crash-test")
    try:
        if CRASH_AT:
            durable.start(flow, WORKFLOW_ID, [])
        else:
            print(json.dumps(durable.result(WORKFLOW_ID)))
    finally:
        durable.shutdown()


if __name__ == "__main__":
    main()
