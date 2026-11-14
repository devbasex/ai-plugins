"""判断のループを耐久ワークフローで回し、指定の手で止まって待ち続けるプロセス（test_faults.py が起こし、SIGKILL で落とす）。

引数: <チェックアウトの置き場> <実行の鍵> <ワークフローの ID> <手の記録のファイル> <止まる手の番号（0 なら止まらない）>
止まらない回は、回復した耐久ワークフローの結果を JSON で標準出力へ 1 行書く。
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from ndf.core import durable, loop
from ndf.core.loop import Next

ROOT, RUN_KEY, WORKFLOW_ID = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
LOG, HANG_AT = Path(sys.argv[4]), int(sys.argv[5])
ACTS = 3


def decide(state: list[int], result: int | None) -> Next[int, list[int]]:
    seen = state if result is None else [*state, result]
    if len(seen) == ACTS:
        return Next.finish(seen)
    return Next.step(len(seen) + 1, seen)


@durable.step
def perform(act: int) -> int:
    with LOG.open("a") as f:
        f.write(f"{act}\n")
    if act == HANG_AT:
        threading.Event().wait()  # 落とされるまで止まる（後片付けの猶予を与えない落ち方を待つ）
    return act * 10


@durable.workflow
def flow(start: list[int]) -> list[int]:
    return loop.run(decide, perform, start)


def main() -> None:
    with durable.session(ROOT, RUN_KEY):
        if HANG_AT:
            durable.start(flow, WORKFLOW_ID, [])
        else:
            print(json.dumps(durable.result(WORKFLOW_ID)))


if __name__ == "__main__":
    main()
