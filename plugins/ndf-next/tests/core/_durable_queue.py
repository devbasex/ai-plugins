"""1 つの SQLite のファイルを実行の鍵で開き、自分のキューへ入れるプロセス（test_durable_queue.py が起こす）。

引数: <DB の URL> <実行の鍵> <流し方> <流した鍵の記録のファイル>
- delayed: 遅らせて取り出す耐久ワークフローをキューへ入れ、取り出される前に落ちる
- run: 耐久ワークフローをキューへ入れて終わりを待ち、ほかの鍵の遅らせた分が取り出せる時刻を過ぎるまで動き続ける
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from ndf.core import durable

URL, RUN_KEY, MODE, LOG = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4])
# delayed が入れた分が取り出せるようになるまでの秒数と、run がその後も動き続ける秒数
DELAY_SECONDS = 2.0
LINGER_SECONDS = 6.0


@durable.step
def mark(key: str) -> str:
    with LOG.open("a") as f:
        f.write(f"{key} by {RUN_KEY}\n")
    return key


@durable.workflow
def queued(key: str) -> str:
    return mark(key)


def main() -> None:
    durable.launch(URL, RUN_KEY)
    target = durable.queue(f"q-{RUN_KEY}")
    if MODE == "delayed":
        durable.enqueue(target, queued, f"wf-{RUN_KEY}", RUN_KEY, delay_seconds=DELAY_SECONDS)
        os._exit(0)
    durable.enqueue(target, queued, f"wf-{RUN_KEY}", RUN_KEY)
    print(durable.result(f"wf-{RUN_KEY}"))
    time.sleep(DELAY_SECONDS + LINGER_SECONDS)
    durable.shutdown()


if __name__ == "__main__":
    main()
